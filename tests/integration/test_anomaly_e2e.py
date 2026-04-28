"""Pipeline de anomalia end-to-end usando UM DB real do servidor antigo.

Estratégia:
  1. Importa /tmp/legacy/2026-04-20.db para o formato DuckDB v5 (via SQL ATTACH).
  2. Roda `analyze_day` na partição.
  3. Confere que: total_conns bate, top_ips tem valores plausíveis, classifier
     devolve uma das categorias documentadas.

Test é skipped se /tmp/legacy/ não existe (mantém suite portável).
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from megalog.analytics.anomaly_analyzer import analyze_day, collect_stats
from megalog.analytics.anomaly_detector import compute_anomalies
from megalog.analytics.classifier import classify_traffic

LEGACY_DB = Path("/tmp/legacy/2026-04-20.db")


@pytest.fixture(scope="module")
def duckdb_from_legacy(tmp_path_factory) -> duckdb.DuckDBPyConnection:
    if not LEGACY_DB.exists():
        pytest.skip("DB legado não disponível em /tmp/legacy/")
    tmp = tmp_path_factory.mktemp("legacy_duck")
    duck_path = tmp / "2026-04-20.duckdb"
    con = duckdb.connect(str(duck_path))
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{LEGACY_DB}' AS src (TYPE SQLITE, READ_ONLY)")
    con.execute("""
        CREATE TABLE logs AS
        SELECT
            ts, in_iface_id, out_iface_id, proto_id, conn_state_id,
            CAST(COALESCE(has_snat, 0) AS BOOLEAN) AS has_snat,
            src_ip_id, src_port, dst_ip_id, dst_port, nat_ip_id, nat_port,
            pkt_len, tcp_flags, log_type
        FROM src.logs
    """)
    con.execute("DETACH src")
    yield con
    con.close()


def test_collect_stats_against_real_data(duckdb_from_legacy) -> None:
    stats = collect_stats(duckdb_from_legacy)
    # Sanity: o DB tem 2.74M linhas
    assert stats["total_conns"] > 1_000_000
    assert stats["unique_src_ips"] > 100
    assert stats["unique_dst_ips"] > 1_000
    # Top talker existe e tem perfil
    assert stats["top_talker"]
    assert stats["top_talker"]["profile"] in {"distributed", "concentrated", "mixed"}
    # Distribuição de portas soma ≈ 1
    pr = stats["port_ranges"]
    assert abs(pr["well_known_pct"] + pr["registered_pct"] + pr["ephemeral_pct"] - 1.0) < 0.01


def test_classify_against_real_data(duckdb_from_legacy) -> None:
    stats = collect_stats(duckdb_from_legacy)
    c = classify_traffic(stats)
    # A classificação tem que ser válida (uma das 9 + indeterminado)
    valid = {
        "P2P/Torrent", "Abuso de Protocolo (NTP)", "Abuso de Protocolo (DNS)",
        "Ataque/DoS", "Varredura de Portas", "Botnet/C2", "Abuso de Banda",
        "Flood/Loop/Tráfego Automatizado", "Malware/Comportamento Anômalo",
        "Alto Volume (Indeterminado)",
    }
    assert c.category in valid
    assert 0.30 <= c.score <= 1.0


def test_analyze_day_returns_text_json_and_classification(duckdb_from_legacy) -> None:
    text, details_json, classification = analyze_day(
        duckdb_from_legacy, log_count=2_741_707,
        expected_count=1_000_000, ratio=2.74,
    )
    assert "Volume" in text
    assert "Classificação" in text
    parsed = json.loads(details_json)
    assert "stats" not in parsed  # stats já está achatado em 'details'
    assert parsed["classification"] == classification.category
    assert parsed["classification_score"] == classification.score


def test_anomaly_detector_with_synthetic_baseline() -> None:
    """7 dias normais de 1M, dia 8 com 5M dispara anomalia."""
    stats = {f"2026-04-{d:02d}": (1_000_000, 100_000_000) for d in range(1, 8)}
    stats["2026-04-08"] = (5_000_000, 500_000_000)
    anomalies = compute_anomalies(stats, baseline_days=7, threshold_ratio=3.0, min_count=100_000)
    assert len(anomalies) == 1
    assert anomalies[0].date_str == "2026-04-08"
    assert anomalies[0].ratio == 5.0
