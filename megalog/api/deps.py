"""Dependency injection: providers de Settings/OperationalStore/IpRegistry/current_user.

FastAPI usa Depends() para injetar essas funções em cada handler.
Estado vivo (registries, conexões) é guardado em `app.state` no factory.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Cookie, Depends, HTTPException, Request, status

from megalog.api.auth import COOKIE_NAME, decode_token
from megalog.config import Settings
from megalog.storage.ip_registry import IpRegistry
from megalog.storage.operational import OperationalStore


@dataclass(slots=True, frozen=True)
class CurrentUser:
    id: int
    username: str
    role: str

    def is_admin(self) -> bool:
        return self.role == "admin"


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_ops_store(request: Request) -> OperationalStore:
    return request.app.state.ops_store


def get_ip_registry(request: Request) -> IpRegistry:
    return request.app.state.ip_registry


def get_current_user(
    settings: Settings = Depends(get_settings_dep),
    ops: OperationalStore = Depends(get_ops_store),
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> CurrentUser:
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão ausente",
        )
    payload = decode_token(session, secret=settings.secret_key)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão inválida ou expirada",
        )
    jti = payload.get("jti")
    if jti and ops.is_jti_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão revogada",
        )
    return CurrentUser(
        id=int(payload["sub"]),
        username=payload["username"],
        role=payload.get("role", "user"),
    )


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a administradores",
        )
    return user
