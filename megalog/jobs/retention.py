"""Job: aplica retenção (deleta Parquets de cold mais antigos que N dias).

Acionado via systemd timer semanal. `delete_after_days = 0` desativa.

Uso direto:
  python -m megalog.jobs.retention
"""
from __future__ import annotations

import logging
import sys

from megalog.config import get_settings
from megalog.storage.partitions import delete_expired

log = logging.getLogger("megalog.jobs.retention")


def run() -> int:
    s = get_settings()
    if s.delete_after_days <= 0:
        log.info("retenção desativada (delete_after_days=0)")
        return 0
    deleted = delete_expired(s.cold_storage_dir, delete_after_days=s.delete_after_days)
    log.info("removidas %d partições antigas: %s", len(deleted), deleted)
    return len(deleted)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    run()


if __name__ == "__main__":
    main()
