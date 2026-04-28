"""Endpoints /api/auth/* — login, logout, me, change-password."""
from __future__ import annotations


def test_login_success_sets_cookie(client):
    r = client.post(
        "/api/auth/login", json={"username": "admin", "password": "megalog123"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin"
    assert body["role"] == "admin"
    assert "megalog_session" in r.cookies


def test_login_wrong_password_returns_401(client):
    r = client.post(
        "/api/auth/login", json={"username": "admin", "password": "errado"}
    )
    assert r.status_code == 401


def test_login_unknown_user_returns_401(client):
    r = client.post(
        "/api/auth/login", json={"username": "nao-existe", "password": "x"}
    )
    assert r.status_code == 401


def test_me_requires_session(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_returns_current_user(admin_client):
    r = admin_client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["username"] == "admin"


def test_logout_clears_cookie(admin_client):
    r = admin_client.post("/api/auth/logout")
    assert r.status_code == 200
    # Após logout, /me deve falhar
    r2 = admin_client.get("/api/auth/me")
    assert r2.status_code == 401


def test_change_password_requires_current(admin_client):
    r = admin_client.post(
        "/api/auth/change-password",
        json={"current_password": "errado", "new_password": "novasenha-12345"},
    )
    assert r.status_code == 403


def test_change_password_works(admin_client):
    r = admin_client.post(
        "/api/auth/change-password",
        json={"current_password": "megalog123", "new_password": "nova-senha-segura-2026"},
    )
    assert r.status_code == 200


def test_session_token_invalid_after_secret_change(api_settings, client):
    # Cookie emitido com secret_key="test-secret-..."; simular validação com outro
    client.post("/api/auth/login", json={"username": "admin", "password": "megalog123"})
    # Forçar cookie inválido
    client.cookies.set("megalog_session", "garbage.token.value")
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_legacy_v4_password_is_accepted_and_rehashed(client, api_settings):
    """Senha em formato v4 ('hex(salt)$hex(sha256(salt+pass))') deve funcionar
    e ser silenciosamente migrada para Argon2 no primeiro login bem-sucedido."""
    import hashlib
    import secrets

    from megalog.storage.operational import OperationalStore

    ops = OperationalStore(api_settings.state_dir / "megalog.db")
    salt = secrets.token_bytes(16)
    plain = "senha-legada-v4"
    legacy_hash = salt.hex() + "$" + hashlib.sha256(salt + plain.encode()).hexdigest()
    ops.create_user(username="legacy", password_hash=legacy_hash, role="user")

    r = client.post(
        "/api/auth/login", json={"username": "legacy", "password": plain}
    )
    assert r.status_code == 200

    # Após login, hash no banco deve ter sido convertido para Argon2
    user = ops.get_user_by_username("legacy")
    assert user["password_hash"].startswith("$argon2")
