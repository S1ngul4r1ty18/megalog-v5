"""Endpoints administrativos: users, audit, alerts."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from megalog.api.auth import hash_password
from megalog.api.deps import CurrentUser, get_ops_store, require_admin
from megalog.storage.operational import OperationalStore

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── Users ────────────────────────────────────────────────────────────────────


class CreateUserIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    role:     str = Field(default="user", pattern="^(admin|user)$")


class UpdateRoleIn(BaseModel):
    role: str = Field(pattern="^(admin|user)$")


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)


@router.get("/users")
def list_users(
    admin: CurrentUser = Depends(require_admin),
    ops: OperationalStore = Depends(get_ops_store),
):
    rows = ops.list_users()
    return [dict(r) for r in rows]


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    body: CreateUserIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    ops: OperationalStore = Depends(get_ops_store),
):
    if ops.get_user_by_username(body.username):
        raise HTTPException(status.HTTP_409_CONFLICT, "Usuário já existe")
    uid = ops.create_user(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    ops.log_audit(
        user_id=admin.id, username=admin.username,
        action="create_user", details=f"username={body.username} role={body.role}",
        ip_address=request.client.host if request.client else None,
    )
    return {"user_id": uid}


@router.delete("/users/{user_id}")
def deactivate_user(
    user_id: int,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    ops: OperationalStore = Depends(get_ops_store),
):
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Não pode desativar a si mesmo")
    if not ops.deactivate_user(user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    ops.log_audit(
        user_id=admin.id, username=admin.username,
        action="deactivate_user", details=f"target_user_id={user_id}",
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True}


@router.put("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    body: UpdateRoleIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    ops: OperationalStore = Depends(get_ops_store),
):
    if not ops.update_user_role(user_id, body.role):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    ops.log_audit(
        user_id=admin.id, username=admin.username,
        action="update_user_role",
        details=f"target_user_id={user_id} role={body.role}",
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True}


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    body: ResetPasswordIn,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    ops: OperationalStore = Depends(get_ops_store),
):
    if not ops.set_user_password(user_id, hash_password(body.new_password)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    ops.log_audit(
        user_id=admin.id, username=admin.username,
        action="reset_password", details=f"target_user_id={user_id}",
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True}


# ── Audit ────────────────────────────────────────────────────────────────────


@router.get("/audit")
def audit(
    limit:   int = 200,
    offset:  int = 0,
    user_id: int | None = None,
    action:  str | None = None,
    admin:   CurrentUser = Depends(require_admin),
    ops:     OperationalStore = Depends(get_ops_store),
):
    rows = ops.list_audit(limit=limit, offset=offset, user_id=user_id, action=action)
    return [dict(r) for r in rows]


# ── Alerts ────────────────────────────────────────────────────────────────────


@router.get("/alerts")
def list_alerts(
    only_unack: bool = False,
    admin: CurrentUser = Depends(require_admin),
    ops:   OperationalStore = Depends(get_ops_store),
):
    rows = ops.list_alerts(only_unacknowledged=only_unack)
    return [dict(r) for r in rows]


@router.get("/alerts/{alert_id}")
def get_alert(
    alert_id: int,
    admin: CurrentUser = Depends(require_admin),
    ops:   OperationalStore = Depends(get_ops_store),
):
    row = ops.get_alert(alert_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alerta não encontrado")
    return dict(row)


@router.post("/alerts/{alert_id}/ack")
def ack_alert(
    alert_id: int,
    request: Request,
    admin: CurrentUser = Depends(require_admin),
    ops:   OperationalStore = Depends(get_ops_store),
):
    if not ops.acknowledge_alert(alert_id, by_username=admin.username):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alerta inexistente ou já reconhecido")
    ops.log_audit(
        user_id=admin.id, username=admin.username,
        action="ack_alert", details=f"alert_id={alert_id}",
        ip_address=request.client.host if request.client else None,
    )
    return {"ok": True}
