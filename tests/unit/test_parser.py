"""Testes do parser — cobre 4 formatos de timestamp + line continuation +
edge cases vistos em produção (interfaces com espaço, IPs CGNAT)."""
from __future__ import annotations

from datetime import datetime

import pytest

from megalog.ingest.parser import (
    extract_ts_and_body,
    is_continuation,
    join_continuations,
    normalize_body,
    parse_line,
    parse_ts,
)


# ── Linhas reais (sintetizadas a partir dos formatos vistos no v4) ────────────

LINE_FORMAT_A = (
    "2026-04-20 10:15:32 firewall,info forward: in:VLAN160-PE01 out:bridge1,"
    "connection-state:new,snat src-mac aa:bb:cc:dd:ee:ff, proto TCP (SYN), "
    "100.80.0.119:51555->8.8.8.8:443, NAT "
    "(100.80.0.119:51555->170.245.175.121:51555)->8.8.8.8:443, len 60"
)

LINE_FORMAT_B = (
    "Apr 20 10:15:32 RB5K9-PE01 LOG_NAT: forward: in:ether4 out:ether2,"
    "connection-state:new, proto UDP, "
    "192.168.130.61:53123->1.1.1.1:53, NAT "
    "(192.168.130.61:53123->170.245.175.121:53123)->1.1.1.1:53, len 78"
)

LINE_FORMAT_C = (
    " 10:15:32 CGNAT: forward: in:VLAN150-SW out:ether7,"
    "connection-state:new, proto TCP, "
    "100.70.1.252:12345->172.217.0.1:443, NAT "
    "(100.70.1.252:12345->170.245.175.121:12345)->172.217.0.1:443, len 56"
)

LINE_FORMAT_D = (
    "<30>Apr 20 10:15:32 RB5K9-PIN forward: in:VLAN P2P out:ether3,"
    "connection-state:new, proto UDP, "
    "100.80.0.99:44879->172.217.172.36:443, NAT "
    "(100.80.0.99:44879->170.245.175.121:44879)->172.217.172.36:443, len 56"
)

# Continuation: a quebra Mikrotik fica DENTRO de um IP:PORT,
# deixando a linha 2 começando com ":porta," (regex RE_CONTINUATION).
LINE_BROKEN_PART_A = (
    "2026-04-20 10:15:32 firewall,info forward: in:bridge1 out:ether2,"
    "connection-state:new, proto TCP, 192.168.1.100:45231->8.8.8.8"
)
LINE_BROKEN_PART_B = (
    ":443, NAT (192.168.1.100:45231->170.245.175.121:45231)"
    "->8.8.8.8:443, len 60"
)


# ── extract_ts_and_body ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "line,expected_fmt,expected_ts_prefix",
    [
        (LINE_FORMAT_A, "A", "2026-04-20"),
        (LINE_FORMAT_B, "B", "Apr 20"),
        (LINE_FORMAT_C, "C", "10:15:32"),
        (LINE_FORMAT_D, "D", "Apr 20"),
    ],
)
def test_format_detection(line: str, expected_fmt: str, expected_ts_prefix: str) -> None:
    ts, body, fmt = extract_ts_and_body(line)
    assert fmt == expected_fmt
    assert ts is not None and ts.startswith(expected_ts_prefix)
    assert body and "forward:" in body.lower()


def test_empty_line_is_rejected() -> None:
    assert extract_ts_and_body("") == (None, None, None)
    assert extract_ts_and_body("   \n") == (None, None, None)


def test_garbage_line_is_rejected() -> None:
    assert extract_ts_and_body("nada-disso-é-syslog") == (None, None, None)


# ── parse_ts heuristic ───────────────────────────────────────────────────────


def test_parse_ts_full_format_a() -> None:
    dt = parse_ts("2026-04-20 10:15:32")
    assert dt == datetime(2026, 4, 20, 10, 15, 32)


def test_parse_ts_format_bd_uses_current_year() -> None:
    fixed_now = datetime(2026, 4, 20, 12, 0, 0)
    dt = parse_ts("Apr 20 10:15:32", now=fixed_now)
    assert dt == datetime(2026, 4, 20, 10, 15, 32)


def test_parse_ts_format_bd_recoils_one_year_when_too_far_future() -> None:
    """Log de 'Dec 31 23:00' chegando em 'Jan 02' deve cair no ano anterior."""
    fixed_now = datetime(2026, 1, 2, 0, 0, 0)
    dt = parse_ts("Dec 31 23:00:00", now=fixed_now)
    assert dt == datetime(2025, 12, 31, 23, 0, 0)


