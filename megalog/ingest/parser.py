"""Parser de syslog Mikrotik.

As regex e o classificador de timestamp/body são copiados verbatim
de processor_service.py:49-267 (v4) — é código já validado em produção,
não há motivo para reescrever a heurística. O que muda é a estrutura
de retorno (dataclass `ParsedLine` em vez de dict) e a remoção de
qualquer side-effect (este módulo é puro: linha entra, ParsedLine ou
None sai).

Resolução de IDs (interfaces, proto, conn_state, IPs) e gravação no
banco são responsabilidade de outras camadas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime


# ── Timestamp formats ─────────────────────────────────────────────────────────

# Formato A: 2026-03-17 16:44:49
RE_TS_A = re.compile(
    r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+(.+)$"
)

# Formato B: Mar 17 16:39:52 HOSTNAME [<PRI>Mar 17 16:39:52 HOSTNAME] PREFIX:
RE_TS_B = re.compile(
    r"^(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+\S+"
    r"(?:\s+<\d+>(?:\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+\S+)?"
    r"\s+(.+)$"
)

# Formato C: HH:MM:SS (apenas hora)
RE_TS_C = re.compile(
    r"^\s*(\d{2}:\d{2}:\d{2})\s+(.+)$"
)

# Formato D: <PRI>Mon DD HH:MM:SS HOSTNAME MSG (RFC3164 com prioridade)
RE_TS_D = re.compile(
    r"^<\d+>(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+\S+\s+(.+)$"
)

# Linha de continuação Mikrotik (IP:PORT quebrado entre dois pacotes UDP)
RE_CONTINUATION = re.compile(r"^\s*(\d{1,3})?:\d+[,\s\)]")

# Limpeza de prefixo: LOG_NAT:, CGNAT:, firewall,info, etc.
RE_STRIP_PREFIX = re.compile(
    r"^(?:(?:[\w\-]+:)\s*)?"
    r"(?:firewall,\w+(?:,\w+)?\s+)?"
    r"(forward:\s+.+)$",
    re.IGNORECASE,
)

RE_SYSTEM_LOG = re.compile(
    r"^system,(\w+(?:,\w+)*)\s+(.+)$",
    re.IGNORECASE,
)

# Pattern principal NAT — captura 19 grupos
RE_FORWARD_NAT = re.compile(
    r"forward:\s+"
    r"in:(.+?)\s+out:([^,]+),"
    r"(?:\s*connection-mark:(\S+))?"
    r"\s*connection-state:([^,\s]+)"
    r"(,snat)?"
    r"(?:\s+src-mac\s+([0-9a-fA-F:]{11,17}))?"
    r",?\s*proto\s+(\w+)"
    r"(?:\s+\(([^)]+)\))?"
    r",\s*(\d+\.\d+\.\d+\.\d+):(\d+)"
    r"->(\d+\.\d+\.\d+\.\d+):(\d+)"
    r",\s*NAT\s+\("
    r"(\d+\.\d+\.\d+\.\d+):(\d+)"
    r"->(\d+\.\d+\.\d+\.\d+):(\d+)"
    r"\)->(\d+\.\d+\.\d+\.\d+):(\d+)"
    r",\s*len\s+(\d+)",
    re.IGNORECASE,
)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


# ── Output dataclass ──────────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class ParsedLine:
    """Resultado do parser — só dados primitivos, sem IDs resolvidos."""
    ts: int
    in_iface: str
    out_iface: str
    conn_mark: str | None
    conn_state: str
    has_snat: bool
    src_mac: str | None
    proto: str
    tcp_flags: str | None
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    nat_ip: str  # nat_trans_ip = IP público pós-CGNAT (auditoria)
    nat_port: int
    pkt_len: int
    log_type: str = "nat"


# ── Funções puras ─────────────────────────────────────────────────────────────


def is_continuation(line: str) -> bool:
    """True se a linha começa com um padrão de IP:PORT quebrado."""
    return bool(RE_CONTINUATION.match(line))


def extract_ts_and_body(raw_line: str) -> tuple[str | None, str | None, str | None]:
    """Extrai (ts_str, body, format_letter) ou (None, None, None)."""
    line = raw_line.strip()
    if not line:
        return None, None, None
    if m := RE_TS_A.match(line):
        return m.group(1), m.group(2).strip(), "A"
    if m := RE_TS_B.match(line):
        return m.group(1), m.group(2).strip(), "B"
    if m := RE_TS_C.match(line):
        return m.group(1), m.group(2).strip(), "C"
    if m := RE_TS_D.match(line):
        return m.group(1), m.group(2).strip(), "D"
    return None, None, None


def parse_ts(ts_str: str, *, now: datetime | None = None) -> datetime:
    """
    Converte ts_str em datetime. Heurística para formatos sem ano:
    se a data parece estar mais de 25h no futuro, recua 1 ano.
    `now` injetável para testes.
    """
    now = now or datetime.now()
    ts_str = ts_str.strip()

    # Formato A — completo
    if len(ts_str) == 19 and ts_str[4] == "-":
        try:
            return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    # Formato B/D — Mon DD HH:MM:SS
    if m := re.match(r"^(\w{3})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})$", ts_str):
        mon = MONTHS.get(m.group(1).lower())
        if mon:
            day = int(m.group(2))
            try:
                dt = datetime.strptime(
                    f"{now.year}-{mon:02d}-{day:02d} {m.group(3)}",
                    "%Y-%m-%d %H:%M:%S",
                )
                if (dt - now).total_seconds() > 25 * 3600:
                    dt = dt.replace(year=now.year - 1)
                return dt
            except ValueError:
                pass

    # Formato C — só hora, herda data de hoje
    if m := re.match(r"^(\d{2}:\d{2}:\d{2})$", ts_str):
        return datetime.strptime(
            f"{now.strftime('%Y-%m-%d')} {m.group(1)}",
            "%Y-%m-%d %H:%M:%S",
        )

    return now


def normalize_body(body: str) -> tuple[str | None, str]:
    """Remove prefixo LOG_NAT:/CGNAT:/etc., devolve (corpo, log_type)."""
    if not body:
        return None, "other"
    body = re.sub(r"^[A-Z][A-Z0-9_\-]+:\s*", "", body)
    if m := RE_STRIP_PREFIX.match(body):
        return m.group(1), "nat"
    if body.lower().startswith("forward:"):
        return body, "nat"
    if RE_SYSTEM_LOG.match(body):
        return body, "system"
    return body, "other"


def parse_line(raw_line: str, *, now: datetime | None = None) -> ParsedLine | None:
    """
    Parser end-to-end de uma linha já joinada (sem continuation).
    Retorna ParsedLine se for NAT válido, None caso contrário.
    """
    ts_str, body, _fmt = extract_ts_and_body(raw_line)
    if not ts_str or not body:
        return None
    norm_body, log_type = normalize_body(body)
    if log_type != "nat" or not norm_body:
        return None
    m = RE_FORWARD_NAT.search(norm_body)
    if not m:
        return None
    (in_iface, out_iface, conn_mark, conn_state, has_snat_flag,
     src_mac, proto, tcp_flags,
     src_ip, src_port, dst_ip, dst_port,
     _nat_orig_ip, _nat_orig_port, nat_trans_ip, nat_trans_port,
     _final_dst_ip, _final_dst_port, pkt_len) = m.groups()

    dt = parse_ts(ts_str, now=now)
    return ParsedLine(
        ts=int(dt.timestamp()),
        in_iface=in_iface.strip(),
        out_iface=out_iface.strip(),
        conn_mark=conn_mark,
        conn_state=conn_state.lower(),
        has_snat=bool(has_snat_flag),
        src_mac=src_mac,
        proto=proto.upper(),
        tcp_flags=tcp_flags,
        src_ip=src_ip,
        src_port=int(src_port),
        dst_ip=dst_ip,
        dst_port=int(dst_port),
        nat_ip=nat_trans_ip,
        nat_port=int(nat_trans_port),
        pkt_len=int(pkt_len),
    )


def join_continuations(lines):
    """
    Generator: junta linhas de continuação Mikrotik em uma única lógica.
    Aceita iterable[str]; emite str (linha completa).
    """
    pending = ""
    for line in lines:
        line = line.rstrip("\n\r")
        if is_continuation(line) and pending:
            pending += line.lstrip()
            continue
        if pending:
            yield pending
        pending = line
    if pending:
        yield pending
