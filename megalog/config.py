"""Configuração central — Pydantic Settings, lida de variáveis de ambiente e .env."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MEGALOG_",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Identidade ────────────────────────────────────────────────────────────
    app_name: str = "MegaLog"
    app_version: str = "5.0.0a1"

    # ── Receptor UDP ──────────────────────────────────────────────────────────
    receiver_host: str = "0.0.0.0"
    receiver_port: int = 514
    receiver_socket_buffer_bytes: int = 16 * 1024 * 1024
    receiver_workers: int = 1  # SO_REUSEPORT permite N processos no mesmo port

    # ── Stream buffer ─────────────────────────────────────────────────────────
    stream_dir: Path = Path("/dados1/stream")
    stream_processed_dir: Path = Path("/dados1/stream/.processed")
    stream_processed_keep_hours: int = 24
    stream_flush_every_n_packets: int = 1000

    # ── Storage ───────────────────────────────────────────────────────────────
    hot_storage_dir: Path = Path("/dados1/hot")
    cold_storage_dir: Path = Path("/dados2/cold")
    state_dir: Path = Path("/dados1/state")
    ip_registry_path: Path = Path("/dados1/state/ip_registry.db")

    # ── Processor ─────────────────────────────────────────────────────────────
    batch_size: int = 5000  # 10x maior que v4 (DuckDB amortiza melhor)
    batch_flush_seconds: float = 2.0
    tail_sleep_seconds: float = 0.05
    # DuckDB tem lock exclusivo: o processor fecha a conexão a cada N segundos
    # para abrir janela de leitura para a API web. Quanto menor, mais "fresh"
    # a busca em logs do dia atual; quanto maior, menos overhead de reconexão.
    duckdb_close_interval_seconds: float = 3.0
    ip_cache_hot_top_n: int = 50_000  # top-N IPs pré-carregados em memória
    ip_cache_max: int = 200_000  # tamanho máximo do LRU em memória
    duckdb_threads: int = 2

    # ── Retenção ──────────────────────────────────────────────────────────────
    hot_retention_days: int = 30
    delete_after_days: int = 365  # 0 = nunca

    # ── Web (Fase 3) ──────────────────────────────────────────────────────────
    web_host: str = "0.0.0.0"
    web_port: int = 5000
    secret_key: str = "change-me-via-env"
    session_timeout_minutes: int = 60

    # ── Tempo ─────────────────────────────────────────────────────────────────
    timezone: str = "America/Sao_Paulo"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Singleton para uso em jobs e CLIs (evita reler env várias vezes)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
