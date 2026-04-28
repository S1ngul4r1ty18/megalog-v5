"""Coleta estatísticas detalhadas de um dia → dict pronto para o classificador.

Adapta as 10 queries de [compress_old_dbs.py:_analyze_anomaly](../../../megalog/compress_old_dbs.py#L743-L1087)
para DuckDB. Mudanças vs v4:
  - SQL é mais conciso em DuckDB (FILTER em vez de SUM(CASE)).
  - IPs vêm como `*_ip_id` (FK ao registry global) — resolvemos para string ao final.
  - Aceita conexão DuckDB read-only (hot ou Parquet via view).
  - Pure Python: não toca em SQLite operacional aqui (job orquestrador faz isso).
"""
from __future__ import annotations

import json
from typing import Any

import duckdb

from megalog.analytics.classifier import Classification, classify_traffic
from megalog.analytics.services import TCP_ONLY_PORTS, WELL_KNOWN_SERVICES, port_category
from megalog.storage.ip_registry import IpRegistry, int_to_ip


_PROTO_NAME = {1: "TCP", 2: "UDP"}


def _resolve_ip(ip_id: int | None, registry: IpRegistry | None) -> str:
    """ip_id → 'A.B.C.D'. Sem registry, devolve repr numérico."""
    if ip_id is None:
        return ""
    if registry is None:
        return f"id={ip_id}"
    # Reverse lookup — registry agora é SQLite WAL (substitui DuckDB).
    row = registry._con.execute(  # noqa: SLF001  acesso interno deliberado
        "SELECT ip FROM ip_registry WHERE ip_id = ?", (ip_id,)
    ).fetchone()
    if row is None:
        return f"id={ip_id}"
    # SQLite retorna INTEGER; pode ser negativo se IP > 2^31 (sign bit) —
    # ip_to_int retorna unsigned 32-bit, mas SQLite trata como signed.
    return int_to_ip(row[0] & 0xFFFFFFFF)


