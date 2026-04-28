"""Smoke E2E para jobs CLI: retention e analyze."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from megalog.config import Settings
from megalog.jobs import analyze as analyze_job
from megalog.jobs import retention as retention_job
from megalog.storage.operational import OperationalStore


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        stream_dir=tmp_path / "stream",
        stream_processed_dir=tmp_path / "stream" / ".processed",
        hot_storage_dir=tmp_path / "hot",
        cold_storage_dir=tmp_path / "cold",
        state_dir=tmp_path / "state",
        ip_registry_path=tmp_path / "state" / "ip_registry.db",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def patched_settings(tmp_path, monkeypatch):
    """Substitui get_settings() pelos jobs por uma instância isolada via tmp_path."""
    s = _settings(tmp_path, delete_after_days=30)
    s.cold_storage_dir.mkdir(parents=True)
    s.state_dir.mkdir(parents=True)
    s.hot_storage_dir.mkdir(parents=True)
    monkeypatch.setattr(retention_job, "get_settings", lambda: s)
    monkeypatch.setattr(analyze_job, "get_settings", lambda: s)
    return s


# ── retention ────────────────────────────────────────────────────────────────


def test_retention_disabled_returns_zero(patched_settings, monkeypatch):
    monkeypatch.setattr(retention_job, "get_settings",
                        lambda: _settings(patched_settings.state_dir.parent, delete_after_days=0))
    assert retention_job.run() == 0


def test_retention_deletes_only_old_partitions(patched_settings):
    cold = patched_settings.cold_storage_dir
    today = datetime.now().date()
    new_date = (today - timedelta(days=10)).isoformat()
    old_date = (today - timedelta(days=60)).isoformat()
    (cold / f"{new_date}.parquet").write_bytes(b"x")
    (cold / f"{old_date}.parquet").write_bytes(b"x")

    n = retention_job.run()
    assert n == 1
    assert (cold / f"{new_date}.parquet").exists()
    assert not (cold / f"{old_date}.parquet").exists()


# ── analyze ──────────────────────────────────────────────────────────────────


def _make_partition(hot_dir: Path, date_str: str, rows: int) -> None:
    hot_dir.mkdir(parents=True, exist_ok=True)
    db = hot_dir / f"{date_str}.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""
        CREATE TABLE logs (
            ts BIGINT, in_iface_id INT, out_iface_id INT, proto_id INT,
            conn_state_id INT, has_snat BOOLEAN, src_ip_id BIGINT, src_port INT,
            dst_ip_id BIGINT, dst_port INT, nat_ip_id BIGINT, nat_port INT,
            pkt_len INT, tcp_flags INT, log_type TEXT
        )
    """)
    con.execute("CREATE TABLE interfaces (id INT, name TEXT)")
    con.execute("CREATE TABLE protocols (id INT, name TEXT)")
    con.execute("CREATE TABLE conn_states (id INT, name TEXT)")
    con.execute("INSERT INTO interfaces VALUES (1,'bridge1'),(2,'ether2')")
    con.execute("INSERT INTO protocols VALUES (6,'TCP'),(17,'UDP')")
    con.execute("INSERT INTO conn_states VALUES (1,'new')")
    base_ts = int(datetime.fromisoformat(date_str).timestamp())
    con.executemany(
        "INSERT INTO logs VALUES (?,1,2,6,1,true,1,40000,2,443,3,40000,60,0,'forward')",
        [(base_ts + i,) for i in range(rows)],
    )
    con.close()


def test_analyze_run_with_no_candidates(patched_settings):
    ops = OperationalStore(patched_settings.state_dir / "megalog.db")
    today = datetime.now().date()
    for i in range(7):
        d = (today - timedelta(days=i + 1)).isoformat()
        ops.upsert_daily_stats(d, log_count=10_000, db_size=1_000_000)

    assert analyze_job.run() == 0


def test_analyze_run_with_only_date_creates_alert(patched_settings):
    ops = OperationalStore(patched_settings.state_dir / "megalog.db")
    target = "2026-04-15"
    ops.upsert_daily_stats(target, log_count=5_000, db_size=500_000)
    _make_partition(patched_settings.hot_storage_dir, target, rows=5_000)

    n = analyze_job.run(only_date=target, force=True)
    assert n == 1
    assert ops.alert_exists(target)


def test_analyze_run_unknown_date_returns_zero(patched_settings):
    OperationalStore(patched_settings.state_dir / "megalog.db")
    assert analyze_job.run(only_date="2025-01-01") == 0
