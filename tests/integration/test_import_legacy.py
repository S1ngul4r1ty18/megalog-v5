"""Testes da tool de importação contra DBs reais e sintéticos.

Cobre:
  - Schema v2 (real, /tmp/legacy/*.db) — caminho principal
  - Schema B sintético (criado em fixture)
  - Schema A sintético (IPs TEXT, ts TEXT)
  - Idempotência (rerun pula)
  - Detecção de schema
  - Detecção de data no nome
  - .db.gz (descompressão transparente)
"""
from __future__ import annotations

import gzip
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from megalog.config import Settings
from megalog.storage.ip_registry import IpRegistry, ip_to_int, int_to_ip
from megalog.storage.operational import OperationalStore
from megalog.tools.import_legacy import (
    detect_date,
    detect_schema,
    discover_files,
    import_file,
    open_legacy_sqlite,
)

LEGACY_DIR = Path("/tmp/legacy")


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        stream_dir=tmp_path / "stream",
        stream_processed_dir=tmp_path / "stream" / ".processed",
        hot_storage_dir=tmp_path / "hot",
        cold_storage_dir=tmp_path / "cold",
        state_dir=tmp_path / "state",
        ip_registry_path=tmp_path / "state" / "ip_registry.db",
    )


# ── Helpers de detecção ──────────────────────────────────────────────────────


def test_detect_date_from_filename() -> None:
    assert detect_date("2026-04-20.db") == "2026-04-20"
    assert detect_date("2026-04-20.db.gz") == "2026-04-20"
    assert detect_date("/path/to/2026-04-20.db") == "2026-04-20"
    with pytest.raises(ValueError):
        detect_date("sem-data.db")


def test_detect_schema_v2(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE ip_addresses (id INTEGER PRIMARY KEY, ip INTEGER UNIQUE);
        CREATE TABLE logs (id INTEGER PRIMARY KEY, ts INTEGER);
    """)
    con.close()
    con = sqlite3.connect(str(db))
    assert detect_schema(con) == "v2"
    con.close()


def test_detect_schema_b(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE d_interfaces (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE d_protocols  (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE logs (id INTEGER PRIMARY KEY, timestamp INTEGER);
    """)
    con.close()
    con = sqlite3.connect(str(db))
    assert detect_schema(con) == "B"
    con.close()


