"""Interface abstrata do storage de logs.

Permite trocar a implementação (DuckDB, futuro outro engine) sem mexer
no resto do pipeline. O processor e a API web só conhecem essa interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Iterable


@dataclass(slots=True)
class LogEntry:
    """Uma linha já parseada e com IDs resolvidos, pronta para INSERT."""
    ts: int
    in_iface_id: int | None
    out_iface_id: int | None
    proto_id: int | None
    conn_state_id: int | None
    has_snat: bool
    src_ip_id: int
    src_port: int | None
    dst_ip_id: int
    dst_port: int | None
    nat_ip_id: int | None
    nat_port: int | None
    pkt_len: int | None
    tcp_flags: str | None
    log_type: str = "nat"


class LogStore(ABC):
    """Storage de logs particionado por dia."""

    @abstractmethod
    def insert_batch(self, day: date, entries: Iterable[LogEntry]) -> int:
        """Insere um batch de entries no DB do dia. Retorna nº de linhas inseridas."""

    @abstractmethod
    def count(self, day: date) -> int:
        ...

    @abstractmethod
    def close(self) -> None:
        ...
