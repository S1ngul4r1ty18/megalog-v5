"""Endpoints de analytics: daily stats + top IPs (registry global) + calendar."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from megalog.api.deps import (
    CurrentUser,
    get_current_user,
    get_ip_registry,
    get_ops_store,
    get_settings_dep,
)
from megalog.config import Settings
from megalog.storage.ip_registry import IpRegistry, int_to_ip
from megalog.storage.operational import OperationalStore
from megalog.storage.partitions import list_all

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/daily")
def daily(
    user: CurrentUser = Depends(get_current_user),
    ops: OperationalStore = Depends(get_ops_store),
    settings: Settings = Depends(get_settings_dep),
):
    """Lista de dias com contagem + tamanho + presença em hot/cold."""
    stats = ops.all_daily_stats()
    parts = list_all(settings.hot_storage_dir, settings.cold_storage_dir)
    return [
        {
            "date":           date_str,
            "log_count":      cnt,
            "db_size_bytes":  size,
            "kind":           parts[date_str].kind   if date_str in parts else "absent",
            "format":         parts[date_str].format if date_str in parts else None,
            "alert":          ops.get_alert_by_date(date_str) is not None,
        }
        for date_str, (cnt, size) in sorted(stats.items())
    ]


@router.get("/top-ips")
def top_ips(
    limit: int = Query(default=50, ge=1, le=1000),
    user: CurrentUser = Depends(get_current_user),
    registry: IpRegistry = Depends(get_ip_registry),
):
    """
    Top N IPs do REGISTRY GLOBAL (todas as partições).
    Esse é um caso onde o `ip_registry` brilha: query única em vez de
    abrir N DBs por dia.
    """
    rows = registry._con.execute(  # noqa: SLF001
        "SELECT ip_id, ip, total_hits, first_seen, last_seen, is_hot "
        "FROM ip_registry ORDER BY total_hits DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "ip_id":      ip_id,
            "ip":         int_to_ip(ip_int & 0xFFFFFFFF),
            "total_hits": hits,
            # SQLite armazena como INTEGER UNIX timestamp
            "first_seen": first,
            "last_seen":  last,
            "is_hot":     bool(is_hot),
        }
        for ip_id, ip_int, hits, first, last, is_hot in rows
    ]


@router.get("/calendar")
def calendar(
    user: CurrentUser = Depends(get_current_user),
    ops: OperationalStore = Depends(get_ops_store),
):
    """Map date_str → {count, alert}, para badge no dashboard."""
    stats = ops.all_daily_stats()
    return {
        date_str: {
            "log_count": cnt,
            "alert":     ops.get_alert_by_date(date_str) is not None,
        }
        for date_str, (cnt, _size) in stats.items()
    }
