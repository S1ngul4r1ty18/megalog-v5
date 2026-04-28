"""Registro global de IPs (SQLite WAL).

Diferença-chave vs MegaLog v4 (`ip_addresses` por DB diário): aqui o `ip_id`
é estável entre dias. O mesmo IP recebe sempre o mesmo ID, o que permite
analytics multi-dia em uma única query e elimina a re-cadastragem diária.

Por que SQLite (e não DuckDB):
  - DuckDB usa lock exclusivo absoluto por arquivo — não permite leitor
    concorrente com escritor. Em produção, o processor é writer e o web
    é leitor → conflito.
  - SQLite com `journal_mode=WAL` permite N leitores + 1 escritor
    simultâneos. Para uma tabela pequena (centenas de milhares de IPs),
    SQLite é mais que suficiente em performance.
  - DuckDB pode ler SQLite via extensão (`INSTALL sqlite`) — então JOINs
    cross-format (Parquet de logs × SQLite de registry) seguem possíveis.

Implementação:
  - Backing store: arquivo SQLite (`ip_registry.db`) com tabela
    `ip_registry(ip_id INTEGER PK, ip INTEGER UNIQUE, first_seen, last_seen,
     total_hits, is_hot)`. WAL mode.
  - Cache em memória: dict[int, int] (ip_int → ip_id).
  - Pré-carga: top-N IPs marcados como `is_hot=1` são carregados no startup.
  - Cold path (cache miss): consulta o SQLite; se IP não existe, INSERT;
    contadores de hits são agregados e flushados periodicamente.

Em CGNAT massivo a cardinalidade é tipicamente <100k IPs únicos/dia, e
o cache hit rate esperado é >95%.
"""
from __future__ import annotations

