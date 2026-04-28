"""Endpoints /api/admin/* — users, audit, alerts."""
from __future__ import annotations


def test_admin_users_requires_admin(regular_user_client):
    r = regular_user_client.get("/api/admin/users")
    assert r.status_code == 403


def test_admin_users_lists_default_admin(admin_client):
    r = admin_client.get("/api/admin/users")
    assert r.status_code == 200
    body = r.json()
    usernames = [u["username"] for u in body]
    assert "admin" in usernames


def test_admin_create_user(admin_client):
    r = admin_client.post(
        "/api/admin/users",
        json={"username": "bob", "password": "bob-pwd-12345", "role": "user"},
    )
    assert r.status_code == 201
    uid = r.json()["user_id"]
    assert uid > 0

    # Listagem inclui novo usuário
    r2 = admin_client.get("/api/admin/users")
    assert "bob" in {u["username"] for u in r2.json()}


def test_admin_create_duplicate_fails_409(admin_client):
    admin_client.post(
        "/api/admin/users",
        json={"username": "carol", "password": "carol-pwd-12345", "role": "user"},
    )
    r = admin_client.post(
        "/api/admin/users",
        json={"username": "carol", "password": "outra-senha-12345", "role": "user"},
    )
    assert r.status_code == 409


def test_admin_cannot_deactivate_self(admin_client):
    me = admin_client.get("/api/auth/me").json()
    r = admin_client.delete(f"/api/admin/users/{me['user_id']}")
    assert r.status_code == 400


def test_admin_deactivate_other_user(admin_client):
    r = admin_client.post(
        "/api/admin/users",
        json={"username": "dave", "password": "dave-pwd-12345", "role": "user"},
    )
    uid = r.json()["user_id"]
    r2 = admin_client.delete(f"/api/admin/users/{uid}")
    assert r2.status_code == 200


def test_admin_update_role(admin_client):
    r = admin_client.post(
        "/api/admin/users",
        json={"username": "eve", "password": "eve-pwd-12345", "role": "user"},
    )
    uid = r.json()["user_id"]
    r2 = admin_client.put(f"/api/admin/users/{uid}/role", json={"role": "admin"})
    assert r2.status_code == 200


def test_admin_reset_password(admin_client):
    r = admin_client.post(
        "/api/admin/users",
        json={"username": "frank", "password": "frank-pwd-12345", "role": "user"},
    )
    uid = r.json()["user_id"]
    r2 = admin_client.post(
        f"/api/admin/users/{uid}/reset-password",
        json={"new_password": "outra-senha-segura-2026"},
    )
    assert r2.status_code == 200


def test_admin_audit_lists_events(admin_client):
    """Login do admin já gerou pelo menos 1 entrada."""
    r = admin_client.get("/api/admin/audit")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    actions = {r["action"] for r in rows}
    assert "login" in actions


def test_admin_alerts_empty_initially(admin_client):
    r = admin_client.get("/api/admin/alerts")
    assert r.status_code == 200
    assert r.json() == []


def test_admin_ack_alert_e2e(admin_client, api_settings):
    """Insere alerta direto e reconhece via API."""
    from megalog.storage.operational import OperationalStore

    ops = OperationalStore(api_settings.state_dir / "megalog.db")
    aid = ops.insert_alert(
        date_str="2026-04-20",
        log_count=5_000_000, db_size_bytes=10_000_000,
        expected_count=1_000_000, ratio=5.0,
        analysis="teste", details_json="{}",
        classification="P2P/Torrent",
    )

    r = admin_client.get(f"/api/admin/alerts/{aid}")
    assert r.status_code == 200
    assert r.json()["acknowledged"] == 0

    r2 = admin_client.post(f"/api/admin/alerts/{aid}/ack")
    assert r2.status_code == 200

    # Segunda chamada falha (já reconhecido)
    r3 = admin_client.post(f"/api/admin/alerts/{aid}/ack")
    assert r3.status_code == 404


def test_healthz_no_auth(client):
    r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_system_status_requires_auth(client):
    r = client.get("/api/system-status")
    assert r.status_code == 401


def test_system_status_returns_metrics(admin_client):
    r = admin_client.get("/api/system-status")
    assert r.status_code == 200
    body = r.json()
    assert "cpu" in body
    assert "ram" in body
    assert "disks" in body
    assert "services" in body
    assert "avg_pct" in body["cpu"]
    assert "used_pct" in body["ram"]
