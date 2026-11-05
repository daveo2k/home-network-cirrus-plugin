"""Optional read-only client for a UniFi OS gateway (UCG / UDM / UDR) on the LAN.

Gives the gateway's WAN status, ISP, WAN IP, last speed test and UniFi's view of clients
(user-set aliases, hostnames, vendor), which fills in what Cirrus cannot see.
Uses the local UniFi OS login with a local, read-only account.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=20)


class UnifiError(Exception):
    """Request failed."""


class UnifiAuthError(UnifiError):
    """Login rejected."""


class UnifiClient:
    def __init__(self, host: str, username: str, password: str, site: str = "default", verify_ssl: bool = False):
        self.host = host.strip().removeprefix("https://").rstrip("/")
        self.username = username
        self.password = password
        self.site = site or "default"
        self._ssl = None if verify_ssl else False
        self._session: aiohttp.ClientSession | None = None
        self._csrf: str | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # Separate session: UniFi uses a cookie on an IP host, which needs an unsafe cookie jar.
            self._session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def login(self) -> None:
        session = await self._ensure_session()
        try:
            async with session.post(
                f"https://{self.host}/api/auth/login",
                json={"username": self.username, "password": self.password, "remember": True},
                ssl=self._ssl,
                timeout=TIMEOUT,
            ) as resp:
                if resp.status in (400, 401, 403):
                    raise UnifiAuthError(f"HTTP {resp.status}")
                if resp.status != 200:
                    raise UnifiError(f"login HTTP {resp.status}")
                self._csrf = resp.headers.get("X-Csrf-Token") or resp.headers.get("x-csrf-token")
        except aiohttp.ClientError as err:
            raise UnifiError(f"Cannot reach {self.host}: {err}") from err

    async def _get(self, path: str) -> Any:
        session = await self._ensure_session()
        if not self._csrf:
            await self.login()
        for attempt in (1, 2):
            headers = {"X-Csrf-Token": self._csrf} if self._csrf else {}
            try:
                async with session.get(
                    f"https://{self.host}/proxy/network{path}", headers=headers, ssl=self._ssl, timeout=TIMEOUT
                ) as resp:
                    if resp.status in (401, 403) and attempt == 1:
                        await self.login()
                        continue
                    if resp.status != 200:
                        raise UnifiError(f"{path}: HTTP {resp.status}")
                    if resp.headers.get("X-Updated-Csrf-Token"):
                        self._csrf = resp.headers["X-Updated-Csrf-Token"]
                    body = await resp.json(content_type=None)
                    return body.get("data", body) if isinstance(body, dict) else body
            except aiohttp.ClientError as err:
                raise UnifiError(f"{path}: {err}") from err
        raise UnifiAuthError("session rejected")

    async def health(self) -> list[dict]:
        return await self._get(f"/api/s/{self.site}/stat/health") or []

    async def clients(self) -> list[dict]:
        return await self._get(f"/api/s/{self.site}/stat/sta") or []

    async def devices(self) -> list[dict]:
        return await self._get(f"/api/s/{self.site}/stat/device-basic") or []

    async def snapshot(self) -> dict:
        health = await self.health()
        clients = await self.clients()
        try:
            devices = await self.devices()
        except UnifiError:
            devices = []
        return {"wan": parse_wan(health, devices), "clients": clients}


def parse_wan(health: list[dict], devices: list[dict]) -> dict:
    """Merge the 'wan' and 'www' health subsystems into one WAN summary."""
    sub = {h.get("subsystem"): h for h in health if isinstance(h, dict)}
    wan, www = sub.get("wan") or {}, sub.get("www") or {}

    def pick(*keys):
        for src in (www, wan):
            for key in keys:
                if src.get(key) not in (None, ""):
                    return src[key]
        return None

    gateway = next((d for d in devices if d.get("type") in ("udm", "ugw", "uxg") or d.get("mac") == wan.get("gw_mac")), {})
    return {
        "status": wan.get("status") or www.get("status"),
        "internet": www.get("status"),
        "wan_ip": wan.get("wan_ip"),
        "isp": wan.get("isp_name") or wan.get("isp_organization"),
        "download_mbps": pick("xput_down"),
        "upload_mbps": pick("xput_up"),
        "latency_ms": pick("speedtest_ping", "latency"),
        "speedtest_status": pick("speedtest_status"),
        "speedtest_lastrun": pick("speedtest_lastrun"),
        "uptime": pick("uptime"),
        "gateway_model": gateway.get("model"),
        "gateway_name": gateway.get("name"),
        "gateway_mac": (gateway.get("mac") or wan.get("gw_mac") or "").upper() or None,
        "gateway_version": gateway.get("version"),
    }