import ipaddress
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS ip_registry (
    ip_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ip         INTEGER NOT NULL UNIQUE,
    first_seen INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL,
    total_hits INTEGER NOT NULL DEFAULT 0,
    is_hot     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ip_registry_hits ON ip_registry(total_hits DESC);
"""


def ip_to_int(ip_str: str) -> int:
    """IPv4 string → uint32. Levanta ValueError se inválido."""
    return int(ipaddress.IPv4Address(ip_str))


def int_to_ip(ip_int: int) -> str:
    return str(ipaddress.IPv4Address(ip_int))


class IpRegistry:
    """
    Resolve `ip_int → ip_id` com cache em RAM e fallback persistente.

    Thread-safe para reads via WAL. Writes serializados pelo lock interno
    (assume-se um único processo writer — o processor).
    """

    def __init__(
        self,
        path: Path,
        hot_top_n: int = 50_000,
        max_cache: int = 200_000,
        read_only: bool = False,
        max_pending_hits: int = 50_000,
    ):
        self.path = path
        self.hot_top_n = hot_top_n
        self.max_cache = max_cache
        self.read_only = read_only
        self.max_pending_hits = max_pending_hits
        path.parent.mkdir(parents=True, exist_ok=True)

        # Em read-only, ainda criamos o arquivo (vazio com schema) na
        # primeira execução do web antes do processor escrever.
        if read_only and not path.exists():
            tmp = sqlite3.connect(str(path), isolation_level=None)
            tmp.executescript(_SCHEMA)
            tmp.close()

        # `check_same_thread=False`: a conexão é usada pelo flush_hits em
        # thread eventual (o processor é single-thread mas o web tem N).
        self._con = sqlite3.connect(
            str(path), isolation_level=None, check_same_thread=False, timeout=30,
        )
        if not read_only:
            self._con.executescript(_SCHEMA)
        else:
            # Garante que mesmo arquivos existentes adotem WAL pra leitura
            # paralela ao processor.
            self._con.execute("PRAGMA journal_mode=WAL")

        # cache em memória: ip_int → ip_id
        self._cache: dict[int, int] = {}
        # contadores pendentes (ip_id → +hits) — flushados em flush_hits()
        self._pending_hits: dict[int, int] = {}
        self._lock = threading.Lock()

        self._preload_hot()

    def _preload_hot(self) -> None:
        rows = self._con.execute(
            "SELECT ip, ip_id FROM ip_registry "
            "WHERE is_hot = 1 OR total_hits > 0 "
            "ORDER BY total_hits DESC LIMIT ?",
            (self.hot_top_n,),
        ).fetchall()
        self._cache = {ip: ip_id for ip, ip_id in rows}

    def get_or_create(self, ip_int: int) -> int:
        """Caminho rápido: cache. Caminho lento: SQLite lookup/insert."""
        if self.read_only:
            raise RuntimeError("IpRegistry aberto em modo read-only — use get_id_no_create")
        cached = self._cache.get(ip_int)
        if cached is not None:
            self._pending_hits[cached] = self._pending_hits.get(cached, 0) + 1
            if len(self._pending_hits) >= self.max_pending_hits:
                self.flush_hits()
            return cached

        # cache miss: tenta achar no SQLite
        row = self._con.execute(
            "SELECT ip_id FROM ip_registry WHERE ip = ?", (ip_int,)
        ).fetchone()
        if row is not None:
            ip_id = row[0]
        else:
            now = int(time.time())
            cur = self._con.execute(
                "INSERT INTO ip_registry (ip, first_seen, last_seen, total_hits) "
                "VALUES (?, ?, ?, 0)",
                (ip_int, now, now),
            )
            ip_id = cur.lastrowid

        # adiciona ao cache (com evicção simples se exceder)
        if len(self._cache) >= self.max_cache:
            cutoff = int(self.max_cache * 0.8)
            self._cache = dict(list(self._cache.items())[-cutoff:])
        self._cache[ip_int] = ip_id
        self._pending_hits[ip_id] = self._pending_hits.get(ip_id, 0) + 1
        if len(self._pending_hits) >= self.max_pending_hits:
            self.flush_hits()
        return ip_id

    def get_id_no_create(self, ip_int: int) -> int | None:
        """Lookup sem criar — usado pela camada de busca/API."""
        cached = self._cache.get(ip_int)
        if cached is not None:
            return cached
        row = self._con.execute(
            "SELECT ip_id FROM ip_registry WHERE ip = ?", (ip_int,)
        ).fetchone()
        return row[0] if row else None

    def flush_hits(self) -> int:
        """
        Persiste os contadores pendentes em uma única transação.
        Chamado pelo processor a cada N segundos. Retorna nº de IPs atualizados.
        Em modo read_only é no-op (sem writes pendentes).
        """
        if self.read_only or not self._pending_hits:
            return 0

        with self._lock:
            pending = self._pending_hits
            self._pending_hits = {}

        now = int(time.time())
        # UPDATE em batch via executemany
        rows = [(hits, now, ip_id) for ip_id, hits in pending.items()]
        self._con.execute("BEGIN")
        try:
            self._con.executemany(
                "UPDATE ip_registry SET total_hits = total_hits + ?, last_seen = ? "
                "WHERE ip_id = ?",
                rows,
            )
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return len(rows)

    def mark_hot(self, top_n: int | None = None) -> int:
        """Recalcula a flag is_hot — chamado pelo job de archive noturno."""
        if self.read_only:
            raise RuntimeError("IpRegistry aberto em modo read-only")
        n = top_n or self.hot_top_n
        self._con.execute("UPDATE ip_registry SET is_hot = 0")
        self._con.execute(
            "UPDATE ip_registry SET is_hot = 1 WHERE ip_id IN ("
            "  SELECT ip_id FROM ip_registry ORDER BY total_hits DESC LIMIT ?"
            ")",
            (n,),
        )
        return n

    def cache_size(self) -> int:
        return len(self._cache)

    def close(self) -> None:
        if not self.read_only:
            self.flush_hits()
        self._con.close()
