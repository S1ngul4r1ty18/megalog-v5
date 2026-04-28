"""Mapa de portas → nome de serviço bem conhecido (IANA + uso comum)."""
from __future__ import annotations

WELL_KNOWN_SERVICES: dict[int, str] = {
    20: "FTP-data", 21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP",
    53: "DNS", 67: "DHCP", 80: "HTTP", 110: "POP3", 123: "NTP",
    143: "IMAP", 161: "SNMP", 179: "BGP", 443: "HTTPS", 465: "SMTPS",
    587: "SMTP/Sub", 993: "IMAPS", 995: "POP3S", 3389: "RDP",
    5060: "SIP", 6881: "BitTorrent", 8080: "HTTP-alt", 8443: "HTTPS-alt",
}

# Portas que existem em IANA exclusivamente como TCP — UDP ali é incoerente
TCP_ONLY_PORTS: tuple[int, ...] = (21, 22, 23, 25, 110, 143, 465, 587, 993, 995)

# Categorização de faixas (RFC 6335)
def port_category(port: int) -> str:
    if port <= 1023:
        return "well_known"
    if port <= 49151:
        return "registered"
    return "ephemeral"
