"""Job: arquiva DBs hot antigos para cold (Parquet zstd) + atualiza daily_stats.

Acionado via systemd timer noturno (deploy/systemd/megalog-archive.timer).
Substitui os passos `move_hot_to_cold` + `compress_cold_dbs` + `update_daily_stats`
de [compress_old_dbs.py](../../../megalog/compress_old_dbs.py).

Uso direto:
  python -m megalog.jobs.archive
"""
from __future__ import annotations

import logging
import sys

from megalog.config import get_settings
from megalog.storage.operational import OperationalStore
from megalog.storage.partitions import (
    count_logs,
    list_all,
    move_old_to_cold,
)

log = logging.getLogger("megalog.jobs.archive")


def run() -> int:
    s = get_settings()
    s.cold_storage_dir.mkdir(parents=True, exist_ok=True)
    s.state_dir.mkdir(parents=True, exist_ok=True)

    archived = move_old_to_cold(
        s.hot_storage_dir,
        s.cold_storage_dir,
        retention_days=s.hot_retention_days,
    )
    log.info("Arquivados %d DBs para cold: %s", len(archived), archived)

    # Atualiza daily_stats: rescaneia tudo
    ops = OperationalStore(s.state_dir / "megalog.db")
    partitions = list_all(s.hot_storage_dir, s.cold_storage_dir)
    for date_str, loc in sorted(partitions.items()):
        n = count_logs(loc)
        ops.upsert_daily_stats(date_str, n, loc.size_bytes)
    log.info("daily_stats atualizado: %d partições", len(partitions))
    return len(archived)


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