def test_parse_ts_format_c_inherits_today() -> None:
    fixed_now = datetime(2026, 4, 20, 0, 0, 0)
    dt = parse_ts("10:15:32", now=fixed_now)
    assert dt == datetime(2026, 4, 20, 10, 15, 32)


# ── normalize_body ───────────────────────────────────────────────────────────


def test_normalize_body_strips_log_nat_prefix() -> None:
    body, kind = normalize_body("LOG_NAT: forward: in:ether1 out:ether2, ...")
    assert kind == "nat"
    assert body and body.startswith("forward:")


def test_normalize_body_handles_firewall_info() -> None:
    body, kind = normalize_body("firewall,info forward: in:ether1 out:ether2")
    assert kind == "nat"
    assert body and body.startswith("forward:")


def test_normalize_body_unknown_returns_other() -> None:
    body, kind = normalize_body("topics,info: random message")
    # 'topics' começa com minúscula, não casa o regex de prefixo;
    # tampouco é forward/system → cai em 'other'
    assert kind == "other"
    assert body is not None


# ── parse_line — end-to-end por formato ───────────────────────────────────────


@pytest.mark.parametrize("line", [LINE_FORMAT_A, LINE_FORMAT_B, LINE_FORMAT_C, LINE_FORMAT_D])
def test_parse_line_extracts_nat_fields(line: str) -> None:
    parsed = parse_line(line, now=datetime(2026, 4, 20, 12, 0, 0))
    assert parsed is not None
    assert parsed.proto in ("TCP", "UDP")
    assert parsed.src_ip.count(".") == 3
    assert parsed.nat_ip.count(".") == 3
    assert parsed.nat_ip == "170.245.175.121"  # IP público pós-CGNAT
    assert parsed.dst_port > 0
    assert parsed.pkt_len > 0
    assert parsed.log_type == "nat"


def test_parse_line_preserves_interface_with_space() -> None:
    """Interface 'VLAN P2P' (com espaço) é caso real."""
    parsed = parse_line(LINE_FORMAT_D, now=datetime(2026, 4, 20, 12, 0, 0))
    assert parsed is not None
    assert parsed.in_iface == "VLAN P2P"
    assert parsed.out_iface == "ether3"


def test_parse_line_returns_none_for_garbage() -> None:
    assert parse_line("isso não é syslog Mikrotik") is None


def test_parse_line_returns_none_for_non_nat_forward() -> None:
    # forward: que não bate o RE_FORWARD_NAT (sem NAT(...))
    line = "2026-04-20 10:15:32 firewall,info forward: in:ether1 out:ether2 (sem NAT)"
    assert parse_line(line) is None


# ── Continuation handling ─────────────────────────────────────────────────────


def test_is_continuation_recognizes_broken_ip_port() -> None:
    # Quebra Mikrotik deixa a continuação começando com ":port," ou "<resto>:port,"
    assert is_continuation(":443, NAT (...)")
    assert is_continuation("1:443, NAT (...)")
    assert is_continuation(" :443 NAT (...)")
    assert not is_continuation("Apr 20 10:15:32 RB5K9 LOG_NAT: forward: ...")
    assert not is_continuation("forward: in:ether1 proto=TCP")


def test_join_continuations_merges_two_lines() -> None:
    joined = list(join_continuations([LINE_BROKEN_PART_A, LINE_BROKEN_PART_B]))
    assert len(joined) == 1
    full = joined[0]
    assert "192.168.1.100:45231" in full  # porta reconstituída
    assert "8.8.8.8:443" in full


def test_join_continuations_emits_complete_lines_unmodified() -> None:
    joined = list(join_continuations([LINE_FORMAT_A, LINE_FORMAT_B]))
    assert joined == [LINE_FORMAT_A, LINE_FORMAT_B]


def test_parse_line_after_join() -> None:
    """E2E: linhas quebradas são parseáveis após join."""
    [joined] = list(join_continuations([LINE_BROKEN_PART_A, LINE_BROKEN_PART_B]))
    parsed = parse_line(joined, now=datetime(2026, 4, 20, 12, 0, 0))
    assert parsed is not None
    assert parsed.src_ip == "192.168.1.100"
    assert parsed.src_port == 45231
    assert parsed.dst_ip == "8.8.8.8"
    assert parsed.dst_port == 443
    assert parsed.nat_ip == "170.245.175.121"
    assert parsed.nat_port == 45231
