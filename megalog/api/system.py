"""Coleta de status do sistema (CPU/RAM/disco/serviços + métricas operacionais)."""
from __future__ import annotations

import shutil
import subprocess
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import psutil

from megalog.config import Settings
from megalog.storage.partitions import open_for_query


def _disk_for(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    usage = shutil.disk_usage(path)
    return {
        "path":        str(path),
        "total_bytes": usage.total,
        "used_bytes":  usage.used,
        "free_bytes":  usage.free,
        "used_pct":    round(usage.used / usage.total * 100, 1) if usage.total else 0,
    }


def _service_status(unit: str) -> str:
    """systemd is-active. Devolve 'active', 'inactive', 'failed' ou 'unknown'."""
    try:
        out = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip() or "unknown"
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unknown"


def _raw_buffer_size(stream_dir: Path) -> tuple[int, str | None]:
    """Soma o tamanho de todos os .raw da hora atual + última atividade."""
    if not stream_dir.exists():
        return 0, None
    total = 0
    latest_mtime: float | None = None
    for p in stream_dir.iterdir():
        if p.is_file() and p.suffix == ".raw":
            try:
                st = p.stat()
                total += st.st_size
                if latest_mtime is None or st.st_mtime > latest_mtime:
                    latest_mtime = st.st_mtime
            except OSError:
                pass
    last_iso = (
        datetime.fromtimestamp(latest_mtime).isoformat() if latest_mtime else None
    )
    return total, last_iso


# Cache em memória do último COUNT(*) válido. O DuckDB hot tem lock
# exclusivo do processor; o web abre janelas curtas a cada ~3s. Para
# /api/system-status (chamado a cada 3s pelo dashboard), evitamos a
# contenção: ressonância do polling do front com o intervalo de close
# fazia /api/system-status pegar lock conflict com frequência alta —
# resultado: "Logs hoje" piscava entre número e null.
#
# Cache TTL = 8s: pelo menos 2 ciclos do duckdb_close_interval (3s),
# garantindo que qualquer requisição vê um valor recente. Se a contagem
# falhar mesmo após retries, devolvemos o último valor conhecido (não null).
_count_cache: dict[str, tuple[float, int]] = {}  # date_str -> (timestamp, count)
_COUNT_CACHE_TTL_SECONDS = 8.0


def _today_db_metrics(hot_dir: Path) -> dict[str, Any]:
    """Tamanho + contagem do DuckDB do dia. Usa cache TTL para evitar
    flicker quando o processor segura o write-lock momentaneamente."""
    today = date.today().isoformat()
    db_path = hot_dir / f"{today}.duckdb"
    out: dict[str, Any] = {
        "today_date":     today,
        "today_db_path":  str(db_path),
        "today_db_size":  0,
        "today_log_count": None,
        "today_count_age_seconds": None,
    }
    if not db_path.exists():
        return out
    try:
        out["today_db_size"] = db_path.stat().st_size
    except OSError:
        pass

    cached = _count_cache.get(today)
    cached_age = (time.time() - cached[0]) if cached else None

    # Cache fresh? Devolve direto sem tentar abrir o DB (zero contenção).
    if cached and cached_age is not None and cached_age < _COUNT_CACHE_TTL_SECONDS:
        out["today_log_count"] = cached[1]
        out["today_count_age_seconds"] = round(cached_age, 1)
        return out

    # Cache stale ou ausente: tenta refresh com retry generoso (cobre o
    # close interval do processor: 8 × 0.5s = 4s).
    try:
        from megalog.storage.partitions import PartitionLocation
        loc = PartitionLocation(today, db_path, "hot", "duckdb", out["today_db_size"])
        con = open_for_query(loc, retries=8, retry_delay=0.5)
        try:
            n = con.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            _count_cache[today] = (time.time(), n)
            out["today_log_count"] = n
            out["today_count_age_seconds"] = 0.0
        finally:
            con.close()
    except duckdb.Error:
        # Lock persistente — devolve o último valor conhecido (mesmo stale).
        if cached:
            out["today_log_count"] = cached[1]
            out["today_count_age_seconds"] = round(cached_age or 0, 1)

    return out


def collect_status(settings: Settings) -> dict[str, Any]:
    cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    raw_size, last_raw_at = _raw_buffer_size(settings.stream_dir)
    today = _today_db_metrics(settings.hot_storage_dir)

    return {
        "cpu": {
            "per_core_pct": cpu_per_core,
            "avg_pct":      round(sum(cpu_per_core) / len(cpu_per_core), 1)
                            if cpu_per_core else 0.0,
            "load_avg":     list(psutil.getloadavg()),
        },
        "ram": {
            "total_bytes":     vm.total,
            "used_bytes":      vm.used,
            "available_bytes": vm.available,
            "used_pct":        vm.percent,
        },
        "swap": {
            "total_bytes": sm.total,
            "used_bytes":  sm.used,
            "used_pct":    sm.percent,
        },
        "disks": {
            "hot":    _disk_for(settings.hot_storage_dir),
            "cold":   _disk_for(settings.cold_storage_dir),
            "state":  _disk_for(settings.state_dir),
            "stream": _disk_for(settings.stream_dir),
        },
        "services": {
            "receiver":  _service_status("megalog-receiver.service"),
            "processor": _service_status("megalog-processor.service"),
            "web":       _service_status("megalog-web.service"),
        },
        "ingest": {
            "raw_buffer_bytes": raw_size,
            "raw_last_seen":    last_raw_at,
            **today,
        },
        "now": datetime.now().isoformat(timespec="seconds"),
    }
