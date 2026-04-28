"""Endpoints de sistema: status (CPU/RAM/disco/serviços) + healthz."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Response

from megalog.api.deps import CurrentUser, get_current_user, get_settings_dep
from megalog.api.system import collect_status
from megalog.config import Settings

router = APIRouter(prefix="/api", tags=["system"])

# Thresholds: 503 se algum disparar.
DISK_HOT_COLD_MAX_PCT = 95.0
DISK_STATE_MAX_PCT = 90.0
INGEST_STALE_SECONDS = 300  # 5 min sem packets é suspeito (operação normal: 10+ pkt/s)
IP_REGISTRY_MAX_BYTES = 5 * 1024**3  # 5 GB


def _check_health(status: dict, settings: Settings) -> list[str]:
    issues: list[str] = []

    for name in ("hot", "cold"):
        d = status["disks"].get(name) or {}
        pct = d.get("used_pct") or 0
        if pct > DISK_HOT_COLD_MAX_PCT:
            issues.append(f"disk_{name}_full ({pct:.1f}%)")
    state_d = status["disks"].get("state") or {}
    state_pct = state_d.get("used_pct") or 0
    if state_pct > DISK_STATE_MAX_PCT:
        issues.append(f"disk_state_full ({state_pct:.1f}%)")

    for svc in ("receiver", "processor", "web"):
        if status["services"][svc] != "active":
            issues.append(f"service_{svc}_not_active")

    last = status["ingest"].get("raw_last_seen")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if datetime.now() - last_dt > timedelta(seconds=INGEST_STALE_SECONDS):
                age = (datetime.now() - last_dt).total_seconds()
                issues.append(f"ingest_stale ({int(age)}s sem packets)")
        except ValueError:
            pass

    reg = settings.ip_registry_path
    if reg.exists() and reg.stat().st_size > IP_REGISTRY_MAX_BYTES:
        gb = reg.stat().st_size / 1024**3
        issues.append(f"ip_registry_oversized ({gb:.1f}GB)")

    return issues


@router.get("/system-status")
def system_status(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
):
    return collect_status(settings)


@router.get("/healthz")
def healthz(
    response: Response,
    settings: Settings = Depends(get_settings_dep),
):
    """Liveness + readiness sumário.

    Não exige autenticação (monitoria externa precisa bater sem cookie).
    Retorna 503 se algum threshold crítico falhar, com lista de issues.
    """
    status = collect_status(settings)
    issues = _check_health(status, settings)
    if issues:
        response.status_code = 503
        return {"status": "degraded", "issues": issues}
    return {
        "status": "ok",
        "ingest": {
            "raw_last_seen":    status["ingest"].get("raw_last_seen"),
            "today_log_count":  status["ingest"].get("today_log_count"),
        },
    }
