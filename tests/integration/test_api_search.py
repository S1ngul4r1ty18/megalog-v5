"""Endpoints /api/search e /api/search/export."""
from __future__ import annotations


def test_search_requires_auth(client):
    r = client.post("/api/search", json={"date": "2026-04-20"})
    assert r.status_code == 401


def test_search_404_when_no_partition(admin_client):
    r = admin_client.post("/api/search", json={"date": "2099-01-01"})
    assert r.status_code == 404


def test_search_returns_paginated_results(admin_client, api_settings, populate_partition):
    populate_partition(api_settings, "2026-04-20", n=120)

    r = admin_client.post(
        "/api/search",
        json={"date": "2026-04-20", "page": 1, "per_page": 50},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 120
    assert len(body["rows"]) == 50
    # Linhas têm strings IP enriquecidas
    assert body["rows"][0]["src_ip"] == "100.80.0.119"
    assert body["rows"][0]["nat_ip"] == "170.245.175.121"
    assert body["rows"][0]["proto"] == "TCP"


def test_search_filters_by_src_ip(admin_client, api_settings, populate_partition):
    populate_partition(api_settings, "2026-04-20", n=30)
    r = admin_client.post(
        "/api/search",
        json={"date": "2026-04-20", "src_ip": "100.80.0.119"},
    )
    assert r.status_code == 200
    assert r.json()["total"] == 30


def test_search_unknown_ip_returns_zero(admin_client, api_settings, populate_partition):
    populate_partition(api_settings, "2026-04-20", n=5)
    r = admin_client.post(
        "/api/search",
        json={"date": "2026-04-20", "src_ip": "203.0.113.99"},
    )
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_search_filters_by_dst_port(admin_client, api_settings, populate_partition):
    populate_partition(api_settings, "2026-04-20", n=10)
    r = admin_client.post(
        "/api/search",
        json={"date": "2026-04-20", "dst_port": 443},
    )
    assert r.status_code == 200
    assert r.json()["total"] == 10
    r2 = admin_client.post(
        "/api/search",
        json={"date": "2026-04-20", "dst_port": 9999},
    )
    assert r2.json()["total"] == 0


def test_search_invalid_ip_returns_422(admin_client):
    r = admin_client.post(
        "/api/search",
        json={"date": "2026-04-20", "src_ip": "isso.nao.eh.ip"},
    )
    assert r.status_code == 422


def test_search_invalid_proto_returns_422(admin_client):
    r = admin_client.post(
        "/api/search",
        json={"date": "2026-04-20", "proto": "ICMP"},
    )
    assert r.status_code == 422


def test_export_csv_returns_attachment(admin_client, api_settings, populate_partition):
    populate_partition(api_settings, "2026-04-20", n=5)
    r = admin_client.post(
        "/api/search/export",
        json={"date": "2026-04-20"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    text = r.text
    assert text.startswith("ts_iso,ts,")
    # 5 linhas + header
    assert text.count("\n") == 6


def test_search_logs_audit_entry(admin_client, api_settings, populate_partition):
    """Cada busca cria entrada em audit_log."""
    from megalog.storage.operational import OperationalStore
    populate_partition(api_settings, "2026-04-20", n=3)
    admin_client.post("/api/search", json={"date": "2026-04-20"})
    ops = OperationalStore(api_settings.state_dir / "megalog.db")
    rows = ops.list_audit(action="search")
    assert len(rows) >= 1
