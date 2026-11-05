"""Read MAC learning / ARP tables from OmniSwitches over SSH (key or certificate auth)."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)

MAC_RE = re.compile(r"\b([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5}|[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})\b")
IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
PORT_RE = re.compile(r"^\d+/\d+(?:/\d+)?[A-Za-z]?$")
CMD_TIMEOUT = 40


class SshError(Exception):
    """SSH collection failed."""


@dataclass
class MacEntry:
    mac: str
    port: str
    vlan: str | None = None
    type: str | None = None


@dataclass
class SwitchTables:
    host: str
    macs: list[MacEntry] = field(default_factory=list)
    arp: dict[str, str] = field(default_factory=dict)  # mac -> ip


def norm_mac(value: str | None) -> str | None:
    """Normalise a MAC to AA:BB:CC:DD:EE:FF, or None if it is not a MAC."""
    if not value:
        return None
    hexchars = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(hexchars) != 12 or not MAC_RE.fullmatch(value.strip()):
        return None
    return ":".join(hexchars[i : i + 2] for i in range(0, 12, 2)).upper()


def parse_mac_learning(text: str) -> list[MacEntry]:
    """Parse AOS 8 `show mac-learning` output (column order tolerant)."""
    entries: list[MacEntry] = []
    for line in text.splitlines():
        match = MAC_RE.search(line)
        if not match or line.lstrip().startswith(("Legend", "Mac Address:")):
            continue
        mac = norm_mac(match.group(1))
        if not mac:
            continue
        before = line[: match.start()].split()
        after = line[match.end() :].split()
        if not after:
            continue
        port = after[-1]
        if not PORT_RE.match(port):
            # e.g. "1/1/13," or a description column; pick the last port-shaped token.
            ports = [t for t in after if PORT_RE.match(t.rstrip(","))]
            if not ports:
                continue
            port = ports[-1].rstrip(",")
        vlan = next((t for t in reversed(before) if t.isdigit()), None)
        entries.append(MacEntry(mac=mac, port=port, vlan=vlan, type=after[0].lower()))
    return entries


def parse_arp(text: str) -> dict[str, str]:
    """Parse AOS `show arp` output into {mac: ip}."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        ip = IP_RE.search(line)
        mac = MAC_RE.search(line)
        if ip and mac:
            norm = norm_mac(mac.group(1))
            if norm:
                result.setdefault(norm, ip.group(1))
    return result


async def _run_commands(conn, commands: list[str]) -> list[str]:
    """Run commands via exec channel; fall back to an interactive shell."""
    outputs: list[str] = []
    try:
        for cmd in commands:
            res = await asyncio.wait_for(conn.run(cmd, check=False), CMD_TIMEOUT)
            outputs.append(str(res.stdout or ""))
        if any(MAC_RE.search(o) for o in outputs):
            return outputs
    except Exception as err:  # noqa: BLE001 - exec may be unsupported
        _LOGGER.debug("SSH exec mode failed (%s), trying interactive shell", err)

    # AOS has no `echo`, so split the transcript on the echoed command lines.
    proc = await conn.create_process(term_type="vt100", term_size=(250, 1000))
    proc.stdin.write("".join(f"{c}\n" for c in commands) + "exit\n")
    try:
        data = await asyncio.wait_for(proc.stdout.read(), CMD_TIMEOUT)
    except asyncio.TimeoutError:
        data = ""
    proc.close()
    return split_transcript(str(data), commands)


def split_transcript(text: str, commands: list[str]) -> list[str]:
    """Return the output of each command from an interactive session transcript."""
    starts: list[int] = []
    pos = 0
    for cmd in commands:
        idx = text.find(cmd, pos)
        starts.append(idx)
        if idx >= 0:
            pos = idx + len(cmd)
    outputs = []
    for i, cmd in enumerate(commands):
        if starts[i] < 0:
            outputs.append("")
            continue
        begin = starts[i] + len(cmd)
        later = [s for s in starts[i + 1 :] if s >= 0]
        outputs.append(text[begin : later[0]] if later else text[begin:])
    return outputs


async def fetch_switch_tables(
    host: str,
    username: str,
    key_path: str | None,
    known_hosts: str | None,
    port: int = 22,
    include_arp: bool = True,
) -> SwitchTables:
    """Connect to a switch and read MAC (and optionally ARP) tables."""
    import asyncssh  # imported lazily so HA can load without the package during tests

    options = {
        "username": username,
        "port": port,
        "known_hosts": known_hosts or None,
        # asyncssh also loads "<key>-cert.pub" alongside the key, for certificate auth.
        "client_keys": [key_path] if key_path else None,
        "connect_timeout": 15,
    }
    commands = ["show mac-learning"] + (["show arp"] if include_arp else [])
    try:
        async with asyncssh.connect(host, **options) as conn:
            outputs = await _run_commands(conn, commands)
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as err:
        raise SshError(f"{host}: {err}") from err

    tables = SwitchTables(host=host, macs=parse_mac_learning(outputs[0]))
    if include_arp and len(outputs) > 1:
        tables.arp = parse_arp(outputs[1])
    if not tables.macs:
        raise SshError(f"{host}: no MAC entries parsed from 'show mac-learning'")
    return tables
