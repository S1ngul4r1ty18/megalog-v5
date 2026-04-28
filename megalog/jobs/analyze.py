"""Job: detecta anomalias e gera alertas com classificação multi-sinal.

Acionado via systemd timer 02:30 (depois do archive). Lê `daily_stats`,
identifica datas que excedem baseline×threshold, abre cada partição
candidata, executa as 10 queries de análise + classificador, e persiste
o alerta em `anomaly_alerts`.

Uso direto:
  python -m megalog.jobs.analyze [--date YYYY-MM-DD] [--force]
"""
from __future__ import annotations

import argparse
import logging
import sys

from megalog.analytics.anomaly_analyzer import analyze_day
from megalog.analytics.anomaly_detector import compute_anomalies
from megalog.config import get_settings
from megalog.storage.ip_registry import IpRegistry
from megalog.storage.operational import OperationalStore
from megalog.storage.partitions import list_all, open_for_query

log = logging.getLogger("megalog.jobs.analyze")


def _analyze_one(
    date_str: str,
    *,
    log_count: int,
    db_size_bytes: int,
    expected_count: float,
    ratio: float,
    ops: OperationalStore,
    registry: IpRegistry,
    partitions: dict,
    force: bool,
) -> bool:
    if not force and ops.alert_exists(date_str):
        log.info("alerta para %s já existe (use --force para reanalisar)", date_str)
        return False

    loc = partitions.get(date_str)
    if loc is None:
        log.warning("partição %s não encontrada", date_str)
        return False

    con = open_for_query(loc)
    try:
        analysis, details_json, classification = analyze_day(
            con,
            registry=registry,
            log_count=log_count,
            expected_count=expected_count,
            ratio=ratio,
        )
    finally:
        con.close()

    ops.insert_alert(
        date_str=date_str,
        log_count=log_count,
        db_size_bytes=db_size_bytes,
        expected_count=int(expected_count),
        ratio=ratio,
        analysis=analysis,
        details_json=details_json,
        classification=classification.category,
    )
    log.info(
        "alerta %s: ratio=%.1fx classification=%s score=%.2f",
        date_str, ratio, classification.category, classification.score,
    )
    return True


def run(*, only_date: str | None = None, force: bool = False) -> int:
    s = get_settings()
    ops = OperationalStore(s.state_dir / "megalog.db")
    registry = IpRegistry(
        s.ip_registry_path,
        hot_top_n=s.ip_cache_hot_top_n,
        max_cache=s.ip_cache_max,
    )
    partitions = list_all(s.hot_storage_dir, s.cold_storage_dir)
    daily_stats = ops.all_daily_stats()

    if only_date:
        if only_date not in daily_stats:
            log.error("data %s não está em daily_stats", only_date)
            return 0
        count, size = daily_stats[only_date]
        # Sem baseline pra rato; análise forçada com expected=count, ratio=1.0
        return int(_analyze_one(
            only_date,
            log_count=count, db_size_bytes=size,
            expected_count=float(count), ratio=1.0,
            ops=ops, registry=registry, partitions=partitions, force=True,
        ))

    candidates = compute_anomalies(daily_stats)
    log.info("candidatos a análise: %d", len(candidates))
    n = 0
    for c in candidates:
        if _analyze_one(
            c.date_str,
            log_count=c.log_count, db_size_bytes=c.db_size_bytes,
            expected_count=c.expected_count, ratio=c.ratio,
            ops=ops, registry=registry, partitions=partitions, force=force,
        ):
            n += 1
    registry.close()
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="Detecta e classifica anomalias diárias.")
    parser.add_argument("--date", dest="date", default=None,
                        help="Analisa data específica (YYYY-MM-DD), forçando.")
    parser.add_argument("--force", action="store_true",
                        help="Reanalisa mesmo se alerta já existe.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    n = run(only_date=args.date, force=args.force)
    log.info("alertas gerados: %d", n)


if __name__ == "__main__":
    main()
