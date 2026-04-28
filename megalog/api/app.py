"""FastAPI factory + bootstrap + servidor uvicorn.

Estado vivo (OperationalStore, IpRegistry) é colocado em `app.state`
para que dependências em deps.py possam acessar via Request.
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from megalog.api.auth import COOKIE_NAME, hash_password
from megalog.api.routes import admin as admin_routes
from megalog.api.routes import analytics as analytics_routes
from megalog.api.routes import auth as auth_routes
from megalog.api.routes import docs as docs_routes
from megalog.api.routes import search as search_routes
from megalog.api.routes import system as system_routes
from megalog.config import Settings, get_settings
from megalog.storage.ip_registry import IpRegistry
from megalog.storage.operational import OperationalStore

log = logging.getLogger("megalog.api")

CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def _csrf_origin_check(request: Request, call_next):
    """Defesa CSRF: requests mutadoras autenticadas precisam ter Origin
    ou Referer batendo com o Host. Requests sem cookie de sessão passam
    (não há nada a forjar). Endpoints anônimos como /login não são afetados.
    """
    if request.method in CSRF_SAFE_METHODS:
        return await call_next(request)
    if not request.cookies.get(COOKIE_NAME):
        return await call_next(request)

    host = request.headers.get("host", "").split(":")[0]
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")

    def _matches(url: str | None) -> bool:
        if not url:
            return False
        try:
            return urlparse(url).hostname == host
        except ValueError:
            return False

    if not (_matches(origin) or _matches(referer)):
        return JSONResponse(
            status_code=403,
            content={"detail": "Origin/Referer ausente ou divergente (CSRF)"},
        )
    return await call_next(request)


def _ensure_default_admin(ops: OperationalStore) -> None:
    if ops.user_count() > 0:
        return
    ops.create_user(
        username="admin",
        password_hash=hash_password("megalog123"),
        role="admin",
    )
    log.warning(
        "Usuário admin criado com senha padrão 'megalog123' — TROCAR no primeiro login!"
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    s: Settings = app.state.settings
    s.state_dir.mkdir(parents=True, exist_ok=True)

    ops = OperationalStore(s.state_dir / "megalog.db")
    _ensure_default_admin(ops)
    purged = ops.purge_expired_jti()
    if purged:
        log.info("Purgados %d JTIs expirados da blacklist", purged)
    app.state.ops_store = ops

    # Web é read-only no registry: o processor é o único writer (DuckDB exige
    # write-lock exclusivo). Lookups (filtro de busca, top-IPs) funcionam normal.
    app.state.ip_registry = IpRegistry(
        s.ip_registry_path,
        hot_top_n=s.ip_cache_hot_top_n,
        max_cache=s.ip_cache_max,
        read_only=True,
    )

    log.info("MegaLog API ready on %s:%d", s.web_host, s.web_port)
    yield

    app.state.ip_registry.close()


def _validate_secret_key(s: Settings) -> None:
    if s.secret_key == "change-me-via-env" or len(s.secret_key) < 32:
        raise RuntimeError(
            "MEGALOG_SECRET_KEY ausente, default ou curta demais (>=32 chars). "
            "Gere com: python -c 'import secrets; print(secrets.token_urlsafe(48))' "
            "e configure em /etc/megalog/megalog.env"
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    _validate_secret_key(s)
    app = FastAPI(
        title="MegaLog API",
        version=s.app_version,
        lifespan=_lifespan,
        # Swagger UI movido para /api/_swagger pra não colidir com /api/docs
        # (servindo a documentação Markdown do projeto)
        docs_url="/api/_swagger",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = s
    app.middleware("http")(_csrf_origin_check)
    app.include_router(auth_routes.router)
    app.include_router(search_routes.router)
    app.include_router(analytics_routes.router)
    app.include_router(system_routes.router)
    app.include_router(docs_routes.router)
    app.include_router(admin_routes.router)
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    s = get_settings()
    uvicorn.run(
        "megalog.api.app:create_app",
        factory=True,
        host=s.web_host,
        port=s.web_port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
