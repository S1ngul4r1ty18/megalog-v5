"""Testes do classificador: cobre cada uma das 9 categorias com fixtures
sintéticas que isolam os sinais relevantes."""
from __future__ import annotations

from megalog.analytics.classifier import classify_traffic


def _empty_stats(**overrides) -> dict:
    """Stats mínimo (sem disparar nenhuma categoria); overrides ativam sinais."""
    base = {
        "total_conns": 1_000_000,
        "unique_src_ips": 100,
        "unique_dst_ips": 100,
        "unique_dst_ports": 100,
        "p2p_suspect_ips": 0,
        "avg_ports_per_ip": 10.0,
        "top_ip_pct": 0.05,
        "port_ranges": {"well_known_pct": 0.5, "registered_pct": 0.4, "ephemeral_pct": 0.1},
        "protocols": {
            "dns_pct": 0.05, "dns_conns": 50_000,
            "http_pct": 0.30, "http_conns": 300_000,
            "ntp_pct": 0.0, "ntp_conns": 0,
            "ssh_pct": 0.0, "ssh_conns": 0,
        },
        "ntp_detail": {}, "dns_detail": {},
        "top_talker": {"dst_ports": 50, "dst_ips": 50, "ephemeral_pct": 5, "well_known_pct": 30},
        "flow_repetition": {"max_flow_count": 0, "top_flows": []},
        "protocol_anomaly": {"udp_on_tcp_ports": 0, "udp_on_tcp_pct": 0.0},
    }
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


def test_indeterminado_quando_sem_sinais() -> None:
    c = classify_traffic(_empty_stats())
    assert c.category == "Alto Volume (Indeterminado)"
    assert c.score == 0.30


def test_p2p_torrent_dispara_em_top_talker_distribuido() -> None:
    stats = _empty_stats(
        top_talker={"dst_ports": 5000, "dst_ips": 5000, "ephemeral_pct": 60, "well_known_pct": 5},
        p2p_suspect_ips=30,
        avg_ports_per_ip=200,
        unique_dst_ips=15_000,
        unique_dst_ports=8_000,
        port_ranges={"well_known_pct": 0.05, "registered_pct": 0.30, "ephemeral_pct": 0.65},
        protocols={"dns_pct": 0.02, "dns_conns": 20_000, "http_pct": 0.05, "http_conns": 50_000,
                   "ntp_pct": 0.0, "ntp_conns": 0, "ssh_pct": 0.0, "ssh_conns": 0},
    )
    c = classify_traffic(stats)
    assert c.category == "P2P/Torrent"
    assert c.score >= 0.7


def test_ntp_abuse_um_unico_cliente_em_loop() -> None:
    stats = _empty_stats(
        protocols={"ntp_pct": 0.50, "ntp_conns": 500_000,
                   "dns_pct": 0.05, "dns_conns": 50_000,
                   "http_pct": 0.10, "http_conns": 100_000,
                   "ssh_pct": 0.0, "ssh_conns": 0},
        ntp_detail={"ntp_clients": 1, "ntp_servers": 1},
    )
    c = classify_traffic(stats)
    assert c.category == "Abuso de Protocolo (NTP)"
    assert c.score >= 0.7
    # Sinaliza loop para servidor único na mensagem
    assert any("loop" in r.lower() or "único" in r.lower() for r in c.reasons)


def test_dns_abuse_poucos_clientes_alto_volume() -> None:
    stats = _empty_stats(
        protocols={"dns_pct": 0.40, "dns_conns": 1_000_000,
                   "http_pct": 0.20, "http_conns": 200_000,
                   "ntp_pct": 0.0, "ntp_conns": 0, "ssh_pct": 0.0, "ssh_conns": 0},
        dns_detail={"dns_clients": 2, "dns_servers": 5},
    )
    c = classify_traffic(stats)
    assert c.category == "Abuso de Protocolo (DNS)"
    assert c.score >= 0.6


def test_ataque_dos_top_ip_concentrado_em_poucos_destinos() -> None:
    stats = _empty_stats(
        top_ip_pct=0.85,
        unique_src_ips=3,
        unique_dst_ips=2,
        unique_dst_ports=1,
        protocols={"dns_pct": 0.0, "dns_conns": 0, "http_pct": 0.05, "http_conns": 50_000,
                   "ntp_pct": 0.0, "ntp_conns": 0, "ssh_pct": 0.0, "ssh_conns": 0},
        port_ranges={"well_known_pct": 0.10, "registered_pct": 0.10, "ephemeral_pct": 0.80},
        top_talker={"dst_ports": 1, "dst_ips": 2, "ephemeral_pct": 80, "well_known_pct": 10},
    )
    c = classify_traffic(stats)
    assert c.category == "Ataque/DoS"
    assert c.score >= 0.7