def test_detect_schema_c(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE interfaces  (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE protocols   (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE conn_states (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE db_meta     (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE logs (id INTEGER PRIMARY KEY, ts INTEGER);
    """)
    con.close()
    con = sqlite3.connect(str(db))
    assert detect_schema(con) == "C"
    con.close()


def test_detect_schema_a(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE logs (
            id INTEGER PRIMARY KEY, timestamp TEXT,
            interface_in TEXT, interface_out TEXT, proto TEXT,
            src_ip_priv TEXT, src_port_priv INTEGER,
            dst_ip TEXT, dst_port INTEGER,
            nat_ip_pub TEXT, nat_port_pub INTEGER
        );
    """)
    con.close()
    con = sqlite3.connect(str(db))
    assert detect_schema(con) == "A"
    con.close()


def test_open_legacy_sqlite_handles_gzip(tmp_path: Path) -> None:
    src = tmp_path / "2026-04-20.db"
    sqlite3.connect(str(src)).executescript("CREATE TABLE x (a INTEGER); INSERT INTO x VALUES (42);")
    gz = tmp_path / "2026-04-20.db.gz"
    with src.open("rb") as fi, gzip.open(gz, "wb") as fo:
        shutil.copyfileobj(fi, fo)
    src.unlink()

    with open_legacy_sqlite(gz) as (con, eff):
        assert eff.exists() and eff != gz
        assert con.execute("SELECT a FROM x").fetchone()[0] == 42
    # tmpdir limpo após context exit
    assert not eff.exists()


def test_discover_files_expands_dir(tmp_path: Path) -> None:
    (tmp_path / "2026-04-20.db").touch()
    (tmp_path / "2026-04-21.db.gz").touch()
    (tmp_path / "outro.txt").touch()
    files = discover_files([tmp_path])
    names = sorted(p.name for p in files)
    assert names == ["2026-04-20.db", "2026-04-21.db.gz"]


# ── Import schema B sintético ────────────────────────────────────────────────


def test_import_schema_b_synthetic(tmp_path: Path) -> None:
    src = tmp_path / "2026-04-20.db"
    con = sqlite3.connect(str(src))
    con.executescript("""
        CREATE TABLE d_interfaces (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE d_protocols  (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE d_states     (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE logs (
            id INTEGER PRIMARY KEY, timestamp INTEGER,
            interface_in_id INTEGER, interface_out_id INTEGER,
            protocol_id INTEGER, state_id INTEGER,
            src_ip_priv INTEGER, src_port_priv INTEGER,
            dst_ip INTEGER, dst_port INTEGER,
            nat_ip_pub INTEGER, nat_port_pub INTEGER
        );
        INSERT INTO d_interfaces VALUES (1,'ether1'),(2,'ether2');
        INSERT INTO d_protocols  VALUES (1,'TCP'),(2,'UDP');
        INSERT INTO d_states     VALUES (1,'new');
    """)
    src_ip_int = ip_to_int("100.80.0.119")
    dst_ip_int = ip_to_int("8.8.8.8")
    nat_ip_int = ip_to_int("170.245.175.121")
    for i in range(20):
        con.execute(
            "INSERT INTO logs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (None, 1776657600 + i, 1, 2, 1, 1,
             src_ip_int, 50000 + i, dst_ip_int, 443, nat_ip_int, 50000 + i),
        )
    con.commit(); con.close()

    s = _settings(tmp_path)
    s.cold_storage_dir.mkdir(parents=True)
    s.state_dir.mkdir(parents=True)
    reg = IpRegistry(s.ip_registry_path)
    ops = OperationalStore(s.state_dir / "megalog.db")
    result = import_file(src, settings=s, registry=reg, ops=ops)
    reg.close()

    assert result["schema"] == "B"
    assert result["rows"] == 20
    pq = Path(result["output"])
    assert pq.exists()

    # Lê de volta o Parquet e valida
    con = duckdb.connect(":memory:")
    rows = con.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT src_ip_id), COUNT(DISTINCT nat_ip_id) "
        f"FROM read_parquet('{pq}')"
    ).fetchone()
    assert rows == (20, 1, 1)
    con.close()

    # daily_stats foi populado
    assert s.cold_storage_dir / f"{result['date']}.parquet" == pq
    stats = ops.all_daily_stats()
    assert "2026-04-20" in stats
    assert stats["2026-04-20"][0] == 20


# ── Import schema C sintético ────────────────────────────────────────────────


def test_import_schema_c_synthetic(tmp_path: Path) -> None:
    src = tmp_path / "2026-04-20.db"
    con = sqlite3.connect(str(src))
    con.executescript("""
        CREATE TABLE interfaces  (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE protocols   (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE conn_states (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE db_meta     (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE logs (
            id INTEGER PRIMARY KEY, ts INTEGER NOT NULL,
            in_iface_id INTEGER, out_iface_id INTEGER, conn_mark TEXT,
            conn_state_id INTEGER, has_snat INTEGER DEFAULT 0,
            src_mac TEXT, proto_id INTEGER, tcp_flags TEXT,
            src_ip INTEGER NOT NULL DEFAULT 0, src_port INTEGER,
            dst_ip INTEGER NOT NULL DEFAULT 0, dst_port INTEGER,
            nat_ip INTEGER, nat_port INTEGER, pkt_len INTEGER,
            log_type TEXT DEFAULT 'nat', raw_msg TEXT
        );
        INSERT INTO interfaces  VALUES (1,'ether1'),(2,'ether2');
        INSERT INTO protocols   VALUES (1,'TCP'),(2,'UDP');
        INSERT INTO conn_states VALUES (1,'new');
        INSERT INTO db_meta     VALUES ('schema_origin','test');
    """)
    src_ip_int = ip_to_int("100.80.0.119")
    dst_ip_int = ip_to_int("8.8.8.8")
    nat_ip_int = ip_to_int("170.245.175.121")
    for i in range(20):
        con.execute(
            "INSERT INTO logs (ts,in_iface_id,out_iface_id,conn_state_id,has_snat,"
            "proto_id,src_ip,src_port,dst_ip,dst_port,nat_ip,nat_port,pkt_len,tcp_flags,log_type) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1776657600 + i, 1, 2, 1, 0, 1,
             src_ip_int, 50000 + i, dst_ip_int, 443, nat_ip_int, 50000 + i,
             60, 'SYN', 'nat'),
        )
    con.commit(); con.close()

    s = _settings(tmp_path)
    s.cold_storage_dir.mkdir(parents=True)
    s.state_dir.mkdir(parents=True)
    reg = IpRegistry(s.ip_registry_path)
    ops = OperationalStore(s.state_dir / "megalog.db")
    result = import_file(src, settings=s, registry=reg, ops=ops)
    reg.close()

    assert result["schema"] == "C"
    assert result["rows"] == 20
    pq = Path(result["output"])
    assert pq.exists()

    con = duckdb.connect(":memory:")
    rows = con.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT src_ip_id), COUNT(DISTINCT nat_ip_id) "
        f"FROM read_parquet('{pq}')"
    ).fetchone()
    assert rows == (20, 1, 1)
    con.close()


# ── Import schema A sintético ────────────────────────────────────────────────


