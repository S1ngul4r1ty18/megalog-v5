"""Integração end-to-end da Fase 1.

Cobre o caminho completo:
  receiver UDP → arquivo .raw → processor.tick() → DuckDB hot storage

Não dependemos de privilégios root: receiver bindando em porta efêmera local
no localhost. O processor é instanciado com Settings sobrescritas para
diretórios temporários.
"""
from __future__ import annotations

import asyncio
import socket
from datetime import date, datetime
from pathlib import Path

import duckdb
import pytest

from megalog.config import Settings
from megalog.ingest.parser import join_continuations, parse_line
from megalog.ingest.processor import Processor
from megalog.ingest.receiver import SyslogProtocol
from megalog.ingest.stream import HourlyStreamWriter

LINE_TEMPLATE = (
    "2026-04-20 10:15:{sec:02d} firewall,info forward: "
    "in:bridge1 out:ether2,connection-state:new,snat "
    "src-mac aa:bb:cc:dd:ee:ff, proto TCP, "
    "{src_ip}:{src_port}->8.8.8.8:443, NAT "
    "({src_ip}:{src_port}->170.245.175.121:{src_port})->8.8.8.8:443, len 60"
)


def _tmp_settings(tmp_path: Path) -> Settings:
    """Override Settings para escrita em diretório temporário."""
    return Settings(
        stream_dir=tmp_path / "stream",
        stream_processed_dir=tmp_path / "stream" / ".processed",
        hot_storage_dir=tmp_path / "hot",
        cold_storage_dir=tmp_path / "cold",
        state_dir=tmp_path / "state",
        ip_registry_path=tmp_path / "state" / "ip_registry.duckdb",
        batch_size=10,
        batch_flush_seconds=0.1,
        ip_cache_hot_top_n=100,
        ip_cache_max=1000,
        duckdb_threads=1,
    )


def test_processor_inserts_into_duckdb(tmp_path: Path) -> None:
    """Processor consome um .raw já populado e insere no DuckDB hot."""
    s = _tmp_settings(tmp_path)
    s.stream_dir.mkdir(parents=True, exist_ok=True)
    raw_file = s.stream_dir / "2026-04-20-10.raw"
    lines = [
        LINE_TEMPLATE.format(sec=i % 60, src_ip="100.80.0.119", src_port=51555 + i)
        for i in range(50)
    ]
    raw_file.write_text("\n".join(lines) + "\n")

    proc = Processor(settings=s)
    proc.tick()
    proc._flush(force=True)
    proc.store.checkpoint(date(2026, 4, 20))
    proc.store.close()
    proc.ip_reg.close()

    db_path = s.hot_storage_dir / "2026-04-20.duckdb"
    assert db_path.exists()
    con = duckdb.connect(str(db_path), read_only=True)
    n = con.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    assert n == 50
    # Confere que normalização funcionou: 1 src_ip único no registry para 50 logs
    distinct_src = con.execute(
        "SELECT COUNT(DISTINCT src_ip_id) FROM logs"
    ).fetchone()[0]
    assert distinct_src == 1
    # nat_ip também: todos vão para o mesmo nat_ip_id
    distinct_nat = con.execute(
        "SELECT COUNT(DISTINCT nat_ip_id) FROM logs"
    ).fetchone()[0]
    assert distinct_nat == 1
    con.close()


def test_ip_registry_is_global_across_days(tmp_path: Path) -> None:
    """Mesmo IP em dias diferentes recebe o mesmo ip_id."""
    s = _tmp_settings(tmp_path)
    s.stream_dir.mkdir(parents=True, exist_ok=True)

    day1 = s.stream_dir / "2026-04-20-10.raw"
    day1.write_text(LINE_TEMPLATE.format(sec=0, src_ip="100.80.0.119", src_port=1) + "\n")

    proc = Processor(settings=s)
    proc.tick()
    proc._flush(force=True)
    proc.store.checkpoint(date(2026, 4, 20))

    # Dia diferente — abrindo via CLI o registry precisa devolver o MESMO ip_id
    from megalog.storage.ip_registry import ip_to_int
    src_int = ip_to_int("100.80.0.119")
    id_day1 = proc.ip_reg.get_id_no_create(src_int)
    assert id_day1 is not None

    # Simula reuso no dia seguinte
    day2 = s.stream_dir / "2026-04-21-10.raw"
    day2.write_text(
        LINE_TEMPLATE.format(sec=0, src_ip="100.80.0.119", src_port=2)
        .replace("2026-04-20", "2026-04-21")
        + "\n"
    )
    proc.tick()
    proc._flush(force=True)
    proc.store.checkpoint(date(2026, 4, 21))

    id_day2 = proc.ip_reg.get_id_no_create(src_int)
    assert id_day2 == id_day1

    proc.store.close()
    proc.ip_reg.close()