def collect_stats(
    con: duckdb.DuckDBPyConnection,
    *,
    registry: IpRegistry | None = None,
    proto_id_tcp: int = 1,
    proto_id_udp: int = 2,
) -> dict[str, Any]:
    """
    Roda as 10 queries de análise sobre `con` (precisa ter view/tabela `logs`).
    `proto_id_*` permitem mapear caso o seed do dicionário seja diferente.
    """
    # ── 1: globais + distribuição de portas + protocolos comuns ──────────────
    g = con.execute(
        """
        SELECT
            COUNT(*)                                                    AS total,
            COUNT(DISTINCT src_ip_id)                                   AS unique_src,
            COUNT(DISTINCT dst_ip_id)                                   AS unique_dst,
            COUNT(DISTINCT dst_port)                                    AS unique_dst_ports,
            COUNT(*) FILTER (WHERE dst_port BETWEEN 0   AND 1023)       AS well_known,
            COUNT(*) FILTER (WHERE dst_port BETWEEN 1024 AND 49151)     AS registered,
            COUNT(*) FILTER (WHERE dst_port >= 49152)                   AS ephemeral,
            COUNT(*) FILTER (WHERE dst_port = 53)                       AS dns,
            COUNT(*) FILTER (WHERE dst_port IN (80, 443))               AS http,
            COUNT(*) FILTER (WHERE dst_port = 123)                      AS ntp,
            COUNT(*) FILTER (WHERE dst_port = 22)                       AS ssh,
            COUNT(*) FILTER (WHERE dst_port = 3389)                     AS rdp,
            COUNT(*) FILTER (WHERE dst_port IN (25, 465, 587, 993, 995)) AS email
        FROM logs
        """
    ).fetchone()
    total_c = g[0] or 0
    if total_c == 0:
        return {"total_conns": 0}

    unique_src    = g[1] or 0
    unique_dst    = g[2] or 0
    unique_dports = g[3] or 0
    wk, reg, eph  = g[4] or 0, g[5] or 0, g[6] or 0
    port_sum      = max(wk + reg + eph, 1)
    dns_abs, http_abs, ntp_abs, ssh_abs = g[7] or 0, g[8] or 0, g[9] or 0, g[10] or 0
    rdp_abs, email_abs = g[11] or 0, g[12] or 0

    # ── 2: top 10 IPs origem com perfil ──────────────────────────────────────
    top_ip_rows = con.execute(
        """
        SELECT src_ip_id,
               COUNT(*) AS cnt,
               COUNT(DISTINCT dst_ip_id)   AS dst_ips,
               COUNT(DISTINCT dst_port)    AS dst_ports,
               (COUNT(*) FILTER (WHERE dst_port BETWEEN 0 AND 1023)) * 100.0 / COUNT(*) AS wk_pct,
               (COUNT(*) FILTER (WHERE dst_port >= 49152))           * 100.0 / COUNT(*) AS eph_pct
        FROM logs
        WHERE src_ip_id IS NOT NULL
        GROUP BY src_ip_id
        ORDER BY cnt DESC
        LIMIT 10
        """
    ).fetchall()
    top_ips: list[dict[str, Any]] = []
    for ip_id, cnt, dst_ips, dst_ports, wkp, ephp in top_ip_rows:
        top_ips.append({
            "ip":             _resolve_ip(ip_id, registry),
            "connections":    cnt,
            "dst_ips":        dst_ips,
            "dst_ports":      dst_ports,
            "pct":            round(cnt / total_c * 100, 1),
            "well_known_pct": round(wkp or 0, 1),
            "ephemeral_pct":  round(ephp or 0, 1),
        })

    tt_full: dict[str, Any] = {}
    if top_ips:
        tt = top_ips[0]
        if tt["dst_ports"] >= 500 and tt["dst_ips"] >= 500:
            profile = "distributed"
        elif tt["dst_ports"] <= 20 and tt["dst_ips"] <= 20:
            profile = "concentrated"
        else:
            profile = "mixed"
        tt_full = {**tt, "profile": profile}

    # ── 3: top 10 portas destino ─────────────────────────────────────────────
    top_port_rows = con.execute(
        """
        SELECT dst_port, COUNT(*) AS cnt
        FROM logs
        WHERE dst_port IS NOT NULL
        GROUP BY dst_port
        ORDER BY cnt DESC
        LIMIT 10
        """
    ).fetchall()
    top_ports = [
        {
            "port":     p,
            "count":    cnt,
            "pct":      round(cnt / total_c * 100, 2),
            "category": port_category(p),
            "service":  WELL_KNOWN_SERVICES.get(p, ""),
        }
        for p, cnt in top_port_rows
    ]

    # ── 4: top 10 IPs destino ────────────────────────────────────────────────
    top_dip_rows = con.execute(
        """
        SELECT dst_ip_id, COUNT(*) AS cnt
        FROM logs
        WHERE dst_ip_id IS NOT NULL
        GROUP BY dst_ip_id
        ORDER BY cnt DESC
        LIMIT 10
        """
    ).fetchall()
    top_dst_ips = [
        {
            "ip":    _resolve_ip(ip_id, registry),
            "count": cnt,
            "pct":   round(cnt / total_c * 100, 2),
        }
        for ip_id, cnt in top_dip_rows
    ]

    # ── 5: IPs com >50 portas distintas (suspeitos P2P) ──────────────────────
    p2p_row = con.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT src_ip_id FROM logs
            WHERE src_ip_id IS NOT NULL
            GROUP BY src_ip_id
            HAVING COUNT(DISTINCT dst_port) > 50
        ) AS sub
        """
    ).fetchone()
    p2p_suspects = (p2p_row[0] if p2p_row else 0)

    # ── 6: média de portas distintas por IP origem ───────────────────────────
    avg_row = con.execute(
        """
        SELECT AVG(pc) FROM (
            SELECT COUNT(DISTINCT dst_port) AS pc FROM logs
            WHERE src_ip_id IS NOT NULL
            GROUP BY src_ip_id
        ) AS sub
        """
    ).fetchone()
    avg_ports = float(avg_row[0]) if avg_row and avg_row[0] is not None else 0.0

    # ── 7: detalhe NTP (só se volume relevante) ──────────────────────────────
    ntp_detail: dict[str, int] = {}
    if ntp_abs >= 50_000:
        nr = con.execute(
            "SELECT COUNT(DISTINCT src_ip_id), COUNT(DISTINCT dst_ip_id) "
            "FROM logs WHERE dst_port = 123"
        ).fetchone()
        ntp_detail = {"ntp_clients": nr[0] or 0, "ntp_servers": nr[1] or 0}

    # ── 8: detalhe DNS ───────────────────────────────────────────────────────
    dns_detail: dict[str, int] = {}
    if dns_abs >= 200_000:
        dr = con.execute(
            "SELECT COUNT(DISTINCT src_ip_id), COUNT(DISTINCT dst_ip_id) "
            "FROM logs WHERE dst_port = 53"
        ).fetchone()
        dns_detail = {"dns_clients": dr[0] or 0, "dns_servers": dr[1] or 0}

    # ── 9: top 5 fluxos repetidos (4-tupla, sem src_port) ────────────────────
    top_flow_rows = con.execute(
        """
        SELECT src_ip_id, dst_ip_id, dst_port, proto_id, COUNT(*) AS cnt
        FROM logs
        WHERE src_ip_id IS NOT NULL AND dst_ip_id IS NOT NULL
        GROUP BY src_ip_id, dst_ip_id, dst_port, proto_id
        ORDER BY cnt DESC
        LIMIT 5
        """
    ).fetchall()
    top_flows = []
    max_flow_count = 0
    for src_id, dst_id, dst_port, proto_id, cnt in top_flow_rows:
        top_flows.append({
            "src_ip":   _resolve_ip(src_id, registry),
            "dst_ip":   _resolve_ip(dst_id, registry),
            "dst_port": dst_port,
            "proto":    _PROTO_NAME.get(proto_id, str(proto_id)),
            "count":    cnt,
            "service":  WELL_KNOWN_SERVICES.get(dst_port, ""),
        })
        max_flow_count = max(max_flow_count, cnt)

    # ── 10: UDP em portas exclusivas TCP ─────────────────────────────────────
    placeholders = ",".join("?" for _ in TCP_ONLY_PORTS)
    udp_tcp_rows = con.execute(
        f"""
        SELECT dst_port, COUNT(*) AS cnt
        FROM logs
        WHERE proto_id = ? AND dst_port IN ({placeholders})
        GROUP BY dst_port
        ORDER BY cnt DESC
        """,
        [proto_id_udp, *TCP_ONLY_PORTS],
    ).fetchall()
    udp_on_tcp_total = sum(r[1] for r in udp_tcp_rows)
    udp_on_tcp_pct   = udp_on_tcp_total / max(total_c, 1)

    top_ip_pct = top_ips[0]["pct"] / 100.0 if top_ips else 0.0

    return {
        "total_conns":      total_c,
        "unique_src_ips":   unique_src,
        "unique_dst_ips":   unique_dst,
        "unique_dst_ports": unique_dports,
        "p2p_suspect_ips":  p2p_suspects,
        "avg_ports_per_ip": round(avg_ports, 1),
        "top_ip_pct":       round(top_ip_pct, 3),
        "port_ranges": {
            "well_known_pct": round(wk  / port_sum, 3),
            "registered_pct": round(reg / port_sum, 3),
            "ephemeral_pct":  round(eph / port_sum, 3),
        },
        "protocols": {
            "dns_pct":    round(dns_abs  / total_c, 4),
            "dns_conns":  dns_abs,
            "http_pct":   round(http_abs / total_c, 4),
            "http_conns": http_abs,
            "ntp_pct":    round(ntp_abs  / total_c, 4),
            "ntp_conns":  ntp_abs,
            "ssh_pct":    round(ssh_abs  / total_c, 4),
            "ssh_conns":  ssh_abs,
            "rdp_conns":  rdp_abs,
            "email_conns": email_abs,
        },
        "ntp_detail":  ntp_detail,
        "dns_detail":  dns_detail,
        "top_ips":     top_ips,
        "top_dst_ips": top_dst_ips,
        "top_dst_ports": top_ports,
        "top_talker":  tt_full,
        "flow_repetition": {
            "max_flow_count": max_flow_count,
            "top_flows":      top_flows,
        },
        "protocol_anomaly": {
            "udp_on_tcp_ports": udp_on_tcp_total,
            "udp_on_tcp_pct":   round(udp_on_tcp_pct, 4),
            "affected_ports":   [{"port": r[0], "count": r[1]} for r in udp_tcp_rows],
        },
    }


def analyze_day(
    con: duckdb.DuckDBPyConnection,
    *,
    registry: IpRegistry | None = None,
    log_count: int | None = None,
    expected_count: float = 0.0,
    ratio: float = 0.0,
) -> tuple[str, str, Classification]:
    """
    Pipeline completo: stats → classify → narrative + JSON.
    Retorna (analysis_text, details_json, classification).
    """
    stats = collect_stats(con, registry=registry)
    classification = classify_traffic(stats)
    n = log_count if log_count is not None else stats.get("total_conns", 0)

    lines: list[str] = []
    if ratio > 0 and expected_count > 0:
        lines.append(f"Volume {ratio:.1f}x acima da média ({n:,} logs vs {expected_count:,.0f} esperados).")
    else:
        lines.append(f"Volume total: {n:,} conexões registradas.")

    pr = stats.get("port_ranges", {})
    lines.append(
        f"\nDistribuição de portas: "
        f"{pr.get('well_known_pct', 0)*100:.0f}% bem conhecidas / "
        f"{pr.get('registered_pct', 0)*100:.0f}% registradas / "
        f"{pr.get('ephemeral_pct', 0)*100:.0f}% efêmeras"
    )
    proto = stats.get("protocols", {})
    parts = []
    if proto.get("ntp_pct", 0) >= 0.005:
        parts.append(f"NTP {proto['ntp_conns']:,} ({proto['ntp_pct']*100:.1f}%)")
    if proto.get("dns_pct", 0) >= 0.01:
        parts.append(f"DNS {proto['dns_conns']:,} ({proto['dns_pct']*100:.1f}%)")
    if proto.get("http_pct", 0) >= 0.01:
        parts.append(f"HTTP/S {proto['http_conns']:,} ({proto['http_pct']*100:.1f}%)")
    if proto.get("ssh_pct", 0) >= 0.005:
        parts.append(f"SSH {proto['ssh_conns']:,} ({proto['ssh_pct']*100:.1f}%)")
    if parts:
        lines.append("Protocolos destacados: " + " | ".join(parts))

    flow = stats.get("flow_repetition", {})
    if flow.get("max_flow_count", 0) >= 1_000 and flow.get("top_flows"):
        tf = flow["top_flows"][0]
        svc_str = f" ({tf['service']})" if tf["service"] else ""
        lines.append(
            f"\n⚠  FLUXO REPETIDO: {tf['src_ip']} → {tf['dst_ip']}:"
            f"{tf['dst_port']}{svc_str} [{tf['proto']}] — "
            f"{flow['max_flow_count']:,} repetições"
        )

    pa = stats.get("protocol_anomaly", {})
    if pa.get("udp_on_tcp_ports", 0) >= 100:
        ports_str = ", ".join(
            f"{p['port']}({WELL_KNOWN_SERVICES.get(p['port'], '?')})"
            for p in pa.get("affected_ports", [])[:5]
        )
        lines.append(
            f"\n⚠  PROTOCOLO ANÔMALO: {pa['udp_on_tcp_ports']:,} UDP em portas TCP "
            f"({pa['udp_on_tcp_pct']*100:.2f}%) — portas: {ports_str}"
        )

    tt = stats.get("top_talker", {})
    if tt:
        profile_pt = {
            "distributed":  "distribuído (P2P/scan)",
            "concentrated": "concentrado (suspeito de abuso/ataque)",
            "mixed":        "misto",
        }
        lines.append(
            f"\nTop cliente {tt['ip']}: {tt['connections']:,} conexões "
            f"({tt['pct']}%) — {tt['dst_ports']:,} portas × "
            f"{tt['dst_ips']:,} IPs — perfil {profile_pt.get(tt.get('profile', ''), '')}"
        )

    lines.append(f"\nClassificação: {classification.category} (confiança: {classification.score*100:.0f}%)")
    for r in classification.reasons:
        lines.append(f"  • {r}")
    if classification.possible_causes:
        lines.append("Possíveis causas:")
        for c in classification.possible_causes[:3]:
            lines.append(f"  ◦ {c}")

    details = {
        **stats,
        "classification":         classification.category,
        "classification_score":   classification.score,
        "classification_reasons": classification.reasons,
        "possible_causes":        classification.possible_causes,
    }
    return "\n".join(lines), json.dumps(details, ensure_ascii=False), classification
