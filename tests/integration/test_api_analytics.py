"""Endpoints /api/analytics/{daily,top-ips,calendar}."""
from __future__ import annotations

from megalog.storage.operational import OperationalStore


def test_daily_requires_auth(client):
    r = client.get("/api/analytics/daily")
    assert r.status_code == 401


def test_daily_returns_empty_when_no_data(admin_client):
    r = admin_client.get("/api/analytics/daily")
    assert r.status_code == 200
    assert r.json() == []


def test_daily_lists_partitions_after_archive(
    admin_client, api_settings, populate_partition
):
    """daily reflete o que `archive` job populou em daily_stats."""
    populate_partition(api_settings, "2026-04-19", n=10)
    populate_partition(api_settings, "2026-04-20", n=20)

    # Roda o job archive (sem mover, só atualiza daily_stats — todas as datas
    # são recentes e ficam em hot)
    from megalog.jobs.archive import run as run_archive
    import megalog.jobs.archive as ja
    # patch do get_settings dentro do job
    ja.get_settings = lambda: api_settings  # type: ignore[assignment]
    run_archive()

    r = admin_client.get("/api/analytics/daily")
    assert r.status_code == 200
    body = r.json()
    by_date = {row["date"]: row for row in body}
    assert by_date["2026-04-19"]["log_count"] == 10
    assert by_date["2026-04-20"]["log_count"] == 20
    assert by_date["2026-04-19"]["kind"] == "hot"


def test_top_ips_returns_global_registry(
    admin_client, api_settings, populate_partition
):
    populate_partition(api_settings, "2026-04-20", n=10)
    r = admin_client.get("/api/analytics/top-ips")
    assert r.status_code == 200
    body = r.json()
    # Tem que conter pelo menos 100.80.0.119 (src), 8.8.8.8 (dst), 170.245.175.121 (nat)
    ips = {row["ip"] for row in body}
    assert "100.80.0.119" in ips
    assert "8.8.8.8" in ips
    assert "170.245.175.121" in ips


def test_top_ips_limit_param_respected(
    admin_client, api_settings, populate_partition
):
    populate_partition(api_settings, "2026-04-20", n=5)
    r = admin_client.get("/api/analytics/top-ips?limit=2")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_calendar_returns_dict(admin_client, api_settings, populate_partition):
    populate_partition(api_settings, "2026-04-20", n=5)
    # popula daily_stats
    from megalog.jobs.archive import run as run_archive
    import megalog.jobs.archive as ja
    ja.get_settings = lambda: api_settings  # type: ignore[assignment]
    run_archive()

    r = admin_client.get("/api/analytics/calendar")
    assert r.status_code == 200
    body = r.json()
    assert "2026-04-20" in body
    assert body["2026-04-20"]["log_count"] == 5
    assert body["2026-04-20"]["alert"] is False
