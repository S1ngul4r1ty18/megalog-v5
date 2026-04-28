"""Endpoints de busca forense + export CSV."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from megalog.api.deps import (
    CurrentUser,
    get_current_user,
    get_ip_registry,
    get_ops_store,
    get_settings_dep,
)
from megalog.api.search import SearchQuery, SearchResult, export_csv, search_partition
from megalog.config import Settings
from megalog.storage.ip_registry import IpRegistry
from megalog.storage.operational import OperationalStore
from megalog.storage.partitions import list_all

router = APIRouter(prefix="/api/search", tags=["search"])


def _resolve_partition(query: SearchQuery, settings: Settings):
    parts = list_all(settings.hot_storage_dir, settings.cold_storage_dir)
    return parts.get(query.date.isoformat())


@router.post("", response_model=SearchResult)
def search(
    query: SearchQuery,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
    registry: IpRegistry = Depends(get_ip_registry),
    ops: OperationalStore = Depends(get_ops_store),
):
    location = _resolve_partition(query, settings)
    if location is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Nenhuma partição para a data {query.date.isoformat()}",
        )
    ops.log_audit(
        user_id=user.id, username=user.username, action="search",
        details=query.model_dump_json(exclude_none=True),
        ip_address=request.client.host if request.client else None,
    )
    return search_partition(location, query, registry)


@router.post("/export")
def export(
    query: SearchQuery,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
    registry: IpRegistry = Depends(get_ip_registry),
    ops: OperationalStore = Depends(get_ops_store),
):
    location = _resolve_partition(query, settings)
    if location is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Nenhuma partição para a data {query.date.isoformat()}",
        )
    ops.log_audit(
        user_id=user.id, username=user.username, action="export",
        details=query.model_dump_json(exclude_none=True),
        ip_address=request.client.host if request.client else None,
    )
    fname = f"megalog-{query.date.isoformat()}.csv"
    return StreamingResponse(
        export_csv(location, query, registry),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
