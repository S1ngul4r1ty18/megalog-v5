"""Stream buffer rotacionado por hora.

Substitui o `stream_logs.raw` monolítico do v4. Cada hora gera um
arquivo `YYYY-MM-DD-HH.raw` em `stream_dir/`. Após o processor confirmar
flush no DuckDB, o arquivo é movido para `stream_dir/.processed/`
e excluído após N horas (janela para replay).

Offsets persistidos por arquivo em `state_dir/stream_offsets.json` —
sobrevivem a reboot, sem dependência de `/tmp`.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path


class HourlyStreamWriter:
    """Append-only writer com rollover automático na virada de hora."""

    def __init__(self, stream_dir: Path, flush_every: int = 1000):
        self.stream_dir = stream_dir
        self.flush_every = flush_every
        stream_dir.mkdir(parents=True, exist_ok=True)
        self._fp = None
        self._current_hour: str | None = None
        self._writes_since_flush = 0

    def _hour_key(self, dt: datetime | None = None) -> str:
        dt = dt or datetime.now()
        return dt.strftime("%Y-%m-%d-%H")

    def _path_for(self, hour_key: str) -> Path:
        return self.stream_dir / f"{hour_key}.raw"

    def _rollover_if_needed(self) -> None:
        hour = self._hour_key()
        if hour != self._current_hour:
            if self._fp is not None:
                self._fp.flush()
                os.fsync(self._fp.fileno())
                self._fp.close()
            path = self._path_for(hour)
            # buffering=0 + manual flush dá mais controle que buffer Python
            self._fp = open(path, "ab", buffering=64 * 1024)
            self._current_hour = hour
            self._writes_since_flush = 0

    def write_line(self, raw: str) -> None:
        self._rollover_if_needed()
        if not raw.endswith("\n"):
            raw = raw + "\n"
        self._fp.write(raw.encode("utf-8", errors="replace"))
        self._writes_since_flush += 1
        if self._writes_since_flush >= self.flush_every:
            self._fp.flush()
            self._writes_since_flush = 0

    def close(self) -> None:
        if self._fp is not None:
            self._fp.flush()
            os.fsync(self._fp.fileno())
            self._fp.close()
            self._fp = None


class StreamOffsets:
    """Persiste offsets de leitura por arquivo .raw."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, int] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, filename: str) -> int:
        return self._data.get(filename, 0)

    def set(self, filename: str, offset: int) -> None:
        self._data[filename] = offset

    def forget(self, filename: str) -> None:
        self._data.pop(filename, None)

    def save(self) -> None:
        # Escrita atômica: tmp + rename
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data))
        tmp.replace(self.path)


def list_stream_files(stream_dir: Path) -> list[Path]:
    """Lista arquivos `.raw` em ordem cronológica (alfabética serve)."""
    if not stream_dir.exists():
        return []
    return sorted(p for p in stream_dir.iterdir() if p.suffix == ".raw" and p.is_file())


def archive_processed(
    src: Path,
    processed_dir: Path,
    keep_hours: int,
) -> None:
    """
    Move arquivo já consumido para `.processed/` e remove arquivos
    arquivados há mais de `keep_hours`.

    Reseta mtime ao mover: a janela de retenção mede "tempo desde o
    arquivamento", não "tempo desde a última escrita". Sem isso, .raw
    com mtime herdado de fonte externa (replay/import de logs antigos)
    seriam apagados imediatamente após mover.
    """
    processed_dir.mkdir(parents=True, exist_ok=True)
    dst = processed_dir / src.name
    src.replace(dst)
    now = time.time()
    try:
        os.utime(dst, (now, now))
    except OSError:
        pass
    cutoff = now - keep_hours * 3600
    for p in processed_dir.iterdir():
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def is_finalized(path: Path, now: datetime | None = None) -> bool:
    """
    Um arquivo de hora está "finalizado" quando a hora dele já passou.
    Usado para decidir se o processor pode arquivá-lo após consumi-lo.
    """
    now = now or datetime.now()
    name = path.stem  # YYYY-MM-DD-HH
    try:
        file_hour = datetime.strptime(name, "%Y-%m-%d-%H")
    except ValueError:
        return False
    return now >= file_hour + timedelta(hours=1)
