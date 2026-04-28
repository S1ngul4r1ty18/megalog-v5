from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from megalog.api.app import create_app
from megalog.api.auth import hash_password
from megalog.config import Settings
from megalog.ingest.processor import Processor
from megalog.storage.operational import OperationalStore


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── Test infra para a API ────────────────────────────────────────────────────


def _build_settings(tmp_path: Path) -> Settings:
    return Settings(
        stream_dir=tmp_path / "stream",
        stream_processed_dir=tmp_path / "stream" / ".processed",
        hot_storage_dir=tmp_path / "hot",
        cold_storage_dir=tmp_path / "cold",
        state_dir=tmp_path / "state",
        ip_registry_path=tmp_path / "state" / "ip_registry.duckdb",
        secret_key="test-secret-key-for-jwt-signing-only",
        session_timeout_minutes=15,
        batch_size=10,
        batch_flush_seconds=0.1,
    )


@pytest.fixture
def api_settings(tmp_path) -> Settings:
    return _build_settings(tmp_path)


@pytest.fixture
def client(api_settings) -> TestClient:
    """TestClient que respeita o lifespan (cria admin default, abre registry).

    O middleware CSRF rejeita POST/PUT/DELETE sem Origin batendo com Host.
    TestClient usa http://testserver por padrão; setamos Origin coincidente.
    """
    app = create_app(settings=api_settings)
    with TestClient(app, headers={"Origin": "http://testserver"}) as c:
        yield c


@pytest.fixture
def admin_client(client) -> TestClient:
    """TestClient já autenticado como admin (cookie persistido pelo TestClient)."""
    r = client.post("/api/auth/login", json={"username": "admin", "password": "megalog123"})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
def regular_user_client(client, api_settings) -> TestClient:
    """Cria um usuário 'user' não-admin e loga."""
    ops = OperationalStore(api_settings.state_dir / "megalog.db")
    ops.create_user(
        username="alice",
        password_hash=hash_password("alice-password-123"),
        role="user",
    )
    # cliente novo (sem cookies do admin)
    app = client.app
    new_client = TestClient(app, headers={"Origin": "http://testserver"})
    r = new_client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-password-123"}
    )
    assert r.status_code == 200, r.text
    return new_client


# Linha de log padrão para popular partições nos testes da API
_LINE_TEMPLATE = (
    "2026-04-20 10:15:{sec:02d} firewall,info forward: "
    "in:bridge1 out:ether2,connection-state:new, proto TCP, "
    "{src_ip}:{src_port}->8.8.8.8:443, NAT "
    "({src_ip}:{src_port}->170.245.175.121:{src_port})->8.8.8.8:443, len 60"
)


@pytest.fixture
def populate_partition():
    """Factory: cria N linhas para uma data via o processor real.

    Registry é SQLite WAL — leitor (web) e escritor (processor) podem
    coexistir no mesmo processo sem o dance de fechar/reabrir conexão.
    """
    def _populate(settings: Settings, date_str: str, n: int = 50) -> None:
        settings.stream_dir.mkdir(parents=True, exist_ok=True)
        raw = settings.stream_dir / f"{date_str}-10.raw"
        lines = [
            _LINE_TEMPLATE.format(sec=i % 60, src_ip="100.80.0.119", src_port=51555 + i)
            .replace("2026-04-20", date_str)
            for i in range(n)
        ]
        raw.write_text("\n".join(lines) + "\n")
        proc = Processor(settings=settings)
        proc.tick()
        proc._flush(force=True)
        proc.store.checkpoint(date.fromisoformat(date_str))
        proc.store.close()
        proc.ip_reg.close()
    return _populate
