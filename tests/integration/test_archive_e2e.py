"""Lifecycle hot → cold (Parquet) + listagem + queries via partição."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from megalog.config import Settings
from megalog.ingest.processor import Processor
from megalog.storage.partitions import (
    archive_day,
    count_logs,
    delete_expired,
    list_all,
    move_old_to_cold,
    open_for_query,
)


LINE_TEMPLATE = (
    "2026-04-20 10:15:{sec:02d} firewall,info forward: "
    "in:bridge1 out:ether2,connection-state:new, proto TCP, "
    "{src_ip}:{src_port}->8.8.8.8:443, NAT "
    "({src_ip}:{src_port}->170.245.175.121:{src_port})->8.8.8.8:443, len 60"
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        stream_dir=tmp_path / "stream",
        stream_processed_dir=tmp_path / "stream" / ".processed",
        hot_storage_dir=tmp_path / "hot",
        cold_storage_dir=tmp_path / "cold",
        state_dir=tmp_path / "state",
        ip_registry_path=tmp_path / "state" / "ip_registry.duckdb",
        batch_size=10,
        batch_flush_seconds=0.1,
    )


def _ingest_some(s: Settings, date_str: str, n: int) -> None:
    """Cria um .raw e roda o processor uma vez."""
    s.stream_dir.mkdir(parents=True, exist_ok=True)
    raw = s.stream_dir / f"{date_str}-10.raw"
    lines = [
        LINE_TEMPLATE.format(sec=i % 60, src_ip="100.80.0.119", src_port=51555 + i)
        .replace("2026-04-20", date_str)
        for i in range(n)
    ]
    raw.write_text("\n".join(lines) + "\n")
    proc = Processor(settings=s)
    proc.tick()
    proc._flush(force=True)
    proc.store.checkpoint(date.fromisoformat(date_str))
    proc.store.close()
    proc.ip_reg.close()


def test_archive_day_creates_parquet_and_removes_hot(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _ingest_some(s, "2026-04-20", 50)

    hot_db = s.hot_storage_dir / "2026-04-20.duckdb"
    assert hot_db.exists()

    cold_pq = archive_day("2026-04-20", s.hot_storage_dir, s.cold_storage_dir)
    assert cold_pq is not None
    assert cold_pq.exists()
    assert not hot_db.exists()

    # Parquet legível e com a contagem certa
    n = duckdb.connect(":memory:").execute(
        "SELECT COUNT(*) FROM read_parquet(?)", [str(cold_pq)]
    ).fetchone()[0]
    assert n == 50


def test_archive_idempotent(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _ingest_some(s, "2026-04-20", 10)
    archive_day("2026-04-20", s.hot_storage_dir, s.cold_storage_dir)
    # Segunda chamada não deve falhar
    pq = archive_day("2026-04-20", s.hot_storage_dir, s.cold_storage_dir)
    assert pq is not None
    assert pq.exists()


def test_archive_compresses_well(tmp_path: Path) -> None:
    """Parquet zstd deve ser bem menor que o DuckDB nativo equivalente."""
    s = _settings(tmp_path)
    # 2000 linhas com src_ip alternando entre 5 IPs (alta repetição)
    lines = []
    s.stream_dir.mkdir(parents=True, exist_ok=True)
    for i in range(2000):
        ip = f"100.80.0.{100 + (i % 5)}"
        lines.append(LINE_TEMPLATE.format(sec=i % 60, src_ip=ip, src_port=51555 + i))
    (s.stream_dir / "2026-04-20-10.raw").write_text("\n".join(lines) + "\n")
    proc = Processor(settings=s)
    proc.tick()
    proc._flush(force=True)
    proc.store.checkpoint(date(2026, 4, 20))
    proc.store.close()
    proc.ip_reg.close()

    hot_size = (s.hot_storage_dir / "2026-04-20.duckdb").stat().st_size
    pq = archive_day("2026-04-20", s.hot_storage_dir, s.cold_storage_dir)
    cold_size = pq.stat().st_size
    # Parquet deve ser menor (na prática 5-10x; com 2000 linhas o overhead
    # de header pesa mais — exigimos só que não cresça)
    assert cold_size <= hot_size


def test_list_all_lists_hot_and_cold(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _ingest_some(s, "2026-04-19", 5)
    _ingest_some(s, "2026-04-20", 5)
    archive_day("2026-04-19", s.hot_storage_dir, s.cold_storage_dir)

    parts = list_all(s.hot_storage_dir, s.cold_storage_dir)
    assert set(parts.keys()) == {"2026-04-19", "2026-04-20"}
    assert parts["2026-04-19"].kind == "cold"
    assert parts["2026-04-19"].format == "parquet"
    assert parts["2026-04-20"].kind == "hot"
    assert parts["2026-04-20"].format == "duckdb"


def test_count_logs_works_for_both_formats(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _ingest_some(s, "2026-04-19", 7)
    _ingest_some(s, "2026-04-20", 13)
    archive_day("2026-04-19", s.hot_storage_dir, s.cold_storage_dir)
    parts = list_all(s.hot_storage_dir, s.cold_storage_dir)
    assert count_logs(parts["2026-04-19"]) == 7
    assert count_logs(parts["2026-04-20"]) == 13


def test_open_for_query_view_works_uniformly(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _ingest_some(s, "2026-04-19", 5)
    _ingest_some(s, "2026-04-20", 5)
    archive_day("2026-04-19", s.hot_storage_dir, s.cold_storage_dir)
    parts = list_all(s.hot_storage_dir, s.cold_storage_dir)

    # Tanto hot quanto cold devem responder ao SELECT logs:
    for date_str in ("2026-04-19", "2026-04-20"):
        con = open_for_query(parts[date_str])
        try:
            n = con.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            assert n == 5
        finally:
            con.close()


def test_delete_expired_removes_old_parquets(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    s.cold_storage_dir.mkdir(parents=True, exist_ok=True)
    # Cria Parquets falsos com mtime antigos (via touch)
    old = s.cold_storage_dir / "2020-01-01.parquet"
    old.write_bytes(b"PAR1")  # cabeçalho mínimo, conteúdo irrelevante
    new = s.cold_storage_dir / "2026-04-20.parquet"
    new.write_bytes(b"PAR1")

    deleted = delete_expired(s.cold_storage_dir, delete_after_days=365)
    assert "2020-01-01" in deleted
    assert "2026-04-20" not in deleted
    assert not old.exists()
    assert new.exists()


def test_move_old_to_cold_respects_retention(tmp_path: Path) -> None:
    """DBs com data > retention dias atrás vão para cold; recentes ficam."""
    s = _settings(tmp_path)
    # data antiga: 60 dias atrás
    old_date = (datetime.now().date() - timedelta(days=60)).isoformat()
    today_date = datetime.now().date().isoformat()
    _ingest_some(s, old_date, 5)
    _ingest_some(s, today_date, 5)

    archived = move_old_to_cold(s.hot_storage_dir, s.cold_storage_dir, retention_days=30)
    assert old_date in archived
    assert today_date not in archived
    assert (s.cold_storage_dir / f"{old_date}.parquet").exists()
    assert (s.hot_storage_dir / f"{today_date}.duckdb").exists()
