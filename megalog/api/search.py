"""SearchService — consulta forense sobre partições hot/cold.

Substitui [app/database.py:search_logs](../../../app/database.py#L464) (v4).
Recebe um `SearchQuery` Pydantic, abre a partição do dia (transparente
hot/cold) via `partitions.open_for_query`, monta WHERE dinâmico, retorna
linhas paginadas + total + dicionários resolvidos (interface, proto, IPs).

Performance:
  - Filtros de IP são resolvidos via IpRegistry (1 lookup) ANTES da query —
    evita JOIN com `ip_registry.duckdb` em cada linha.
  - Paginação por LIMIT/OFFSET (ok até dezenas de milhares); para janelas
    maiores, exportar via CSV.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Iterable

import duckdb
from pydantic import BaseModel, Field, field_validator

from megalog.storage.ip_registry import IpRegistry, ip_to_int, int_to_ip
from megalog.storage.partitions import PartitionLocation, open_for_query


_PROTO_NAME = {1: "TCP", 2: "UDP"}


class SearchQuery(BaseModel):
    """Parâmetros da busca forense — todos opcionais exceto `date`."""
    date: date
    src_ip:    str | None = None
    dst_ip:    str | None = None
    nat_ip:    str | None = None
    src_port:  int | None = Field(default=None, ge=0, le=65535)
    dst_port:  int | None = Field(default=None, ge=0, le=65535)
    nat_port:  int | None = Field(default=None, ge=0, le=65535)
    proto:     str | None = None  # 'TCP' ou 'UDP'
    ts_start:  int | None = None
    ts_end:    int | None = None
    page:      int = Field(default=1, ge=1)
    per_page:  int = Field(default=100, ge=1, le=1000)

    @field_validator("src_ip", "dst_ip", "nat_ip")
    @classmethod
    def _validate_ip(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        try:
            ip_to_int(v)
        except ValueError as e:
            raise ValueError(f"IP inválido: {v}") from e
        return v

    @field_validator("proto")
    @classmethod
    def _normalize_proto(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.strip().upper()
        if v not in {"TCP", "UDP"}:
            raise ValueError("proto deve ser TCP ou UDP")
        return v


class SearchResult(BaseModel):
    total: int
    page: int
    per_page: int
    rows: list[dict[str, Any]]


def _resolve_or_none(ip_str: str | None, registry: IpRegistry) -> int | None:
    if ip_str is None:
        return None
    ip_int = ip_to_int(ip_str)
    return registry.get_id_no_create(ip_int)


def _proto_id(proto: str | None, con: duckdb.DuckDBPyConnection) -> int | None:
    if not proto:
        return None
    # protocols está dentro do DuckDB attachado (hot) ou — em Parquet —
    # não existe. Tentamos buscar; se não existe, mapeamos por convenção.
    try:
        row = con.execute(
            "SELECT id FROM src.protocols WHERE name = ?", [proto]
        ).fetchone()
        if row is not None:
            return row[0]
    except duckdb.Error:
        pass
    # Convenção do dicionário (igual no v4): 1=TCP, 2=UDP, na ordem em que
    # surgem. Default seguro:
    return {"TCP": 1, "UDP": 2}.get(proto)


def search_partition(
    location: PartitionLocation,
    query: SearchQuery,
    registry: IpRegistry,
) -> SearchResult:
    """Executa a busca em UMA partição (1 dia)."""
    con = open_for_query(location)
    try:
        # ── resolver IPs/protocolo via registry ANTES da query ───────────────
        src_id = _resolve_or_none(query.src_ip, registry)
        dst_id = _resolve_or_none(query.dst_ip, registry)
        nat_id = _resolve_or_none(query.nat_ip, registry)
        # se filtro de IP foi pedido mas registry não conhece o IP → 0 resultados
        for filter_ip, resolved in (
            (query.src_ip, src_id), (query.dst_ip, dst_id), (query.nat_ip, nat_id),
        ):
            if filter_ip is not None and resolved is None:
                return SearchResult(total=0, page=query.page, per_page=query.per_page, rows=[])

        # protocolo: tenta dicionário do hot, fallback para convenção
        proto_id_filter: int | None = None
        if query.proto:
            try:
                row = con.execute(
                    "SELECT id FROM src.protocols WHERE name=?", [query.proto]
                ).fetchone()
                proto_id_filter = row[0] if row else {"TCP": 1, "UDP": 2}.get(query.proto)
            except duckdb.Error:
                proto_id_filter = {"TCP": 1, "UDP": 2}.get(query.proto)

        # ── WHERE dinâmico ───────────────────────────────────────────────────
        clauses: list[str] = []
        params: list[Any] = []

        if query.ts_start is not None:
            clauses.append("ts >= ?"); params.append(query.ts_start)
        if query.ts_end is not None:
            clauses.append("ts <= ?"); params.append(query.ts_end)
        if src_id is not None:
            clauses.append("src_ip_id = ?"); params.append(src_id)
        if dst_id is not None:
            clauses.append("dst_ip_id = ?"); params.append(dst_id)
        if nat_id is not None:
            clauses.append("nat_ip_id = ?"); params.append(nat_id)
        if query.src_port is not None:
            clauses.append("src_port = ?"); params.append(query.src_port)
        if query.dst_port is not None:
            clauses.append("dst_port = ?"); params.append(query.dst_port)
        if query.nat_port is not None:
            clauses.append("nat_port = ?"); params.append(query.nat_port)
        if proto_id_filter is not None:
            clauses.append("proto_id = ?"); params.append(proto_id_filter)

        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        offset = (query.page - 1) * query.per_page

        total = con.execute(
            f"SELECT COUNT(*) FROM logs{where_sql}", params
        ).fetchone()[0]

        rows = con.execute(
            f"SELECT ts, in_iface_id, out_iface_id, proto_id, conn_state_id, "
            f"       has_snat, src_ip_id, src_port, dst_ip_id, dst_port, "
            f"       nat_ip_id, nat_port, pkt_len, tcp_flags "
            f"FROM logs{where_sql} "
            f"ORDER BY ts ASC "
            f"LIMIT ? OFFSET ?",
            params + [query.per_page, offset],
        ).fetchall()

        # ── enriquecer resultado: id → string ────────────────────────────────
        # Hot (DuckDB) lê das tabelas auxiliares; cold (Parquet) lê do sidecar
        # JSON gerado em archive_day. Parquets antigos sem sidecar degradam
        # graciosamente para None nos campos.
        iface_map: dict[int, str] = {}
        proto_map: dict[int, str] = dict(_PROTO_NAME)
        state_map: dict[int, str] = {}
        if location.format == "duckdb":
            try:
                for r in con.execute("SELECT id, name FROM src.interfaces").fetchall():
                    iface_map[r[0]] = r[1]
                for r in con.execute("SELECT id, name FROM src.protocols").fetchall():
                    proto_map[r[0]] = r[1]
                for r in con.execute("SELECT id, name FROM src.conn_states").fetchall():
                    state_map[r[0]] = r[1]
            except duckdb.Error:
                pass
        else:
            sidecar = location.path.parent / f"{location.path.stem}.dicts.json"
            if sidecar.exists():
                data = json.loads(sidecar.read_text())
                iface_map = {int(k): v for k, v in data.get("interfaces", {}).items()}
                for k, v in data.get("protocols", {}).items():
                    proto_map[int(k)] = v
                state_map = {int(k): v for k, v in data.get("conn_states", {}).items()}

        # Bulk lookup: 1 SELECT IN (...) por chunk em vez de N queries individuais.
        # SQLite default SQLITE_MAX_VARIABLE_NUMBER = 999.
        ids_needed: set[int] = set()
        for r in rows:
            for idn in (r[6], r[8], r[10]):  # src_ip_id, dst_ip_id, nat_ip_id
                if idn is not None:
                    ids_needed.add(idn)

        ip_str_cache: dict[int, str] = {}
        ids_list = list(ids_needed)
        for i in range(0, len(ids_list), 900):
            chunk = ids_list[i : i + 900]
            placeholders = ",".join("?" * len(chunk))
            for ip_id, ip_int in registry._con.execute(  # noqa: SLF001
                f"SELECT ip_id, ip FROM ip_registry WHERE ip_id IN ({placeholders})",
                chunk,
            ).fetchall():
                ip_str_cache[ip_id] = int_to_ip(ip_int & 0xFFFFFFFF)

        def _ip(idn: int | None) -> str | None:
            if idn is None:
                return None
            return ip_str_cache.get(idn, f"id={idn}")

        out: list[dict[str, Any]] = []
        for r in rows:
            (ts, in_id, out_id, proto_id, state_id, has_snat,
             src_id_, src_port, dst_id_, dst_port,
             nat_id_, nat_port, pkt_len, tcp_flags) = r
            out.append({
                "ts":       ts,
                "ts_iso":   datetime.fromtimestamp(ts).isoformat(),
                "in_iface":   iface_map.get(in_id),
                "out_iface":  iface_map.get(out_id),
                "proto":      proto_map.get(proto_id),
                "conn_state": state_map.get(state_id),
                "has_snat":   bool(has_snat),
                "src_ip":     _ip(src_id_),
                "src_port":   src_port,
                "dst_ip":     _ip(dst_id_),
                "dst_port":   dst_port,
                "nat_ip":     _ip(nat_id_),
                "nat_port":   nat_port,
                "pkt_len":    pkt_len,
                "tcp_flags":  tcp_flags,
            })

        return SearchResult(
            total=total, page=query.page, per_page=query.per_page, rows=out,
        )
    finally:
        con.close()


def export_csv(
    location: PartitionLocation,
    query: SearchQuery,
    registry: IpRegistry,
    *,
    max_rows: int = 100_000,
) -> Iterable[str]:
    """Generator que produz linhas CSV (com header). Não materializa em RAM."""
    # Reusa search_partition mudando per_page para o limite total
    cap_query = query.model_copy(update={"page": 1, "per_page": max_rows})
    result = search_partition(location, cap_query, registry)
    headers = [
        "ts_iso", "ts", "src_ip", "src_port", "dst_ip", "dst_port",
        "nat_ip", "nat_port", "proto", "in_iface", "out_iface",
        "conn_state", "has_snat", "tcp_flags", "pkt_len",
    ]
    yield ",".join(headers) + "\n"
    for r in result.rows:
        vals = []
        for h in headers:
            v = r.get(h, "")
            vals.append("" if v is None else str(v).replace(",", " "))
        yield ",".join(vals) + "\n"
