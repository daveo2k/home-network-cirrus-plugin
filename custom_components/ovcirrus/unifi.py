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
        # full records: they carry the port table, LLDP neighbours and system stats
        return await self._get(f"/api/s/{self.site}/stat/device") or []

    async def gateway_ports(self) -> list[dict]:
        """Just the gateway's port table with live rates (for the bandwidth history)."""
        g = parse_gateway([], await self.devices())
        return (g or {}).get("ports") or []

    async def snapshot(self) -> dict:
        health = await self.health()
        clients = await self.clients()
        try:
            devices = await self.devices()
        except UnifiError:
            devices = []
        return {"wan": parse_wan(health, devices), "gateway": parse_gateway(health, devices), "clients": clients}


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


GATEWAY_TYPES = ("udm", "ugw", "uxg")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_gateway(health: list[dict], devices: list[dict]) -> dict | None:
    """Live port, internet and health state of the UniFi gateway, for the map."""
    sub = {h.get("subsystem"): h for h in health if isinstance(h, dict)}
    wan, www = sub.get("wan") or {}, sub.get("www") or {}
    dev = next((d for d in devices if d.get("type") in GATEWAY_TYPES), None) or next(
        (d for d in devices if d.get("mac") and d.get("mac") == wan.get("gw_mac")), None
    )
    if not dev:
        return None
    lldp = {e.get("local_port_name"): e for e in dev.get("lldp_table") or [] if e.get("local_port_name")}
    ports = []
    for p in sorted(dev.get("port_table") or [], key=lambda p: p.get("port_idx") or 0):
        up = bool(p.get("up"))
        nb = lldp.get(p.get("ifname")) or {}
        ports.append({
            "idx": p.get("port_idx"),
            "ifname": p.get("ifname"),
            "name": p.get("name") or f"Port {p.get('port_idx')}",
            "up": up,
            "speed": p.get("speed") if up else None,  # Mbps
            "full_duplex": p.get("full_duplex") if up else None,
            "media": p.get("media"),
            "role": "wan" if p.get("network_name") == "wan" or p.get("is_uplink") else "lan",
            "uplink": bool(p.get("is_uplink")),
            "rx": _num(p.get("rx_bytes-r")),  # bytes/s into the gateway
            "tx": _num(p.get("tx_bytes-r")),  # bytes/s out of the gateway
            "poe": p.get("poe_mode") if p.get("poe_enable") else None,
            "poe_w": _num(p.get("poe_power")) if p.get("poe_enable") else None,
            "neighbor": {"mac": nb.get("chassis_id"), "port": nb.get("port_id")} if nb else None,
        })
    stats = dev.get("system-stats") or {}
    sys = dev.get("sys_stats") or {}
    temps = [t for t in dev.get("temperatures") or [] if _num(t.get("value")) is not None]
    storage = [
        {"name": s.get("name"), "used": s.get("used"), "size": s.get("size")}
        for s in dev.get("storage") or [] if s.get("size")
    ]
    ips = dev.get("ids_ips_signature") or {}
    uptime_stats = (dev.get("uptime_stats") or {}).get("WAN") or (wan.get("uptime_stats") or {}).get("WAN") or {}
    monitors = [
        {"target": m.get("target"), "type": m.get("type"), "latency": m.get("latency_average"), "availability": m.get("availability")}
        for m in uptime_stats.get("alerting_monitors") or []
    ]
    st = dev.get("speedtest-status") or {}
    wan_port = next((p for p in ports if p["uplink"]), next((p for p in ports if p["role"] == "wan"), None))
    geo = (dev.get("active_geo_info") or {}).get("WAN") or {}
    health_out = {
        "cpu": _num(stats.get("cpu")),
        "mem": _num(stats.get("mem")),
        "load": [_num(sys.get(k)) for k in ("loadavg_1", "loadavg_5", "loadavg_15")],
        "temps": [{"name": t.get("name"), "value": round(_num(t.get("value")), 1)} for t in temps],
        "overheating": bool(dev.get("overheating")),
        "uptime": dev.get("uptime"),
        "version": dev.get("displayable_version") or dev.get("version"),
        "upgradable": bool(dev.get("upgradable")),
        "upgrade_to": dev.get("upgrade_to_firmware"),
        "storage": storage,
        "ips_rules": ips.get("rule_count"),
        "ips_updated": (ips.get("update_time") or 0) / 1000 or None,
    }
    alerts = []
    if health_out["overheating"]:
        alerts.append(("error", "Overheating"))
    if (health_out["cpu"] or 0) >= 90 or (health_out["mem"] or 0) >= 95:
        alerts.append(("error", "CPU or memory critical"))
    elif (health_out["cpu"] or 0) >= 75 or (health_out["mem"] or 0) >= 85:
        alerts.append(("warning", "CPU or memory high"))
    if any(t["value"] >= 85 for t in health_out["temps"]):
        alerts.append(("warning", "Running hot"))
    if wan_port and not wan_port["up"]:
        alerts.append(("error", "WAN port down"))
    if (wan.get("status") or "ok") not in ("ok", "unknown") or (www.get("status") or "ok") not in ("ok", "unknown"):
        alerts.append(("error", "Internet connection problem"))
    if health_out["upgradable"]:
        alerts.append(("info", "Firmware update available"))
    return {
        "mac": (dev.get("mac") or "").upper() or None,
        "name": dev.get("name"),
        "model": dev.get("model"),
        "shortname": dev.get("shortname"),
        "ports": ports,
        "health": health_out,
        "alerts": [{"level": lvl, "text": txt} for lvl, txt in alerts],
        "internet": {
            "status": www.get("status") or wan.get("status"),
            "isp": wan.get("isp_name") or geo.get("isp_name") or wan.get("isp_organization"),
            "wan_ip": wan.get("wan_ip") or (dev.get("last_wan_interfaces") or {}).get("WAN", {}).get("ip"),
            "availability": _num(uptime_stats.get("availability")),
            "latency": _num(uptime_stats.get("latency_average")) or _num(www.get("latency")),
            "monitors": monitors,
            "drops": www.get("drops"),
            "uptime": www.get("uptime"),
            "rx": wan_port["rx"] if wan_port else _num(wan.get("rx_bytes-r")),  # download, bytes/s
            "tx": wan_port["tx"] if wan_port else _num(wan.get("tx_bytes-r")),  # upload, bytes/s
            "port": wan_port["ifname"] if wan_port else None,
            "link_speed": wan_port["speed"] if wan_port else None,
            "speedtest": {
                "down": _num(st.get("xput_download")) or _num(www.get("xput_down")),
                "up": _num(st.get("xput_upload")) or _num(www.get("xput_up")),
                "ping": _num(st.get("latency")) or _num(www.get("speedtest_ping")),
                "ts": st.get("rundate") or www.get("speedtest_lastrun"),
                "server": ", ".join(x for x in ((st.get("server") or {}).get("provider"), (st.get("server") or {}).get("city")) if x) or None,
            },
        },
    }
