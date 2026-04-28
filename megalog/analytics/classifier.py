"""Classificador multi-sinal de tráfego anômalo — 9 categorias.

Portado verbatim de [compress_old_dbs.py:341-740](../../../megalog/compress_old_dbs.py#L341-L740)
do MegaLog v4. É código pura-Python que opera sobre um dict de estatísticas
(montado pelo `anomaly_analyzer`); thresholds e heurísticas inalterados.

Categorias (em ordem alfabética de variável interna):
  - P2P/Torrent
  - Abuso de Protocolo (NTP)
  - Abuso de Protocolo (DNS)
  - Ataque/DoS
  - Varredura de Portas
  - Botnet/C2
  - Abuso de Banda
  - Flood/Loop/Tráfego Automatizado
  - Malware/Comportamento Anômalo

Retorna sempre uma classificação. Se nenhuma categoria atinge score>=0.30:
"Alto Volume (Indeterminado)".
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class Classification:
    category: str
    score: float            # 0.0 – 1.0
    reasons: list[str]
    possible_causes: list[str]


def classify_traffic(stats: dict) -> Classification:
    """
    Operação determinística sobre `stats`. Estrutura esperada:
      stats = {
        'unique_src_ips':, 'unique_dst_ips':, 'unique_dst_ports':,
        'avg_ports_per_ip':, 'top_ip_pct':, 'total_conns':, 'p2p_suspect_ips':,
        'port_ranges': {'well_known_pct':, 'registered_pct':, 'ephemeral_pct':},
        'protocols':   {'dns_conns':, 'dns_pct':, 'ntp_conns':, 'ntp_pct':,
                        'http_pct':, 'ssh_pct':, ...},
        'ntp_detail':  {'ntp_clients':, 'ntp_servers':},
        'dns_detail':  {'dns_clients':, 'dns_servers':},
        'top_talker':  {'dst_ports':, 'dst_ips':, 'ephemeral_pct':, 'well_known_pct':},
        'flow_repetition':  {'max_flow_count':},
        'protocol_anomaly': {'udp_on_tcp_ports':, 'udp_on_tcp_pct':},
      }
    """
    unique_src    = stats.get("unique_src_ips", 1)
    unique_dips   = stats.get("unique_dst_ips", 0)
    unique_dports = stats.get("unique_dst_ports", 0)
    avg_ports     = stats.get("avg_ports_per_ip", 0.0)
    top_pct       = stats.get("top_ip_pct", 0.0)
    total         = max(stats.get("total_conns", 1), 1)
    p2p_suspects  = stats.get("p2p_suspect_ips", 0)

    pr       = stats.get("port_ranges", {})
    wk_pct   = pr.get("well_known_pct", 0.0)
    reg_pct  = pr.get("registered_pct", 0.0)
    eph_pct  = pr.get("ephemeral_pct", 0.0)

    proto    = stats.get("protocols", {})
    ntp_abs  = proto.get("ntp_conns", 0)
    ntp_pct  = proto.get("ntp_pct", 0.0)
    dns_abs  = proto.get("dns_conns", 0)
    dns_pct  = proto.get("dns_pct", 0.0)
    http_pct = proto.get("http_pct", 0.0)
    ssh_pct  = proto.get("ssh_pct", 0.0)

    ntp_det     = stats.get("ntp_detail", {})
    ntp_clients = ntp_det.get("ntp_clients", 0)
    ntp_servers = ntp_det.get("ntp_servers", 0)

    dns_det     = stats.get("dns_detail", {})
    dns_clients = dns_det.get("dns_clients", 0)
    dns_servers = dns_det.get("dns_servers", 0)

    tt          = stats.get("top_talker", {})
    tt_ports    = tt.get("dst_ports", 0)
    tt_dips     = tt.get("dst_ips", 0)
    tt_eph_pct  = tt.get("ephemeral_pct", 0.0)
    tt_wk_pct   = tt.get("well_known_pct", 0.0)

    flow_rep      = stats.get("flow_repetition", {})
    max_flow      = flow_rep.get("max_flow_count", 0)

    proto_anom    = stats.get("protocol_anomaly", {})
    udp_tcp_total = proto_anom.get("udp_on_tcp_ports", 0)
    udp_tcp_pct   = proto_anom.get("udp_on_tcp_pct", 0.0)

    candidates: dict[str, tuple[float, list[str], list[str]]] = {}

    # ── A) P2P / Torrent ──────────────────────────────────────────────────────
    p2p_s, p2p_r = 0.0, []
    if tt_ports >= 2000 and tt_dips >= 2000:
        p2p_s += 0.30
        p2p_r.append(f"Cliente principal: {tt_ports:,} portas × {tt_dips:,} IPs destino (padrão mesh P2P)")
    elif tt_ports >= 500 and tt_dips >= 500:
        p2p_s += 0.15

    if p2p_suspects >= 20 and avg_ports >= 100:
        p2p_s += 0.30
        p2p_r.append(f"{p2p_suspects} clientes com >50 portas distintas e média {avg_ports:.0f} portas/IP")
    elif p2p_suspects >= 5 and avg_ports >= 50:
        p2p_s += 0.15
        p2p_r.append(f"{p2p_suspects} clientes com alta dispersão de portas (média {avg_ports:.0f}/IP)")

    if eph_pct >= 0.30 and unique_dports >= 3000:
        p2p_s += 0.20
        p2p_r.append(f"{eph_pct*100:.0f}% conexões em portas efêmeras ({unique_dports:,} portas distintas)")
    elif (reg_pct + eph_pct) >= 0.70 and unique_dports >= 1500:
        p2p_s += 0.10

    if unique_dips >= 10000:
        p2p_s += 0.15
        p2p_r.append(f"{unique_dips:,} IPs destino distintos (típico de swarm BitTorrent)")
    elif unique_dips >= 3000 and p2p_s >= 0.15:
        p2p_s += 0.08

    if http_pct < 0.10 and dns_pct < 0.20 and p2p_s >= 0.15:
        p2p_s += 0.10
        p2p_r.append(f"Baixo tráfego web ({http_pct*100:.1f}% HTTP/S, {dns_pct*100:.1f}% DNS) — incomum para navegação")

    if http_pct >= 0.50:
        p2p_s = max(p2p_s - 0.20, 0.0)
    if wk_pct >= 0.70:
        p2p_s = max(p2p_s - 0.15, 0.0)
    if max_flow >= 5_000:
        p2p_s = max(p2p_s - 0.20, 0.0)

    if p2p_s >= 0.30:
        candidates["P2P/Torrent"] = (
            min(p2p_s, 1.0), p2p_r,
            ["Clientes com torrent ativo (qBittorrent, uTorrent, Transmission)",
             "Aplicativos de compartilhamento P2P (eMule, DC++)",
             "Jogos online com modo peer-to-peer",
             "IPFS ou aplicativos descentralizados"],
        )

    # ── B) Abuso de Protocolo — NTP (123) ─────────────────────────────────────
    ntp_s, ntp_r = 0.0, []
    if ntp_abs >= 100_000 and ntp_pct >= 0.02:
        if ntp_clients <= 3:
            ntp_s += 0.70
            ntp_r.append(f"{ntp_clients} cliente(s) gerando {ntp_abs:,} queries NTP ({ntp_pct*100:.1f}% do tráfego)")
            if ntp_clients == 1:
                ntp_r.append("Um único IP em loop de NTP — equipamento com daemon mal configurado")
        elif ntp_clients <= 10:
            ntp_s += 0.50
            ntp_r.append(f"{ntp_clients} clientes com NTP anômalo: {ntp_abs:,} queries ({ntp_pct*100:.1f}%)")

        if ntp_servers >= 50:
            ntp_s += 0.15
            ntp_r.append(f"Consultando {ntp_servers} servidores NTP distintos (possível reconnaissance)")
        elif ntp_servers <= 3 and ntp_clients <= 3:
            ntp_r.append(f"Loop para {ntp_servers} servidor(es) NTP fixo(s) — provável misconfiguration")

    if ntp_s >= 0.30:
        candidates["Abuso de Protocolo (NTP)"] = (
            min(ntp_s, 1.0), ntp_r,
            ["Equipamento com firmware defeituoso em loop de NTP (TV Box, roteador, ONT)",
             "Daemon ntpd ou chrony com configuração inválida causando loop",
             "Participante de ataque de amplificação NTP (NTP monlist exploit)",
             "Script/aplicativo sem backoff fazendo polling excessivo"],
        )

    # ── C) Abuso de Protocolo — DNS (53) ──────────────────────────────────────
    dns_s, dns_r = 0.0, []
    if dns_abs >= 500_000 and dns_pct >= 0.15:
        if dns_clients <= 5:
            dns_s += 0.60
            dns_r.append(f"{dns_clients} cliente(s) gerando {dns_abs:,} queries DNS ({dns_pct*100:.1f}%)")
        elif dns_servers <= 3 and dns_clients >= 20:
            dns_s += 0.55
            dns_r.append(f"{dns_clients} clientes enviando tudo para {dns_servers} servidor(es) DNS")

        if dns_servers >= 100 and dns_clients <= 5:
            dns_s += 0.15
            dns_r.append(f"{dns_servers} servidores DNS distintos contactados (possível tunneling)")

    if dns_s >= 0.30:
        candidates["Abuso de Protocolo (DNS)"] = (
            min(dns_s, 1.0), dns_r,
            ["Malware usando DNS tunneling para exfiltração de dados",
             "Resolver recursivo aberto mal configurado em loop",
             "Ataque de amplificação DNS",
             "Aplicativo com retry agressivo em falha de resolução"],
        )

    # ── D) Ataque / DoS ───────────────────────────────────────────────────────
    dos_s, dos_r = 0.0, []
    if top_pct >= 0.80 and unique_dips <= 10:
        dos_s += 0.55
        dos_r.append(f"IP principal: {top_pct*100:.0f}% do tráfego para apenas {unique_dips} destino(s)")
    elif top_pct >= 0.60 and unique_dips <= 20:
        dos_s += 0.35
        dos_r.append(f"IP principal: {top_pct*100:.0f}% do tráfego, {unique_dips} destinos")

    if unique_src <= 5 and unique_dips <= 5:
        dos_s += 0.25
        dos_r.append(f"Fluxo concentrado: {unique_src} origem(ns) → {unique_dips} destino(s)")

    if unique_dports <= 5 and dos_s >= 0.25:
        dos_s += 0.15
        dos_r.append(f"Apenas {unique_dports} porta(s) distintas — flood direcionado")

    if unique_dips >= 1000:
        dos_s = max(dos_s - 0.30, 0.0)
    if p2p_suspects >= 10:
        dos_s = max(dos_s - 0.15, 0.0)

    if dos_s >= 0.30:
        candidates["Ataque/DoS"] = (
            min(dos_s, 1.0), dos_r,
            ["Máquina comprometida participando de ataque DDoS distribuído",
             "SYN flood, UDP flood ou ICMP flood",
             "Teste de carga/stress não autorizado",
             "Malware gerando tráfego de ataque como serviço"],
        )

    # ── E) Varredura de Portas ────────────────────────────────────────────────
    scan_s, scan_r = 0.0, []
    if unique_src <= 10 and unique_dports >= 1000:
        scan_s += 0.55
        scan_r.append(f"{unique_src} IP(s) origem varrendo {unique_dports:,} portas distintas")
    elif unique_src <= 5 and unique_dports >= 500:
        scan_s += 0.40
        scan_r.append(f"{unique_src} IP(s) com {unique_dports:,} portas distintas")

    if unique_dips >= 500 and unique_src <= 10:
        scan_s += 0.20
        scan_r.append(f"Varrendo {unique_dips:,} IPs distintos")

    if unique_src >= 30:
        scan_s = max(scan_s - 0.25, 0.0)

    if scan_s >= 0.30:
        candidates["Varredura de Portas"] = (
            min(scan_s, 1.0), scan_r,
            ["Scanner de rede (Nmap, Masscan, Shodan crawler)",
             "Malware buscando alvos vulneráveis para propagação",
             "Worm de rede em fase de reconhecimento",
             "Auditoria de segurança autorizada (verificar com equipe)"],
        )

    # ── F) Botnet / C2 ────────────────────────────────────────────────────────
    bot_s, bot_r = 0.0, []
    if unique_src >= 30 and unique_dips <= 10 and unique_dports <= 5:
        bot_s += 0.70
        bot_r.append(
            f"{unique_src} clientes → {unique_dips} destino(s) em {unique_dports} "
            f"porta(s) — padrão C2 (Command & Control)"
        )
    elif unique_src >= 20 and unique_dips <= 15 and unique_dports <= 8:
        bot_s += 0.45
        bot_r.append(f"{unique_src} clientes concentrados em {unique_dips} destino(s)")

    if unique_src >= 30 and top_pct <= 0.15:
        bot_s += 0.15
        bot_r.append(f"Distribuição uniforme: nenhum cliente domina (top IP = {top_pct*100:.0f}%)")

    if udp_tcp_total >= 5_000 and bot_s >= 0.20:
        bot_s += 0.15
        bot_r.append(f"UDP em portas TCP ({udp_tcp_total:,} eventos) — possível C2 com evasão de protocolo")

    if bot_s >= 0.30:
        candidates["Botnet/C2"] = (
            min(bot_s, 1.0), bot_r,
            ["Dispositivos infectados por malware conectando a servidor C2",
             "RAT (Remote Access Trojan) ou backdoor ativo",
             "Dispositivos IoT comprometidos (câmeras, roteadores, smart TVs)",
             "Mineradores de criptomoeda conectando a pool centralizado"],
        )

    # ── G) Abuso de Banda ─────────────────────────────────────────────────────
    bw_s, bw_r = 0.0, []
    p2p_score_cur = candidates.get("P2P/Torrent", (0.0,))[0]
    dos_score_cur = candidates.get("Ataque/DoS",  (0.0,))[0]

    if top_pct >= 0.50 and p2p_score_cur < 0.40 and dos_score_cur < 0.35:
        if tt_wk_pct >= 30 and tt_ports < 500:
            bw_s += 0.50
            bw_r.append(f"Cliente {top_pct*100:.0f}% do tráfego com perfil de serviços conhecidos "
                        f"({tt_wk_pct:.0f}% portas bem conhecidas)")
        elif top_pct >= 0.40 and unique_src >= 3:
            bw_s += 0.35
            bw_r.append(f"Cliente dominante: {top_pct*100:.0f}% do volume total")

    if bw_s >= 0.30:
        candidates["Abuso de Banda"] = (
            min(bw_s, 1.0), bw_r,
            ["Streaming de vídeo em ultra HD (Netflix 4K, Disney+, YouTube Live)",
             "Download massivo sem limitação de velocidade (Steam, ISO grandes)",
             "Backup em nuvem sem throttling (iCloud, Google Drive, OneDrive)",
             "Servidor web/FTP/mídia residencial não declarado"],
        )

    # ── H) Flood / Loop / Tráfego Automatizado ────────────────────────────────
    flood_s, flood_r = 0.0, []
    if max_flow >= 500_000:
        flood_s += 0.90
        flood_r.append(f"Fluxo único repetido {max_flow:,} vezes — flood/loop crítico")
    elif max_flow >= 100_000:
        flood_s += 0.80
        flood_r.append(f"Fluxo único repetido {max_flow:,} vezes — padrão claro de loop ou flood")
    elif max_flow >= 10_000:
        flood_s += 0.65
        flood_r.append(f"Fluxo único repetido {max_flow:,} vezes — tráfego automatizado suspeito")
    elif max_flow >= 1_000:
        flood_s += 0.45
        flood_r.append(f"Fluxo único repetido {max_flow:,} vezes — repetição anômala (normal ≤ algumas dezenas)")

    if flood_s >= 0.30:
        candidates["Flood/Loop/Tráfego Automatizado"] = (
            min(flood_s, 1.0), flood_r,
            ["Equipamento em loop: TV Box, câmera IP ou roteador com firmware defeituoso",
             "Daemon (ntpd, chronyd, openvpn) com retry sem backoff em falha de rede",
             "Keepalive ultra-agressivo de aplicativo de monitoramento ou VoIP",
             "Máquina comprometida com malware fazendo beacon periódico ou flood"],
        )

    # ── I) Malware / Comportamento Anômalo ────────────────────────────────────
    malware_s, malware_r = 0.0, []
    if udp_tcp_total >= 10_000 and udp_tcp_pct >= 0.01:
        malware_s += 0.60
        malware_r.append(
            f"{udp_tcp_total:,} conexões UDP em portas tipicamente TCP "
            f"({udp_tcp_pct*100:.1f}% do tráfego) — protocolo incoerente"
        )
    elif udp_tcp_total >= 1_000:
        malware_s += 0.40
        malware_r.append(f"{udp_tcp_total:,} conexões UDP em portas exclusivas TCP")

    if malware_s >= 0.30 and flood_s >= 0.30:
        malware_s = min(malware_s + 0.20, 1.0)
        malware_r.append("Loop/flood + protocolo anômalo — combinação fortemente indicativa de malware")

    if malware_s >= 0.30:
        candidates["Malware/Comportamento Anômalo"] = (
            min(malware_s, 1.0), malware_r,
            ["Malware usando UDP para simular portas TCP (evasão de firewall)",
             "Equipamento com firmware comprometido ou com bug de protocolo",
             "Ferramenta de pen-test ou fuzzing enviando pacotes malformados",
             "Rootkit ou backdoor com transporte de protocolo não-padrão"],
        )

    # ── Selecionar melhor candidato ───────────────────────────────────────────
    best_cat = "Alto Volume (Indeterminado)"
    best_score = 0.30  # limiar mínimo
    best_r = ["Padrão não se enquadra claramente em nenhuma categoria"]
    best_causes = [
        "Atualização de SO em massa (Windows Update, apt/yum, App Store)",
        "Evento de streaming popular (futebol, show, lançamento)",
        "Sincronização de CDN ou espelho de repositório",
        "Aumento legítimo de usuários no período",
    ]
    for cat, (s, r, causes) in candidates.items():
        if s > best_score:
            best_score = s
            best_cat = cat
            best_r = r
            best_causes = causes

    return Classification(
        category=best_cat,
        score=round(best_score, 2),
        reasons=best_r,
        possible_causes=best_causes,
    )
