"""Cache de dicionários pequenos: interfaces, protocols, conn_states.

Estes têm cardinalidade muito baixa (dezenas de interfaces, ~3 protocolos,
~10 estados de conexão). Carregamos tudo em memória no startup e fazemos
lookup em O(1).
"""
from __future__ import annotations

import duckdb


class DictCache:
    """Mapeia name → id para uma tabela de dicionário (interfaces, protocols, etc)."""

    def __init__(self, con: duckdb.DuckDBPyConnection, table: str):
        self.con = con
        self.table = table
        # Não há SEQUENCE em DuckDB para tabela qualquer; geramos id manualmente.
        self.con.execute(
            f"CREATE TABLE IF NOT EXISTS {table} "
            f"(id INTEGER PRIMARY KEY, name VARCHAR UNIQUE NOT NULL)"
        )
        rows = self.con.execute(f"SELECT id, name FROM {table}").fetchall()
        self._by_name: dict[str, int] = {name: id_ for id_, name in rows}
        self._next_id: int = (max(self._by_name.values()) + 1) if self._by_name else 1

    def get_or_create(self, name: str | None) -> int | None:
        if name is None:
            return None
        if (id_ := self._by_name.get(name)) is not None:
            return id_
        id_ = self._next_id
        self._next_id += 1
        self.con.execute(
            f"INSERT INTO {self.table} (id, name) VALUES (?, ?)", [id_, name]
        )
        self._by_name[name] = id_
        return id_

    def by_id(self) -> dict[int, str]:
        return {v: k for k, v in self._by_name.items()}