def test_import_schema_a_synthetic(tmp_path: Path) -> None:
    src = tmp_path / "2026-04-20.db"
    con = sqlite3.connect(str(src))
    con.executescript("""
        CREATE TABLE logs (
            id INTEGER PRIMARY KEY, timestamp TEXT,
            interface_in TEXT, interface_out TEXT, proto TEXT,
            src_ip_priv TEXT, src_port_priv INTEGER,
            dst_ip TEXT, dst_port INTEGER,
            nat_ip_pub TEXT, nat_port_pub INTEGER
        );
    """)
    for i in range(15):
        con.execute(
            "INSERT INTO logs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (None, "2026-04-20 10:15:00", "ether1", "ether2", "TCP",
             "100.80.0.119", 50000 + i,
             "8.8.8.8", 443,
             "170.245.175.121", 50000 + i),
        )
    con.commit(); con.close()

    s = _settings(tmp_path)
    s.cold_storage_dir.mkdir(parents=True)
    s.state_dir.mkdir(parents=True)
    reg = IpRegistry(s.ip_registry_path)
    ops = OperationalStore(s.state_dir / "megalog.db")
    result = import_file(src, settings=s, registry=reg, ops=ops)
    reg.close()

    assert result["schema"] == "A"
    assert result["rows"] == 15

    # Confere que IPs foram registrados no global
    reg = IpRegistry(s.ip_registry_path)
    assert reg.get_id_no_create(ip_to_int("100.80.0.119")) is not None
    assert reg.get_id_no_create(ip_to_int("170.245.175.121")) is not None
    reg.close()


# ── Idempotência ─────────────────────────────────────────────────────────────


def test_import_skips_when_parquet_exists(tmp_path: Path) -> None:
    src = tmp_path / "2026-04-20.db"
    con = sqlite3.connect(str(src))
    con.executescript("""
        CREATE TABLE ip_addresses (id INTEGER PRIMARY KEY, ip INTEGER UNIQUE);
        CREATE TABLE logs (
            id INTEGER PRIMARY KEY, ts INTEGER,
            in_iface_id INTEGER, out_iface_id INTEGER, conn_mark TEXT,
            conn_state_id INTEGER, has_snat INTEGER, proto_id INTEGER,
            tcp_flags TEXT, src_ip_id INTEGER, src_port INTEGER,
            dst_ip_id INTEGER, dst_port INTEGER, nat_ip_id INTEGER,
            nat_port INTEGER, pkt_len INTEGER, log_type TEXT
        );
        INSERT INTO ip_addresses VALUES (1, 1685173367);
        INSERT INTO logs VALUES (1,1776657600,NULL,NULL,NULL,NULL,0,1,NULL,1,1234,1,443,1,1234,60,'nat');
    """)
    con.close()

    s = _settings(tmp_path)
    s.cold_storage_dir.mkdir(parents=True)
    s.state_dir.mkdir(parents=True)
    reg = IpRegistry(s.ip_registry_path)
    ops = OperationalStore(s.state_dir / "megalog.db")

    r1 = import_file(src, settings=s, registry=reg, ops=ops)
    assert "rows" in r1

    r2 = import_file(src, settings=s, registry=reg, ops=ops)
    assert r2.get("skipped") is True

    r3 = import_file(src, settings=s, registry=reg, ops=ops, overwrite=True)
    assert "rows" in r3 and r3.get("skipped") is None
    reg.close()


# ── Caso real: /tmp/legacy/*.db (skip se não disponível) ─────────────────────


@pytest.mark.skipif(
    not LEGACY_DIR.exists() or not list(LEGACY_DIR.glob("*.db")),
    reason="DBs reais não disponíveis em /tmp/legacy/",
)
def test_import_real_legacy_v2(tmp_path: Path) -> None:
    """Importa o menor DB real de /tmp/legacy/ e valida estrutura."""
    src = min(LEGACY_DIR.glob("*.db"), key=lambda p: p.stat().st_size)

    s = _settings(tmp_path)
    s.cold_storage_dir.mkdir(parents=True)
    s.state_dir.mkdir(parents=True)
    reg = IpRegistry(s.ip_registry_path)
    ops = OperationalStore(s.state_dir / "megalog.db")

    result = import_file(src, settings=s, registry=reg, ops=ops)
    reg.close()

    assert result["schema"] == "v2"
    assert result["rows"] > 1_000_000
    pq = Path(result["output"])
    assert pq.exists()

    # Confere compactação razoável (Parquet zstd << SQLite com índices)
    sqlite_size = src.stat().st_size
    parquet_size = pq.stat().st_size
    assert parquet_size < sqlite_size / 5, (
        f"Esperava compactação >5x, vi {sqlite_size/parquet_size:.1f}x"
    )

    # Sanity: lê de volta e checa contagem
    con = duckdb.connect(":memory:")
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{pq}')").fetchone()[0]
    assert n == result["rows"]
    con.close()
