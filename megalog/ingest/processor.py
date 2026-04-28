"""Processor: tail dos arquivos .raw → parse → insert batch no DuckDB.

Loop principal:
1. Lista `stream_dir/*.raw` em ordem cronológica.
2. Para cada arquivo, abre no offset salvo, faz tail até EOF.
3. Junta linhas de continuação Mikrotik (parser.join_continuations).
4. Parser puro → ParsedLine.
5. Resolve IDs (interfaces/protocols/conn_states/IPs) via caches.
6. Acumula em batch (size=settings.batch_size, flush=settings.batch_flush_seconds).
7. INSERT batch + CHECKPOINT periódico no DuckDB.
8. Salva offset; se arquivo da hora anterior foi totalmente consumido, arquiva.

Heurística importante: o `ParsedLine.ts` decide o `day` (DB destino), não a
data do arquivo .raw. Logs com timestamp pré-meia-noite chegando depois
da virada de dia ainda vão para o DB correto.
"""
from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import date, datetime
from pathlib import Path

from megalog.config import Settings, get_settings
from megalog.ingest.parser import ParsedLine, join_continuations, parse_line
from megalog.ingest.stream import (
    StreamOffsets,
    archive_processed,
    is_finalized,
    list_stream_files,
)
from megalog.storage.base import LogEntry
from megalog.storage.duckdb_store import DuckDBLogStore
from megalog.storage.ip_registry import IpRegistry, ip_to_int

log = logging.getLogger("megalog.processor")


class Processor:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self.s.stream_dir.mkdir(parents=True, exist_ok=True)
        self.s.state_dir.mkdir(parents=True, exist_ok=True)

        self.store = DuckDBLogStore(self.s.hot_storage_dir, threads=self.s.duckdb_threads)
        self.ip_reg = IpRegistry(
            self.s.ip_registry_path,
            hot_top_n=self.s.ip_cache_hot_top_n,
            max_cache=self.s.ip_cache_max,
        )
        self.offsets = StreamOffsets(self.s.state_dir / "stream_offsets.json")

        self._batch: dict[date, list[LogEntry]] = {}
        self._last_flush_at = time.monotonic()
        self._last_ip_flush_at = time.monotonic()
        self._last_duckdb_close_at = time.monotonic()
        self._stats = {"parsed": 0, "inserted": 0, "errors": 0}
        self.running = True

    # ─── pipeline interno ───────────────────────────────────────────────────

    def _to_entry(self, p: ParsedLine) -> LogEntry:
        d = datetime.fromtimestamp(p.ts).date()
        iface_c, proto_c, state_c = self.store.caches(d)
        return LogEntry(
            ts=p.ts,
            in_iface_id=iface_c.get_or_create(p.in_iface),
            out_iface_id=iface_c.get_or_create(p.out_iface),
            proto_id=proto_c.get_or_create(p.proto),
            conn_state_id=state_c.get_or_create(p.conn_state),
            has_snat=p.has_snat,
            src_ip_id=self.ip_reg.get_or_create(ip_to_int(p.src_ip)),
            src_port=p.src_port,
            dst_ip_id=self.ip_reg.get_or_create(ip_to_int(p.dst_ip)),
            dst_port=p.dst_port,
            nat_ip_id=self.ip_reg.get_or_create(ip_to_int(p.nat_ip)),
            nat_port=p.nat_port,
            pkt_len=p.pkt_len,
            tcp_flags=p.tcp_flags,
            log_type=p.log_type,
        )

    def _enqueue(self, entry: LogEntry) -> None:
        d = datetime.fromtimestamp(entry.ts).date()
        self._batch.setdefault(d, []).append(entry)

    def _batch_total(self) -> int:
        return sum(len(v) for v in self._batch.values())

    def _flush(self, force: bool = False) -> None:
        total = self._batch_total()
        time_due = (time.monotonic() - self._last_flush_at) >= self.s.batch_flush_seconds
        if not force and total < self.s.batch_size and not time_due:
            return
        if total == 0:
            self._last_flush_at = time.monotonic()
            self._maybe_release_duckdb_lock()
            return
        for day, entries in self._batch.items():
            n = self.store.insert_batch(day, entries)
            self._stats["inserted"] += n
        self._batch.clear()
        self._last_flush_at = time.monotonic()

        if (time.monotonic() - self._last_ip_flush_at) >= 60:
            self.ip_reg.flush_hits()
            self._last_ip_flush_at = time.monotonic()

        self._maybe_release_duckdb_lock()

    def _maybe_release_duckdb_lock(self) -> None:
        """
        Fecha conexões DuckDB periodicamente para liberar o write-lock e
        permitir que a API web abra os mesmos arquivos em modo read-only.
        DuckDB não suporta leitor concorrente com escritor — esta é a
        janela de visibilidade para queries forenses no dia atual.
        """
        if (time.monotonic() - self._last_duckdb_close_at) < self.s.duckdb_close_interval_seconds:
            return
        self.store.close()
        self._last_duckdb_close_at = time.monotonic()

    def process_line(self, raw: str) -> bool:
        parsed = parse_line(raw)
        if parsed is None:
            self._stats["errors"] += 1
            return False
        self._stats["parsed"] += 1
        self._enqueue(self._to_entry(parsed))
        return True

    # ─── tail loop ──────────────────────────────────────────────────────────

    def _process_file(self, path: Path) -> None:
        offset = self.offsets.get(path.name)
        with path.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            # Lê todas as linhas disponíveis; join_continuations consome o
            # iterator e devolve linhas lógicas já joinadas.
            buffer: list[str] = []
            while self.running:
                line = f.readline()
                if not line:
                    break
                buffer.append(line)
            for joined in join_continuations(buffer):
                self.process_line(joined)
                if self._batch_total() >= self.s.batch_size:
                    self._flush()
            self.offsets.set(path.name, f.tell())
            self.offsets.save()

    def _maybe_archive(self, path: Path) -> None:
        # Arquiva apenas se a hora do arquivo já passou e ele foi totalmente lido.
        if not is_finalized(path):
            return
        try:
            size = path.stat().st_size
        except OSError:
            return
        if self.offsets.get(path.name) >= size:
            self._flush(force=True)
            archive_processed(
                path,
                self.s.stream_processed_dir,
                self.s.stream_processed_keep_hours,
            )
            self.offsets.forget(path.name)
            self.offsets.save()
            log.info("Archived: %s", path.name)

    def tick(self) -> None:
        for path in list_stream_files(self.s.stream_dir):
            self._process_file(path)
            self._maybe_archive(path)
        self._flush()

    def run(self) -> None:
        log.info("Processor starting (batch_size=%d)", self.s.batch_size)

        def _stop(_signum, _frame):
            self.running = False
            log.info("Stop signal received")
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        last_log_at = time.monotonic()
        while self.running:
            self.tick()
            if (time.monotonic() - last_log_at) >= 60:
                log.info(
                    "stats: parsed=%d inserted=%d errors=%d ip_cache=%d",
                    self._stats["parsed"], self._stats["inserted"],
                    self._stats["errors"], self.ip_reg.cache_size(),
                )
                last_log_at = time.monotonic()
            time.sleep(self.s.tail_sleep_seconds)

        self._flush(force=True)
        self.ip_reg.flush_hits()
        self.store.close()
        self.ip_reg.close()
        log.info("Processor stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    Processor().run()


if __name__ == "__main__":
    main()