def test_continuation_lines_are_joined_in_pipeline(tmp_path: Path) -> None:
    """O processor junta linhas quebradas via join_continuations dentro do tick."""
    s = _tmp_settings(tmp_path)
    s.stream_dir.mkdir(parents=True, exist_ok=True)
    raw = s.stream_dir / "2026-04-20-10.raw"
    # Quebra Mikrotik real: dentro de um IP:PORT, sobra ":port," na linha 2
    raw.write_text(
        "2026-04-20 10:15:32 firewall,info forward: in:bridge1 out:ether2,"
        "connection-state:new, proto TCP, 192.168.1.100:45231->8.8.8.8\n"
        ":443, NAT (192.168.1.100:45231->170.245.175.121:45231)"
        "->8.8.8.8:443, len 60\n"
    )

    proc = Processor(settings=s)
    proc.tick()
    proc._flush(force=True)
    proc.store.checkpoint(date(2026, 4, 20))
    proc.store.close()
    proc.ip_reg.close()

    db = duckdb.connect(str(s.hot_storage_dir / "2026-04-20.duckdb"), read_only=True)
    n = db.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    assert n == 1
    db.close()


def test_offset_persists_between_processor_runs(tmp_path: Path) -> None:
    """Após reiniciar, processor não reprocessa linhas já consumidas."""
    s = _tmp_settings(tmp_path)
    s.stream_dir.mkdir(parents=True, exist_ok=True)
    raw = s.stream_dir / "2026-04-20-10.raw"
    raw.write_text(LINE_TEMPLATE.format(sec=0, src_ip="100.80.0.119", src_port=1) + "\n")

    proc1 = Processor(settings=s)
    proc1.tick()
    proc1._flush(force=True)
    proc1.store.checkpoint(date(2026, 4, 20))
    proc1.store.close()
    proc1.ip_reg.close()

    # Append nova linha
    with raw.open("a") as f:
        f.write(LINE_TEMPLATE.format(sec=1, src_ip="100.80.0.99", src_port=2) + "\n")

    proc2 = Processor(settings=s)
    proc2.tick()
    proc2._flush(force=True)
    proc2.store.checkpoint(date(2026, 4, 20))
    proc2.store.close()
    proc2.ip_reg.close()

    db = duckdb.connect(str(s.hot_storage_dir / "2026-04-20.duckdb"), read_only=True)
    n = db.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    # 2 linhas no total (proc1 inseriu 1, proc2 inseriu só a nova)
    assert n == 2
    db.close()


@pytest.mark.asyncio
async def test_receiver_writes_to_stream_file(tmp_path: Path) -> None:
    """Receiver UDP grava em arquivo de hora rotacionado."""
    s = _tmp_settings(tmp_path)
    s.stream_dir.mkdir(parents=True, exist_ok=True)
    writer = HourlyStreamWriter(s.stream_dir, flush_every=1)

    # Bind em porta efêmera localhost (não precisa root)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setblocking(False)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    loop = asyncio.get_running_loop()
    transport, _proto = await loop.create_datagram_endpoint(
        lambda: SyslogProtocol(writer),
        sock=sock,
    )

    payload = LINE_TEMPLATE.format(sec=0, src_ip="100.80.0.119", src_port=1)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for i in range(5):
        sender.sendto(payload.encode("utf-8"), ("127.0.0.1", port))
    sender.close()

    # dá tempo do event loop drenar os datagramas
    await asyncio.sleep(0.2)
    transport.close()
    writer.close()

    raw_files = list(s.stream_dir.glob("*.raw"))
    assert len(raw_files) == 1
    content = raw_files[0].read_text()
    assert content.count("\n") == 5
    assert "100.80.0.119" in content
