"""Testes da tool import_raw — converte syslog texto direto para Parquet v5.

Cobre:
  - Detecção de data no nome
  - Idempotência (parquet existente é pulado, --overwrite força)
  - Formato D (receiver atual, `<PRI>Mon DD HH:MM:SS`)
  - Formato E (backups v4, ISO 8601 com `T`)
  - Sidecar dicts.json gerado
  - Linhas inválidas (não-NAT) viram contador de erros, não quebram run
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from megalog.config import Settings
from megalog.storage.ip_registry import IpRegistry, ip_to_int
from megalog.storage.operational import OperationalStore
from megalog.tools.import_raw import (
    detect_date,
    discover_files,
    import_raw_file,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        stream_dir=tmp_path / "stream",
        stream_processed_dir=tmp_path / "stream" / ".processed",
        hot_storage_dir=tmp_path / "hot",
        cold_storage_dir=tmp_path / "cold",
        state_dir=tmp_path / "state",
        ip_registry_path=tmp_path / "state" / "ip_registry.db",
    )


def test_detect_date_from_raw_filename() -> None:
    assert detect_date("2026-04-20-15.raw") == "2026-04-20"
    assert detect_date("2025-11-15-00.raw") == "2025-11-15"
    assert detect_date("/path/to/2026-04-20.raw") == "2026-04-20"
    with pytest.raises(ValueError):
        detect_date("sem-data.raw")


def test_discover_files_glob_raw_only(tmp_path: Path) -> None:
    (tmp_path / "2026-04-20-00.raw").touch()
    (tmp_path / "2026-04-21-01.raw").touch()
    (tmp_path / "lixo.txt").touch()
    (tmp_path / "outro.log").touch()
    files = discover_files([tmp_path])
    names = sorted(p.name for p in files)
    assert names == ["2026-04-20-00.raw", "2026-04-21-01.raw"]


def test_import_format_d_and_e_mixed(tmp_path: Path) -> None:
    """Um arquivo com linhas formato D (atual) e E (backup v4) — ambas viram parquet."""
    src = tmp_path / "2026-04-20-00.raw"
    lines = [
        # Formato D — receiver atual (RFC3164 com prioridade)
        "<30>Apr 20 10:15:32 RB5K9 LOG_NAT: forward: in:VLAN P2P out:ether2,"
        "connection-state:new,snat src-mac aa:bb:cc:dd:ee:ff, proto UDP, "
        "100.80.0.36:60095->192.168.150.100:61881, NAT "
        "(100.80.0.36:60095->170.245.175.121:60095)->192.168.150.100:61881, len 102",
        # Formato E — ISO 8601 com prefixo duplicado (backup v4)
        "2026-04-20T10:15:33-04:00 BORDA-5K9 LOG_NAT: LOG_NAT forward: "
        "in:VLAN P2P out:ether3, connection-state:new,snat src-mac aa:bb:cc:dd:ee:ff, "
        "proto TCP (SYN), 100.80.0.71:63846->8.8.8.8:443, NAT "
        "(100.80.0.71:63846->170.245.175.121:63846)->8.8.8.8:443, len 60",
        # Linha lixo — vai pra error_count
        "isso não é syslog",
    ]
    src.write_text("\n".join(lines) + "\n")

    s = _settings(tmp_path)
    s.cold_storage_dir.mkdir(parents=True)
    s.state_dir.mkdir(parents=True)

    r = import_raw_file(src, settings=s)
    assert r.get("rows") == 2, r
    assert r.get("errors") == 1
    pq = Path(r["output"])
    assert pq.exists()

    # Sidecar dicts criado
    sidecar = s.cold_storage_dir / f"{r['date']}.dicts.json"
    assert sidecar.exists()
    dicts = json.loads(sidecar.read_text())
    assert "interfaces" in dicts and "protocols" in dicts and "conn_states" in dicts

    # Lê parquet de volta e valida
    con = duckdb.connect(":memory:")
    rows = con.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT src_ip_id), COUNT(DISTINCT proto_id), "
        f"BOOL_OR(has_snat) FROM read_parquet('{pq}')"
    ).fetchone()
    assert rows[0] == 2          # 2 linhas
    assert rows[1] == 2          # 2 src IPs distintos
    assert rows[2] == 2          # TCP + UDP
    assert rows[3] is True       # has_snat
    con.close()

    # IPs foram registrados no registry global
    reg = IpRegistry(s.ip_registry_path)
    assert reg.get_id_no_create(ip_to_int("100.80.0.36")) is not None
    assert reg.get_id_no_create(ip_to_int("170.245.175.121")) is not None
    reg.close()


def test_import_skips_when_parquet_exists(tmp_path: Path) -> None:
    src = tmp_path / "2026-04-20-00.raw"
    src.write_text(
        "<30>Apr 20 10:15:32 RB5K9 LOG_NAT: forward: in:e1 out:e2,"
        "connection-state:new, proto UDP, 100.80.0.1:1->8.8.8.8:53, NAT "
        "(100.80.0.1:1->170.245.175.121:1)->8.8.8.8:53, len 60\n"
    )
    s = _settings(tmp_path)
    s.cold_storage_dir.mkdir(parents=True)
    s.state_dir.mkdir(parents=True)

    r1 = import_raw_file(src, settings=s)
    assert r1.get("rows") == 1

    r2 = import_raw_file(src, settings=s)
    assert r2.get("skipped") is True

    r3 = import_raw_file(src, settings=s, overwrite=True)
    assert r3.get("rows") == 1 and r3.get("skipped") is None


def test_import_empty_file_warns_no_writes_parquet(tmp_path: Path) -> None:
    """Arquivo só com lixo: rows=0, NÃO escreve parquet."""
    src = tmp_path / "2026-04-20-00.raw"
    src.write_text("isso não é syslog\noutra linha lixo\n")
    s = _settings(tmp_path)
    s.cold_storage_dir.mkdir(parents=True)
    s.state_dir.mkdir(parents=True)

    r = import_raw_file(src, settings=s)
    assert r.get("rows") == 0
    assert r.get("errors") == 2
    assert "warning" in r
    assert not (s.cold_storage_dir / f"{r['date']}.parquet").exists()


def test_import_updates_daily_stats_via_run(tmp_path: Path) -> None:
    """run() chama upsert_daily_stats para cada arquivo OK."""
    from megalog.tools.import_raw import run

    src = tmp_path / "2026-04-20-00.raw"
    src.write_text(
        "<30>Apr 20 10:15:32 RB5K9 LOG_NAT: forward: in:e1 out:e2,"
        "connection-state:new, proto UDP, 100.80.0.1:1->8.8.8.8:53, NAT "
        "(100.80.0.1:1->170.245.175.121:1)->8.8.8.8:53, len 60\n"
    )

    # Aponta config para tmp_path via monkeypatch do get_settings
    s = _settings(tmp_path)
    import megalog.tools.import_raw as mod
    original = mod.get_settings
    mod.get_settings = lambda: s
    try:
        n = run([src])
        assert n == 1
    finally:
        mod.get_settings = original

    ops = OperationalStore(s.state_dir / "megalog.db")
    stats = ops.all_daily_stats()
    assert "2026-04-20" in stats
    assert stats["2026-04-20"][0] == 1
