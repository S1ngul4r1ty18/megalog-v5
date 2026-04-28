"""Detector de anomalias — compara volume diário com baseline móvel.

Adapta [compress_old_dbs.py:detect_anomalies](../../../megalog/compress_old_dbs.py#L260)
em duas funções puras:
  - `compute_anomalies` recebe `daily_stats` ({date_str: (count, size)}) e
    retorna lista de candidatos (DateAnomaly) que merecem análise profunda.
  - O orquestrador (job analyze) decide o que fazer com cada candidato:
    abrir o DB, rodar `analyze_day`, persistir alerta.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean


@dataclass(slots=True, frozen=True)
class DateAnomaly:
    date_str: str
    log_count: int
    db_size_bytes: int
    expected_count: float
    ratio: float


def compute_anomalies(
    daily_stats: dict[str, tuple[int, int]],
    *,
    baseline_days: int = 7,
    threshold_ratio: float = 3.0,
    min_count: int = 100_000,
) -> list[DateAnomaly]:
    """
    Para cada data, calcula `ratio = count / mean(baseline)` e emite
    `DateAnomaly` se exceder `threshold_ratio` E tiver volume >= `min_count`.

    Baseline = janela móvel dos N dias anteriores (não inclui o dia atual).
    Requer ≥3 dias de baseline com volume não-nulo para evitar arranque ruim.
    """
    if not daily_stats:
        return []

    dates_sorted = sorted(daily_stats.keys())
    out: list[DateAnomaly] = []

    for i, current_date in enumerate(dates_sorted):
        current_count, current_size = daily_stats[current_date]
        if current_count < min_count:
            continue

        baseline_dates = dates_sorted[max(0, i - baseline_days):i]
        if len(baseline_dates) < 3:
            continue

        baseline_counts = [
            daily_stats[d][0] for d in baseline_dates if daily_stats[d][0] > 0
        ]
        if not baseline_counts:
            continue

        avg = mean(baseline_counts)
        if avg < min_count / 10:
            continue

        ratio = current_count / avg
        if ratio >= threshold_ratio:
            out.append(DateAnomaly(
                date_str=current_date,
                log_count=current_count,
                db_size_bytes=current_size,
                expected_count=avg,
                ratio=ratio,
            ))

    return out
