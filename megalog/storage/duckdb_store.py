"""LogStore implementado em DuckDB.

Um arquivo `.duckdb` por dia em `hot_storage_dir/YYYY-MM-DD.duckdb`.
Tabela `logs` com schema enxuto (UTINYINT/USMALLINT/UINTEGER/BOOLEAN).
Sem índices B-tree explícitos: DuckDB usa zone maps automáticos por
coluna. Ordem de inserção (ts, src_ip_id) garante data locality.

As tabelas de dicionário (interfaces, protocols, conn_states) ficam
embutidas em cada DB diário — são pequenas, recriação não custa.
O `ip_registry` é separado (global, persistente) — vive em outro arquivo.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

import duckdb

from .base import LogEntry, LogStore
from .dict_cache import DictCache


_LOGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS logs (
    ts             UINTEGER NOT NULL,
    in_iface_id    USMALLINT,
    out_iface_id   USMALLINT,
    proto_id       UTINYINT,
    conn_state_id  USMALLINT,
    has_snat       BOOLEAN,
    src_ip_id      UINTEGER NOT NULL,
    src_port       USMALLINT,
    dst_ip_id      UINTEGER NOT NULL,
    dst_port       USMALLINT,
    nat_ip_id      UINTEGER,
    nat_port       USMALLINT,
    pkt_len        USMALLINT,
    tcp_flags      VARCHAR,
    log_type       VARCHAR DEFAULT 'nat'
);

CREATE TABLE IF NOT EXISTS db_meta (
    key   VARCHAR PRIMARY KEY,
    value VARCHAR
);
"""


class DuckDBLogStore(LogStore):
    def __init__(
        self,
        hot_dir: Path,
        threads: int = 2,
    ):
        self.hot_dir = hot_dir
        self.threads = threads
        hot_dir.mkdir(parents=True, exist_ok=True)
        # uma conexão write por dia, mantida aberta entre batches
        self._connections: dict[date, duckdb.DuckDBPyConnection] = {}
        self._iface_cache: dict[date, DictCache] = {}
        self._proto_cache: dict[date, DictCache] = {}
        self._state_cache: dict[date, DictCache] = {}

    def _connect(self, day: date) -> duckdb.DuckDBPyConnection:
        if (con := self._connections.get(day)) is not None:
            return con
        path = self.hot_dir / f"{day.isoformat()}.duckdb"
        con = duckdb.connect(str(path))
        con.execute(f"PRAGMA threads={self.threads}")
        con.execute(_LOGS_SCHEMA)
        self._connections[day] = con
        self._iface_cache[day] = DictCache(con, "interfaces")
        self._proto_cache[day] = DictCache(con, "protocols")
        self._state_cache[day] = DictCache(con, "conn_states")
        return con

    def caches(self, day: date) -> tuple[DictCache, DictCache, DictCache]:
        """Expõe os DictCaches para o processor resolver IDs antes do batch."""
        self._connect(day)
        return self._iface_cache[day], self._proto_cache[day], self._state_cache[day]

    def insert_batch(self, day: date, entries: Iterable[LogEntry]) -> int:
        con = self._connect(day)
        rows = [
            (
                e.ts, e.in_iface_id, e.out_iface_id, e.proto_id,
                e.conn_state_id, e.has_snat, e.src_ip_id, e.src_port,
                e.dst_ip_id, e.dst_port, e.nat_ip_id, e.nat_port,
                e.pkt_len, e.tcp_flags, e.log_type,
            )
            for e in entries
        ]
        if not rows:
            return 0
        con.executemany(
            "INSERT INTO logs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        return len(rows)

    def count(self, day: date) -> int:
        con = self._connect(day)
        return con.execute("SELECT COUNT(*) FROM logs").fetchone()[0]

    def checkpoint(self, day: date) -> None:
        if (con := self._connections.get(day)) is not None:
            con.execute("CHECKPOINT")

    def close_day(self, day: date) -> None:
        """
        Fecha a conexão de um dia específico (libera o write-lock do DuckDB).

        Necessário porque DuckDB tem lock exclusivo por arquivo: enquanto o
        processor mantém a conexão aberta, a API web não consegue ler o
        DuckDB do dia atual. O processor chama isso periodicamente entre
        flushes para abrir uma janela de visibilidade.
        """
        if (con := self._connections.pop(day, None)) is not None:
            try:
                con.execute("CHECKPOINT")
            except Exception:
                pass
            con.close()
        self._iface_cache.pop(day, None)
        self._proto_cache.pop(day, None)
        self._state_cache.pop(day, None)

    def close(self) -> None:
        for day in list(self._connections.keys()):
            self.close_day(day)
