"""Endpoints de autenticação: login, logout, me, change-password."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from megalog.api.auth import (
    COOKIE_NAME,
    decode_token,
    hash_password,
    issue_token,
    verify_password,
)
from megalog.api.deps import (
    CurrentUser,
    get_current_user,
    get_ops_store,
    get_settings_dep,
)
from megalog.config import Settings
from megalog.storage.operational import OperationalStore

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginOut(BaseModel):
    user_id: int
    username: str
    role: str


class MeOut(BaseModel):
    user_id: int
    username: str
    role: str


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=256)


def _set_session_cookie(response: Response, token: str, ttl_minutes: int) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=ttl_minutes * 60,
        httponly=True,
        samesite="strict",
        secure=False,  # ativar em produção atrás de TLS
        path="/",
    )


@router.post("/login", response_model=LoginOut)
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings_dep),
    ops: OperationalStore = Depends(get_ops_store),
):
    user = ops.get_user_by_username(body.username)
    client_ip = request.client.host if request.client else None
    if not user:
        ops.log_audit(
            user_id=None, username=body.username,
            action="login_failed", details="usuário não encontrado",
            ip_address=client_ip,
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciais inválidas")

    valid, needs_rehash = verify_password(user["password_hash"], body.password)
    if not valid:
        ops.log_audit(
            user_id=user["id"], username=user["username"],
            action="login_failed", details="senha incorreta",
            ip_address=client_ip,
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciais inválidas")

    if needs_rehash:
        ops.set_user_password(user["id"], hash_password(body.password))

    ops.touch_last_login(user["id"])
    ops.log_audit(
        user_id=user["id"], username=user["username"],
        action="login", ip_address=client_ip,
    )

    token = issue_token(
        user_id=user["id"],
        username=user["username"],
        role=user["role"],
        secret=settings.secret_key,
        ttl_minutes=settings.session_timeout_minutes,
    )
    _set_session_cookie(response, token, settings.session_timeout_minutes)

    return LoginOut(user_id=user["id"], username=user["username"], role=user["role"])


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
    ops: OperationalStore = Depends(get_ops_store),
):
    # Revoga o JTI do cookie atual até o exp original (impede reuso do token)
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        payload = decode_token(cookie, secret=settings.secret_key)
        if payload and (jti := payload.get("jti")):
            ops.revoke_jti(jti, int(payload["exp"]))
    response.delete_cookie(COOKIE_NAME, path="/")
    ops.log_audit(
        user_id=user.id, username=user.username, action="logout",
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True}


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser = Depends(get_current_user)):
    return MeOut(user_id=user.id, username=user.username, role=user.role)


@router.post("/change-password")
def change_password(
    body: ChangePasswordIn,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    ops: OperationalStore = Depends(get_ops_store),
):
    db_user = ops.get_user(user.id)
    if not db_user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    valid, _ = verify_password(db_user["password_hash"], body.current_password)
    if not valid:
        ops.log_audit(
            user_id=user.id, username=user.username,
            action="change_password_failed",
            ip_address=request.client.host if request.client else None,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Senha atual incorreta")
    ops.set_user_password(user.id, hash_password(body.new_password))
    ops.log_audit(
        user_id=user.id, username=user.username, action="change_password",
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True}
