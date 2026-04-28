"""Testes do detector de anomalias (baseline móvel + threshold)."""
from __future__ import annotations

from megalog.analytics.anomaly_detector import compute_anomalies


def test_empty_input_returns_empty_list() -> None:
    assert compute_anomalies({}) == []


def test_skips_days_below_min_count() -> None:
    stats = {f"2026-04-{d:02d}": (50_000, 1000) for d in range(1, 10)}
    out = compute_anomalies(stats, min_count=100_000)
    assert out == []


def test_skips_when_baseline_too_short() -> None:
    """Precisa de 3 dias de baseline antes."""
    stats = {
        "2026-04-01": (1_000_000, 1000),
        "2026-04-02": (5_000_000, 1000),
    }
    out = compute_anomalies(stats, baseline_days=7, threshold_ratio=3.0, min_count=100_000)
    assert out == []


def test_detects_clear_anomaly() -> None:
    """5x acima do baseline = anomalia."""
    stats = {f"2026-04-{d:02d}": (1_000_000, 1000) for d in range(1, 8)}
    stats["2026-04-08"] = (5_000_000, 5000)
    out = compute_anomalies(stats, baseline_days=7, threshold_ratio=3.0, min_count=100_000)
    assert len(out) == 1
    assert out[0].date_str == "2026-04-08"
    assert out[0].ratio == 5.0
    assert out[0].expected_count == 1_000_000


def test_only_emits_above_threshold() -> None:
    """2x está abaixo do threshold padrão de 3.0."""
    stats = {f"2026-04-{d:02d}": (1_000_000, 1000) for d in range(1, 8)}
    stats["2026-04-08"] = (2_000_000, 2000)
    out = compute_anomalies(stats, baseline_days=7, threshold_ratio=3.0, min_count=100_000)
    assert out == []


def test_baseline_window_slides() -> None:
    """Anomalia em D8 deve usar D1-D7 como baseline, não D2-D8."""
    stats = {f"2026-04-{d:02d}": (100_000, 1000) for d in range(1, 8)}
    stats["2026-04-08"] = (1_000_000, 5000)  # 10x acima de 100k
    out = compute_anomalies(stats, baseline_days=7, threshold_ratio=3.0, min_count=100_000)
    assert len(out) == 1
    assert out[0].ratio == 10.0