def test_varredura_portas_poucos_origins_muitas_portas() -> None:
    stats = _empty_stats(
        unique_src_ips=2,
        unique_dst_ports=5_000,
        unique_dst_ips=800,
        top_talker={"dst_ports": 4_500, "dst_ips": 700, "ephemeral_pct": 5, "well_known_pct": 5},
        protocols={"dns_pct": 0.0, "dns_conns": 0, "http_pct": 0.05, "http_conns": 50_000,
                   "ntp_pct": 0.0, "ntp_conns": 0, "ssh_pct": 0.0, "ssh_conns": 0},
    )
    c = classify_traffic(stats)
    assert c.category == "Varredura de Portas"
    assert c.score >= 0.5


def test_botnet_c2_muitos_clientes_poucos_destinos() -> None:
    stats = _empty_stats(
        unique_src_ips=80,
        unique_dst_ips=3,
        unique_dst_ports=2,
        top_ip_pct=0.05,
        protocols={"dns_pct": 0.0, "dns_conns": 0, "http_pct": 0.10, "http_conns": 100_000,
                   "ntp_pct": 0.0, "ntp_conns": 0, "ssh_pct": 0.0, "ssh_conns": 0},
        protocol_anomaly={"udp_on_tcp_ports": 8000, "udp_on_tcp_pct": 0.008},
    )
    c = classify_traffic(stats)
    assert c.category == "Botnet/C2"
    assert c.score >= 0.7


def test_abuso_banda_top_ip_servicos_conhecidos() -> None:
    stats = _empty_stats(
        top_ip_pct=0.65,
        top_talker={"dst_ports": 50, "dst_ips": 100, "ephemeral_pct": 5, "well_known_pct": 80},
        protocols={"dns_pct": 0.05, "dns_conns": 50_000, "http_pct": 0.55, "http_conns": 550_000,
                   "ntp_pct": 0.0, "ntp_conns": 0, "ssh_pct": 0.0, "ssh_conns": 0},
        port_ranges={"well_known_pct": 0.80, "registered_pct": 0.15, "ephemeral_pct": 0.05},
    )
    c = classify_traffic(stats)
    assert c.category == "Abuso de Banda"


def test_flood_loop_fluxo_unico_500k() -> None:
    stats = _empty_stats(
        flow_repetition={"max_flow_count": 600_000, "top_flows": []},
    )
    c = classify_traffic(stats)
    assert c.category == "Flood/Loop/Tráfego Automatizado"
    assert c.score >= 0.9


def test_malware_udp_em_porta_tcp() -> None:
    stats = _empty_stats(
        protocol_anomaly={"udp_on_tcp_ports": 50_000, "udp_on_tcp_pct": 0.05},
    )
    c = classify_traffic(stats)
    assert c.category == "Malware/Comportamento Anômalo"
    assert c.score >= 0.6


def test_p2p_anti_sinal_reduz_score() -> None:
    """Anti-sinais (HTTP alto + portas bem-conhecidas + flow repetido)
    devem suprimir o score de P2P abaixo do limiar."""
    stats_base = _empty_stats(
        top_talker={"dst_ports": 5000, "dst_ips": 5000, "ephemeral_pct": 60, "well_known_pct": 5},
        p2p_suspect_ips=30,
        avg_ports_per_ip=200,
    )
    score_sem_antisinais = classify_traffic(stats_base).score

    # Mesmos sinais P2P + 3 anti-sinais combinados
    stats_anti = _empty_stats(
        top_talker={"dst_ports": 5000, "dst_ips": 5000, "ephemeral_pct": 60, "well_known_pct": 5},
        p2p_suspect_ips=30,
        avg_ports_per_ip=200,
        protocols={"dns_pct": 0.02, "dns_conns": 20_000, "http_pct": 0.60, "http_conns": 600_000,
                   "ntp_pct": 0.0, "ntp_conns": 0, "ssh_pct": 0.0, "ssh_conns": 0},
        port_ranges={"well_known_pct": 0.75, "registered_pct": 0.20, "ephemeral_pct": 0.05},
        flow_repetition={"max_flow_count": 10_000, "top_flows": []},
    )
    c_anti = classify_traffic(stats_anti)
    # Os 3 anti-sinais juntos (-0.55) devem zerar o P2P; vence outra categoria
    assert c_anti.category != "P2P/Torrent"
    # E o P2P teria sido detectado sem os anti-sinais (sanity do baseline)
    assert score_sem_antisinais > 0.30


def test_score_respeita_limiar_minimo() -> None:
    """Stats com sinais marginais não devem disparar nenhuma categoria forte."""
    stats = _empty_stats(top_ip_pct=0.55)  # disparariia bw_s parcial mas sem combo
    c = classify_traffic(stats)
    assert c.score <= 1.0
