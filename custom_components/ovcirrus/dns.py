"""Reverse DNS (PTR) lookups with caching, via the system resolver or specific DNS servers.

Specific servers (e.g. a gateway or Pi-hole) are queried directly over UDP so no extra
dependency is needed and the lookups do not depend on HA's own resolver settings.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import random
import socket
import struct
import time

_LOGGER = logging.getLogger(__name__)

POSITIVE_TTL = 3600
NEGATIVE_TTL = 600
QUERY_TIMEOUT = 2.0
CONCURRENCY = 4  # small home gateways drop bursts of PTR queries
ERROR_TTL = 120


def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("idna") if label else b""
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def _read_name(buf: bytes, off: int, depth: int = 0) -> tuple[str, int]:
    labels: list[str] = []
    jumped_end: int | None = None
    while True:
        if off >= len(buf) or depth > 20:
            raise ValueError("bad name")
        length = buf[off]
        if length & 0xC0 == 0xC0:
            ptr = ((length & 0x3F) << 8) | buf[off + 1]
            if jumped_end is None:
                jumped_end = off + 2
            off = ptr
            depth += 1
            continue
        off += 1
        if length == 0:
            break
        labels.append(buf[off : off + length].decode("ascii", "replace"))
        off += length
    return ".".join(labels), (jumped_end if jumped_end is not None else off)


def build_ptr_query(ip: str, qid: int) -> bytes:
    rname = ipaddress.ip_address(ip).reverse_pointer
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)  # recursion desired
    return header + _encode_name(rname) + struct.pack(">HH", 12, 1)


def parse_ptr_response(buf: bytes, qid: int) -> tuple[str | None, int]:
    """Return (ptr name or None, ttl)."""
    rid, flags, qd, an, _, _ = struct.unpack(">HHHHHH", buf[:12])
    if rid != qid:
        raise ValueError("id mismatch")
    if flags & 0x000F:  # NXDOMAIN etc.
        return None, NEGATIVE_TTL
    off = 12
    for _ in range(qd):
        _, off = _read_name(buf, off)
        off += 4
    for _ in range(an):
        _, off = _read_name(buf, off)
        rtype, _, ttl, rdlen = struct.unpack(">HHIH", buf[off : off + 10])
        off += 10
        if rtype == 12:
            name, _ = _read_name(buf, off)
            return name.rstrip("."), max(60, min(ttl, POSITIVE_TTL))
        off += rdlen
    return None, NEGATIVE_TTL


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, fut: asyncio.Future) -> None:
        self.fut = fut

    def datagram_received(self, data, addr) -> None:
        if not self.fut.done():
            self.fut.set_result(data)

    def error_received(self, exc) -> None:
        if not self.fut.done():
            self.fut.set_exception(exc)


async def ptr_via_server(ip: str, server: str, port: int = 53) -> tuple[str | None, int]:
    loop = asyncio.get_running_loop()
    qid = random.randint(0, 0xFFFF)
    fut: asyncio.Future = loop.create_future()
    transport, _ = await loop.create_datagram_endpoint(lambda: _Proto(fut), remote_addr=(server, port))
    try:
        transport.sendto(build_ptr_query(ip, qid))
        data = await asyncio.wait_for(fut, QUERY_TIMEOUT)
        return parse_ptr_response(data, qid)
    finally:
        transport.close()


async def ptr_via_system(ip: str) -> tuple[str | None, int]:
    loop = asyncio.get_running_loop()
    try:
        host, _ = await asyncio.wait_for(loop.getnameinfo((ip, 0), socket.NI_NAMEREQD), QUERY_TIMEOUT * 2)
    except (OSError, asyncio.TimeoutError):
        return None, NEGATIVE_TTL
    return host.rstrip("."), POSITIVE_TTL


def parse_servers(text: str | None) -> list[tuple[str, int]]:
    servers = []
    for part in (text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        host, port = part, 53
        if part.count(":") == 1:  # IPv4:port
            host, p = part.split(":")
            port = int(p)
        servers.append((host, port))
    return servers


class ReverseResolver:
    """Cached PTR resolver. mode: 'system' or 'server'."""

    def __init__(self, mode: str, servers: list[tuple[str, int]]) -> None:
        self.mode = mode
        self.servers = servers
        self._cache: dict[str, tuple[str | None, float]] = {}
        self._sem: asyncio.Semaphore | None = None  # created inside the running loop

    async def _lookup(self, ip: str) -> str | None:
        if self._sem is None:
            self._sem = asyncio.Semaphore(CONCURRENCY)
        async with self._sem:
            if self.mode == "system":
                name, ttl = await ptr_via_system(ip)
            else:
                name, ttl = None, ERROR_TTL  # nobody answered: retry soon
                answered = False
                for server, port in self.servers:
                    for _attempt in (1, 2):
                        try:
                            name, ttl = await ptr_via_server(ip, server, port)
                            answered = True
                            break  # an answer (incl. NXDOMAIN) from this server is final
                        except (OSError, asyncio.TimeoutError, ValueError, struct.error) as err:
                            _LOGGER.debug("PTR %s via %s failed: %s", ip, server, err)
                    if answered:
                        break
            self._cache[ip] = (name, time.monotonic() + ttl)
            return name

    async def resolve_many(self, ips) -> dict[str, str]:
        now = time.monotonic()
        todo = []
        for ip in {i for i in ips if i}:
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                continue
            cached = self._cache.get(ip)
            if not cached or cached[1] < now:
                todo.append(ip)
        if todo:
            await asyncio.gather(*(self._lookup(ip) for ip in todo), return_exceptions=True)
        return {ip: name for ip, (name, _) in self._cache.items() if name}

    async def test(self, ip: str) -> bool:
        """True if the configured server(s) answered at all (an empty answer still counts)."""
        if self.mode == "system":
            return True
        for server, port in self.servers:
            try:
                await ptr_via_server(ip, server, port)
                return True
            except (OSError, asyncio.TimeoutError, ValueError, struct.error):
                continue
        return False
