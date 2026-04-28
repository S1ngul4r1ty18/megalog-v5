"""Receptor UDP de syslog — asyncio + uvloop + SO_REUSEPORT.

Substitui [v4 log_receiver.py](../../../megalog/log_receiver.py): aquele era
síncrono, single-thread, com socket.recvfrom + timeout de 1s. Aqui usamos
asyncio.DatagramProtocol que já é não-bloqueante por construção, e uvloop
para reduzir overhead da event loop em alta taxa.

SO_REUSEPORT permite N processos receiver no mesmo (host, porta), que o
kernel distribui em round-robin. Para escalar acima de ~200k pkt/s/core
basta subir mais workers via systemd `Service` com `%i`.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import socket
import sys
from datetime import datetime

import uvloop

from megalog.config import get_settings
from megalog.ingest.stream import HourlyStreamWriter

log = logging.getLogger("megalog.receiver")


class SyslogProtocol(asyncio.DatagramProtocol):
    def __init__(self, writer: HourlyStreamWriter):
        self.writer = writer
        self.packets = 0
        self.bytes = 0
        self._stats_at = datetime.now()

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            line = data.decode("utf-8", errors="replace").rstrip("\n\r")
        except Exception:
            line = data.decode("latin-1", errors="replace").rstrip("\n\r")
        if not line:
            return
        self.writer.write_line(line)
        self.packets += 1
        self.bytes += len(data)

    def error_received(self, exc: Exception) -> None:
        log.error("UDP error: %s", exc)


def _make_socket(host: str, port: int, rcvbuf: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    sock.setblocking(False)
    sock.bind((host, port))
    return sock


async def _run() -> None:
    s = get_settings()
    s.stream_dir.mkdir(parents=True, exist_ok=True)
    writer = HourlyStreamWriter(s.stream_dir, flush_every=s.stream_flush_every_n_packets)
    sock = _make_socket(s.receiver_host, s.receiver_port, s.receiver_socket_buffer_bytes)

    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: SyslogProtocol(writer),
        sock=sock,
    )

    log.info(
        "Receiver listening on %s:%d (SO_RCVBUF=%d, SO_REUSEPORT=on)",
        s.receiver_host, s.receiver_port, s.receiver_socket_buffer_bytes,
    )

    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async def _stats() -> None:
        prev_pkts, prev_bytes = 0, 0
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
                return
            except asyncio.TimeoutError:
                pass
            d_pkts = protocol.packets - prev_pkts
            d_bytes = protocol.bytes - prev_bytes
            prev_pkts, prev_bytes = protocol.packets, protocol.bytes
            log.info(
                "Stats: %d pkt/s | %.1f KB/s | total %d pkts",
                d_pkts // 60, d_bytes / 1024 / 60, protocol.packets,
            )

    stats_task = asyncio.create_task(_stats())
    await stop.wait()
    stats_task.cancel()
    transport.close()
    writer.close()
    log.info("Receiver shutdown — total packets: %d", protocol.packets)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    uvloop.install()
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
