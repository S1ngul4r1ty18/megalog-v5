"""Job: detecta processor travado / buffer .raw em risco de encher.

Acionado por timer (megalog-stream-watchdog.timer). Verifica:
  - mtime do `stream_offsets.json` (se >N segundos = processor não progride)
  - tamanho total do diretório de stream (buffer crescendo sem processar)

Quando algum sinal dispara, insere uma anomaly_alert com classification
'watchdog_stream' que aparece na UI admin/alerts. Idempotente por dia.

Uso direto:
    python -m megalog.jobs.stream_watchdog
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import date

from megalog.config import get_settings
from megalog.storage.operational import OperationalStore

log = logging.getLogger("megalog.jobs.stream_watchdog")

OFFSETS_STALE_SECONDS = 3600  # 1h sem o processor avançar offsets
BUFFER_WARN_BYTES = 2 * 1024**3  # 2 GB acumulados em .raw é muito


def _stream_buffer_bytes(stream_dir) -> int:
    if not stream_dir.exists():
        return 0
    return sum(p.stat().st_size for p in stream_dir.iterdir() if p.suffix == ".raw")


def run() -> int:
    s = get_settings()
    issues: list[str] = []

    offsets_file = s.state_dir / "stream_offsets.json"
    now = time.time()
    if not offsets_file.exists():
        issues.append("stream_offsets.json ausente")
    else:
        age = now - offsets_file.stat().st_mtime
        if age > OFFSETS_STALE_SECONDS:
            issues.append(f"stream_offsets.json sem progresso há {int(age)}s")

    buf = _stream_buffer_bytes(s.stream_dir)
    if buf > BUFFER_WARN_BYTES:
        issues.append(f"buffer .raw com {buf/1024**3:.2f} GB acumulados")

    if not issues:
        log.info("watchdog ok: offsets %.0fs atrás, buffer %.1f MB",
                 now - offsets_file.stat().st_mtime if offsets_file.exists() else -1,
                 buf / 1024 / 1024)
        return 0

    today = date.today().isoformat()
    ops = OperationalStore(s.state_dir / "megalog.db")
    if ops.alert_exists(today):
        log.warning("watchdog: %d issue(s) detectada(s) — alerta de hoje já existe",
                    len(issues))
        return 1

    ops.insert_alert(
        date_str=today,
        log_count=0,
        db_size_bytes=buf,
        expected_count=0,
        ratio=0.0,
        analysis="; ".join(issues),
        details_json=json.dumps({"issues": issues, "checked_at": int(now)}),
        classification="watchdog_stream",
    )
    log.error("watchdog ALERTA: %s", "; ".join(issues))
    return 1


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    sys.exit(run())


if __name__ == "__main__":
    main()
