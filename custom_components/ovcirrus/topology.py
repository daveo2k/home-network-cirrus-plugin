"""Build a hierarchical network map from Cirrus topology, LLDP, clients and MAC tables.

Pure Python (no Home Assistant imports) so it can be unit tested with saved API data.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from .mac_table import SwitchTables, norm_mac

INFRA_KINDS = {"gateway", "switch", "ap"}
# Ports with more unexplained MACs than this are drawn as an unmanaged device.
UNMANAGED_PORT_THRESHOLD = 3

GROUP_ORDER = [
    ("gateway", "Gateway", "mdi:shield-check-outline"),
    ("switch", "Switches", "mdi:switch"),
    ("ap", "Access Points", "mdi:access-point"),
    ("bridge", "Unmanaged devices", "mdi:lan"),
    ("endpoint", "Wired devices (LLDP)", "mdi:lan-connect"),
    ("wired", "Wired clients", "mdi:ethernet"),
]
MANUAL_GROUP = ("manual", "Servers & manual devices", "mdi:server-network")

KIND_ICONS = {
    "internet": "mdi:web",
    "gateway": "mdi:router-network",
    "switch": "mdi:switch",
    "ap": "mdi:access-point",
    "bridge": "mdi:lan",
    "endpoint": "mdi:desktop-tower-monitor",
    "wired": "mdi:ethernet",
}


def _speed(bps: Any) -> str:
    try:
        bps = int(bps)
    except (TypeError, ValueError):
        return ""
    if bps >= 1_000_000_000:
        val = bps / 1_000_000_000
        return f"{val:g}G"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:g}M"
    return ""


def _duration(seconds: Any) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _bytes(value: Any) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} PB"


def client_icon(client: dict) -> str:
    dtype = (client.get("deviceType") or "").lower()
    os_type = (client.get("osType") or "").lower()
    if dtype in ("mobile",) or os_type in ("ios", "android"):
        return "mdi:cellphone"
    if dtype in ("ipad", "tablet"):
        return "mdi:tablet"
    if dtype == "computer" or "windows" in os_type or "mac" in os_type:
        return "mdi:laptop"
    if "iot" in (client.get("ssid") or "").lower():
        return "mdi:home-automation"
    return "mdi:devices"


@dataclass
class Names:
    """Name sources: HA device registry (by MAC) and reverse DNS (by IP)."""

    ha: dict[str, str] = field(default_factory=dict)
    dns: dict[str, str] = field(default_factory=dict)
    prefer_dns: bool = False
    dns_short: bool = True

    def dns_full(self, ip: str | None) -> str | None:
        return self.dns.get(ip or "")

    def dns_display(self, ip: str | None) -> str | None:
        full = self.dns_full(ip)
        if not full:
            return None
        return full.split(".")[0] if self.dns_short else full


def client_name(client: dict, ha_names: dict[str, str], names: Names | None = None) -> str:
    names = names or Names(ha=ha_names)
    mac = norm_mac(client.get("clientMac")) or client.get("clientMac") or "?"
    hostname = (client.get("deviceName") or "").strip()
    dns = names.dns_display(client.get("clientIp"))
    if names.prefer_dns and dns:
        return dns
    return (
        hostname
        or dns
        or ha_names.get(mac)
        or (client.get("auth8021xName") or "").strip()
        or client.get("clientIp")
        or mac
    )


@dataclass
class MapExtras:
    """Optional enrichment computed by the coordinator."""

    identities: dict[str, dict] = field(default_factory=dict)  # MAC -> identity (see coordinator)
    manual: list[dict] = field(default_factory=list)  # user-added devices
    services: dict[str, dict] = field(default_factory=dict)  # IP -> discovery result
    wan: dict | None = None  # UniFi WAN / speed test summary
    port_layouts: dict[str, dict] = field(default_factory=dict)  # model slug -> NetBox port counts
    entry_id: str | None = None
    unifi_ips: dict[str, str] = field(default_factory=dict)  # MAC -> IP from the gateway
    port_vlans: dict[str, dict] = field(default_factory=dict)  # switch serial -> port -> {"untagged","tagged"}
    vlans: dict[str, dict] = field(default_factory=dict)  # VLAN id -> {"name", "up"}
    radios: dict[str, list] = field(default_factory=dict)  # AP MAC -> radio dicts from rf-details
    rf_profiles: dict[str, str] = field(default_factory=dict)  # AP MAC -> RF profile name


def radio_text(r: dict) -> str:
    bits = [f"channel {r.get('channel')}" if r.get("channel") is not None else None,
            f"{r['bandwidth']} MHz" if r.get("bandwidth") else None,
            f"{r['txPower']} dBm" if r.get("txPower") is not None else None,
            f"{r['channelUtilization']}% busy" if r.get("channelUtilization") is not None else None,
            f"noise {r['noiseFloor']} dBm" if r.get("noiseFloor") is not None else None]
    return " · ".join(b for b in bits if b)


def vlan_text(v: dict | None, names: dict[str, dict]) -> str | None:
    if not v:
        return None
    nm = lambda i: f"{i} {names.get(str(i), {}).get('name', '')}".strip()
    parts = []
    if v.get("untagged"):
        parts.append("untagged " + ", ".join(nm(i) for i in v["untagged"]))
    if v.get("tagged"):
        parts.append("tagged " + ", ".join(nm(i) for i in v["tagged"]))
    return " · ".join(parts) or None


def walk_nodes(node: dict):
    yield node
    for group in node.get("groups") or []:
        for child in group.get("children") or []:
            yield from walk_nodes(child)


def pick_name(names: Names, ident: dict | None, hostname: str | None, ip: str | None, mac: str | None,
              ha_name: str | None = None, user: str | None = None) -> str:
    """Override/UniFi alias > hostname > DNS > HA name > 802.1X user > identified type > IP > MAC."""
    ident = ident or {}
    if ident.get("name"):
        return ident["name"]
    dns = names.dns_display(ip)
    if names.prefer_dns and dns:
        return dns
    hostname = (hostname or "").strip()
    identified = ident.get("label") if (ident.get("vendor") or ident.get("brand") or ident.get("type") not in (None, "unknown")) else None
    return hostname or dns or ha_name or (user or "").strip() or identified or ip or mac or "Unknown"


def identity_details(ident: dict | None) -> list[tuple[str, Any]]:
    ident = ident or {}
    return [
        ("Device type", ident.get("type_label") if ident.get("type") not in (None, "unknown") else None),
        ("Brand", ident.get("brand")),
        ("MAC vendor", ident.get("vendor")),
        ("Model", ident.get("model")),
        ("MAC address type", "private (randomised)" if ident.get("private_mac") else None),
        ("Identified by", ", ".join(ident.get("sources") or []) or None),
    ]


SWITCH_MODEL_RE = re.compile(r"OS(\d{4})([A-Z]*)-([A-Z]*)(\d+)", re.I)
AP_MODEL_RE = re.compile(r"AP(\d{4})([A-Z]*)", re.I)
OUTDOOR_AP_PREFIXES = ("125",)  # AP1251 family; AP136x are indoor


def model_slug(model: str | None) -> str:
    """'OAW-AP1321' -> 'oaw-ap1321' (matches image file names)."""
    return re.sub(r"[^a-z0-9]+", "-", (model or "").lower()).strip("-")


def _port_index(port: str) -> int | None:
    try:
        return int(port.split("/")[-1])
    except (ValueError, AttributeError):
        return None


def switch_graphic(model: str | None, ports: list[dict], port_labels: dict[str, str],
                   layout: dict | None = None, port_vlans: dict | None = None) -> dict:
    """Front-panel spec: port counts from NetBox (or the model number), live state from Cirrus."""
    copper, sfp, poe = 24, 4, False
    match = SWITCH_MODEL_RE.search(model or "")
    if match:
        letters, count = match.group(3).upper(), int(match.group(4))
        poe = "P" in letters
        if "U" in letters:  # all-fibre models
            copper, sfp = 0, count
        else:
            copper, sfp = count, 4 if count >= 24 else 2
    if layout:
        copper, sfp = layout.get("copper", copper), layout.get("sfp", sfp)
    state: dict[str, dict] = {}
    max_index = 0
    for entry in ports or []:
        port = entry.get("port") or ""
        idx = _port_index(port)
        if not idx or not port.startswith("1/1/"):
            continue  # only the first chassis/slot is drawn
        max_index = max(max_index, idx)
        state[str(idx)] = {
            "up": entry.get("opeStatus") == "up",
            "admin": entry.get("adminStatus") != "down",
        }
    for port, label in port_labels.items():
        idx = _port_index(port)
        if idx and port.startswith("1/1/"):
            state.setdefault(str(idx), {"up": True, "admin": True})["label"] = label
    for port, v in (port_vlans or {}).items():
        idx = _port_index(port)
        if idx and port.startswith("1/1/") and str(idx) in state:
            state[str(idx)]["u"] = v.get("untagged") or []
            state[str(idx)]["t"] = v.get("tagged") or []
    extra = max(0, max_index - copper - sfp)
    if layout and layout.get("stack") is not None:
        extra = layout["stack"]
    return {
        "type": "switch",
        "model": model,
        "copper": copper,
        "sfp": sfp,
        "stack": extra,
        "poe": poe,
        "ports": state,
        "known": bool(ports),
        "layout_source": "NetBox device library" if layout else "model number",
    }


def ap_graphic(model: str | None) -> dict:
    match = AP_MODEL_RE.search(model or "")
    form = "ceiling"
    if match:
        number, suffix = match.group(1), match.group(2).upper()
        if suffix.startswith("H"):
            form = "wallplate"
        elif number.startswith(OUTDOOR_AP_PREFIXES):
            form = "outdoor"
    return {"type": "ap", "form": form, "model": model}


def _wireless_node(client: dict, ha_names: dict[str, str], names: Names | None = None,
                   ident: dict | None = None) -> dict:
    names = names or Names(ha=ha_names)
    mac = norm_mac(client.get("clientMac")) or client.get("clientMac")
    ident = ident or {}
    rssi = client.get("rssi")
    details = [
        ("IP", client.get("clientIp")),
        ("MAC", mac),
        ("Hostname", client.get("deviceName")),
        ("DNS name", names.dns_full(client.get("clientIp"))),
        ("HA name", ha_names.get(mac or "")),
        ("SSID", client.get("ssid")),
        ("AP", client.get("apLocation") or client.get("apName")),
        ("Band / channel", f"{client.get('band') or ''} ch {client.get('channel') or '?'}"),
        ("Wi-Fi mode", client.get("wifiMode")),
        ("RSSI", f"{rssi} dBm" if rssi is not None else None),
        ("Auth", client.get("authType")),
        ("802.1X user", client.get("auth8021xName")),
        ("Portal user", client.get("portalUsername")),
        ("OS / type", " / ".join(x for x in (client.get("osType"), client.get("deviceType")) if x)),
        ("VLAN", client.get("vlanId")),
        ("Connected", _duration(client.get("sessionDuration"))),
        ("Rx / Tx", f"{_bytes(client.get('rxBytes'))} / {_bytes(client.get('txBytes'))}"),
        *identity_details(ident),
    ]
    sub = [client.get("clientIp") or ""]
    if rssi is not None:
        sub.append(f"{rssi} dBm")
    if client.get("band"):
        sub.append(client["band"])
    return {
        "id": f"client-{mac}",
        "kind": "wireless",
        "name": pick_name(names, ident, client.get("deviceName") or ident.get("hostname"), client.get("clientIp"),
                          mac, ha_names.get(mac or ""), client.get("auth8021xName")),
        "subtitle": " · ".join(s for s in sub if s),
        "icon": ident.get("icon") or client_icon(client),
        "logo": ident.get("logo"),
        "status": "online",
        "rssi": rssi,
        "mac": mac,
        "ip": client.get("clientIp"),
        "hostname": client.get("deviceName") or ident.get("hostname"),
        "vendor": ident.get("brand") or ident.get("vendor"),
        "type_label": ident.get("type_label"),
        "dev_type": ident.get("type"),
        "where": f"{client.get('ssid')} via {client.get('apLocation') or client.get('apName')}",
        "vlan": client.get("vlanId") or None,
        "band": client.get("band") or None,
        "editable": True,
        "details": [[k, str(v)] for k, v in details if v not in (None, "", " / ", "None")],
        "groups": [],
    }


def build_site_map(
    site: dict,
    devices: list[dict],
    topology: dict,
    lldp_by_serial: dict[str, list[dict]],
    clients: list[dict],
    switch_tables: dict[str, SwitchTables],
    ha_names: dict[str, str] | None = None,
    ports_by_serial: dict[str, list[dict]] | None = None,
    dns_names: dict[str, str] | None = None,
    prefer_dns: bool = False,
    dns_short: bool = True,
    images: dict[str, str] | None = None,
    extras: MapExtras | None = None,
) -> dict:
    """Return a card-ready tree for one site.

    switch_tables is keyed by switch serial number.
    """
    ha_names = ha_names or {}
    extras = extras or MapExtras()
    names = Names(ha=ha_names, dns=dns_names or {}, prefer_dns=prefer_dns, dns_short=dns_short)
    ports_by_serial = ports_by_serial or {}
    idents = (extras or MapExtras()).identities
    manual_macs = {norm_mac(m.get("mac")) for m in extras.manual if norm_mac(m.get("mac"))}
    site_id = site.get("id")
    site_devices = [d for d in devices if (d.get("site") or {}).get("id") == site_id]
    dev_by_mac = {norm_mac(d.get("macAddress")): d for d in site_devices if norm_mac(d.get("macAddress"))}
    serial_to_key = {d.get("serialNumber"): norm_mac(d.get("macAddress")) for d in site_devices}
    key_to_serial = {v: k for k, v in serial_to_key.items() if v}

    def port_vlan(sw_key: str, port: str, lag: Any = None) -> dict | None:
        table = extras.port_vlans.get(key_to_serial.get(sw_key, ""), {}) if extras else {}
        return table.get(port) or (table.get(f"0/{lag}") if lag is not None else None)

    # LLDP neighbour info keyed by chassis MAC (or raw chassis id when it is a name).
    lldp_info: dict[str, dict] = {}
    for entries in lldp_by_serial.values():
        for entry in entries or []:
            remote = entry.get("remote") or {}
            key = norm_mac(remote.get("chassisId")) or norm_mac(remote.get("macAddress")) or remote.get("macAddress")
            if key:
                lldp_info.setdefault(key, remote)

    # ---- nodes -----------------------------------------------------------
    nodes: dict[str, dict] = {}

    def add_node(key: str, raw: dict) -> dict:
        dev = dev_by_mac.get(key) or {}
        family = raw.get("family") or dev.get("deviceFamily") or "UNKNOWN"
        caps = set((lldp_info.get(key) or {}).get("enabledCapabilities") or [])
        managed = bool(raw.get("managed")) or bool(dev)
        if family in ("AOS", "LEGACY_OMNISWITCH"):
            kind = "switch"
        elif family == "AP":
            kind = "ap"
        elif "router" in caps and not managed:
            kind = "gateway"
        elif caps & {"bridge", "wlanAccessPoint"}:
            kind = "bridge"
        else:
            kind = "endpoint"
        reachable = (raw.get("reachable") or "").lower()
        if reachable == "on":
            status = "online"
        elif reachable == "off":
            status = "offline"
        elif managed and dev.get("lastSeenTime"):
            status = "online"
        else:
            status = "unknown"
        mgmt = [a.get("address") for a in raw.get("managementAddresses") or [] if a.get("address")]
        ip = dev.get("ipAddress") or raw.get("ipAddress") or (mgmt[0] if mgmt else None)
        name = (dev.get("name") or raw.get("name") or "").strip()
        real_mac = key if norm_mac(key) else None
        node = {
            "id": key,
            "mac": real_mac,
            "kind": kind,
            "family": family,
            "managed": managed,
            "name": name,
            "ip": ip,
            "status": status,
            "serial": dev.get("serialNumber") or raw.get("serialNumber"),
            "model": dev.get("modelName") or raw.get("modelName"),
            "version": dev.get("currentSwVer") or raw.get("version"),
            "uptime": dev.get("upTime"),
            "description": raw.get("description"),
            "location": " / ".join(x for x in (raw.get("building"), raw.get("floor")) if x),
            "inferred": False,
        }
        nodes[key] = node
        return node

    for raw in topology.get("nodes") or []:
        key = norm_mac(raw.get("macAddress")) or raw.get("macAddress")
        if key:
            add_node(key, raw)
    for key, dev in dev_by_mac.items():
        if key not in nodes:
            add_node(key, {"family": dev.get("deviceFamily"), "managed": True})

    # ---- links -------------------------------------------------------------
    links: dict[frozenset, dict] = {}
    port_neighbor: dict[tuple[str, str], str] = {}
    infra_macs: set[str] = {k for k in nodes if norm_mac(k)}

    for link in topology.get("links") or []:
        src, dst = link.get("src") or {}, link.get("dest") or {}
        a = norm_mac(src.get("macAddress")) or src.get("macAddress")
        b = norm_mac(dst.get("macAddress")) or dst.get("macAddress")
        if not a or not b or a == b:
            continue
        for key, raw in ((a, src), (b, dst)):
            if key not in nodes:
                add_node(key, {"name": (lldp_info.get(key) or {}).get("name")})
        attrs = link.get("attributes") or {}
        rec = links.setdefault(
            frozenset((a, b)),
            {"ends": {a: set(), b: set()}, "speed": 0, "up": False, "members": []},
        )
        rec["members"].append({
            "a": a,
            "a_port": src.get("port") or "",
            "b_port": "" if norm_mac(dst.get("port")) else (dst.get("port") or ""),
            "b_desc": dst.get("portDesc") or "",
            "speed": attrs.get("ifSpeed"),
            "up": attrs.get("linkStatus") == "up",
            "lag": attrs.get("lnkAggSrcNumber"),
        })
        rec["ends"][a].add(src.get("port") or "")
        if not norm_mac(dst.get("port")):
            rec["ends"][b].add(dst.get("port") or "")
        try:
            rec["speed"] = max(rec["speed"], int(attrs.get("ifSpeed") or 0))
        except (TypeError, ValueError):
            pass
        rec["up"] = rec["up"] or (attrs.get("linkStatus") == "up")
        port_neighbor[(a, src.get("port") or "")] = b
        if attrs.get("lnkAggSrcNumber") is not None:
            port_neighbor[(a, f"0/{attrs['lnkAggSrcNumber']}")] = b
        # Remote port IDs that are MACs belong to the neighbour (gateway ports, host NICs).
        port_mac = norm_mac(dst.get("port"))
        if port_mac:
            infra_macs.add(port_mac)
            if not nodes[b]["mac"]:
                nodes[b]["mac"] = port_mac

    for node in nodes.values():
        if not node["name"]:
            info = lldp_info.get(node["id"]) or {}
            node["name"] = (
                (info.get("name") or "").strip()
                or ha_names.get(node["mac"] or "")
                or names.dns_display(node.get("ip"))
                or (node["id"] if not norm_mac(node["id"]) else "")
            )
        node["dns"] = names.dns_full(node.get("ip"))
        if node["kind"] == "endpoint" and node["mac"] and ha_names.get(node["mac"]):
            node.setdefault("ha_name", ha_names[node["mac"]])

    wan = extras.wan or {}
    if wan:
        gws = [n for n in nodes.values() if n["kind"] == "gateway"]
        gw = next((n for n in gws if n.get("mac") == wan.get("gateway_mac")), gws[0] if len(gws) == 1 else None)
        if gw:
            gw["model"] = gw.get("model") or wan.get("gateway_model")
            gw["version"] = gw.get("version") or wan.get("gateway_version")
            gw["name"] = gw["name"] or wan.get("gateway_name") or ""
            gw["wan"] = wan

    adjacency: dict[str, list[str]] = defaultdict(list)
    for pair in links:
        a, b = tuple(pair)
        adjacency[a].append(b)
        adjacency[b].append(a)

    # ---- build tree (BFS from gateway) --------------------------------------
    gateways = [k for k, n in nodes.items() if n["kind"] == "gateway"]
    if not gateways:
        switches = sorted(
            (k for k, n in nodes.items() if n["kind"] == "switch"),
            key=lambda k: -len(adjacency[k]),
        )
        roots = switches[:1] or [k for k, n in nodes.items() if n["managed"]][:1]
    else:
        roots = gateways
    parent: dict[str, str | None] = {}
    edge: dict[str, dict] = {}
    queue = deque()
    for root in roots:
        parent[root] = None
        queue.append(root)
    while queue:
        cur = queue.popleft()
        for nxt in sorted(adjacency[cur]):
            if nxt in parent:
                continue
            parent[nxt] = cur
            rec = links[frozenset((cur, nxt))]
            ports = sorted(p for p in rec["ends"][cur] if p) or sorted(p for p in rec["ends"][nxt] if p)
            members = []
            for m in sorted(rec["members"], key=lambda m: (_port_index(m["a_port"]) or 0, m["a_port"])):
                sw_side = m["a"]
                v = port_vlan(sw_side, m["a_port"], m["lag"])
                far = m["b_port"] or re.sub(r"^Alcatel-Lucent Enterprise\s+", "", m["b_desc"] or "")
                members.append({
                    "label": " ↔ ".join(x for x in (m["a_port"], far) if x),
                    "port": m["a_port"],
                    "far": far,
                    "speed": _speed(m["speed"]),
                    "status": "up" if m["up"] else "down",
                    "untagged": (v or {}).get("untagged", []),
                    "tagged": (v or {}).get("tagged", []),
                    "lag": m["lag"],
                })
            vlan_ids = sorted({i for m in members for i in m["untagged"] + m["tagged"]})
            edge[nxt] = {
                "label": " ".join(x for x in (", ".join(ports), _speed(rec["speed"])) if x),
                "status": "up" if rec["up"] else "down",
                "ports": ports,
                "links": members,
                "vlans": vlan_ids,
            }
            queue.append(nxt)

    # Unmanaged neighbours: an active LLDP link is the best liveness signal we have.
    for key, node in nodes.items():
        if node["status"] == "unknown":
            rec_up = any(links[p]["up"] for p in links if key in p)
            if rec_up:
                node["status"] = "online"

    # Locate a MAC in the switch MAC tables -> (switch_key, port).
    mac_location: dict[str, tuple[str, str]] = {}
    for serial, tables in switch_tables.items():
        sw_key = serial_to_key.get(serial)
        if not sw_key:
            continue
        for entry in tables.macs:
            mac_location.setdefault(entry.mac, (sw_key, entry.port))

    root_switch = next((k for k in parent if nodes[k]["kind"] == "switch"), roots[0] if roots else None)

    # Orphans: devices Cirrus knows about but with no LLDP link (e.g. AP behind an unmanaged switch).
    for key, node in nodes.items():
        if key in parent:
            continue
        loc = mac_location.get(node["mac"] or key)
        if loc:
            sw_key, port = loc
            neighbor = port_neighbor.get((sw_key, port))
            if neighbor and neighbor != key and neighbor in parent:
                parent[key] = neighbor
                if nodes[neighbor]["kind"] == "endpoint":
                    nodes[neighbor]["kind"] = "bridge"
                edge[key] = {"label": f"via {port}", "status": "up", "inferred": True}
            else:
                parent[key] = sw_key
                edge[key] = {"label": port, "status": "up", "inferred": True}
        elif root_switch and key != root_switch:
            parent[key] = root_switch
            edge[key] = {"label": "uplink unknown", "status": "unknown", "inferred": True}
        else:
            parent[key] = None
            roots.append(key)

    # ---- wireless clients ----------------------------------------------------
    site_clients = [c for c in clients if c.get("siteId") in (None, site_id)]
    wireless_macs = {norm_mac(c.get("clientMac")) for c in site_clients}
    clients_by_ap: dict[str, list[dict]] = defaultdict(list)
    for client in site_clients:
        ap_key = norm_mac(client.get("apMac"))
        if ap_key not in nodes:
            ap_key = root_switch
        if ap_key:
            clients_by_ap[ap_key].append(client)

    # ---- wired clients from MAC tables ----------------------------------------
    arp: dict[str, str] = {}
    for tables in switch_tables.values():
        for mac, ip in tables.arp.items():
            arp.setdefault(mac, ip)

    wired_by_parent: dict[str, list[dict]] = defaultdict(list)
    seen_wired: set[str] = set()
    port_buckets: dict[tuple[str, str], list] = defaultdict(list)
    for serial, tables in switch_tables.items():
        sw_key = serial_to_key.get(serial)
        if not sw_key or sw_key not in nodes:
            continue
        for entry in tables.macs:
            mac = entry.mac
            if mac in seen_wired or mac in infra_macs or mac in wireless_macs or mac in manual_macs:
                continue
            if int(mac[:2], 16) & 1:  # multicast
                continue
            neighbor = port_neighbor.get((sw_key, entry.port))
            if neighbor and nodes[neighbor]["kind"] in INFRA_KINDS:
                continue  # learned via an uplink or an AP
            seen_wired.add(mac)
            port_buckets[(neighbor or sw_key, entry.port)].append((sw_key, entry))

    for (owner, port), items in port_buckets.items():
        owner_is_switch = nodes[owner]["kind"] == "switch"
        target = owner
        if owner_is_switch and len(items) > UNMANAGED_PORT_THRESHOLD:
            target = f"unmanaged-{owner}-{port}"
            nodes[target] = {
                "id": target, "mac": None, "kind": "bridge", "family": "UNKNOWN", "managed": False,
                "name": f"Unmanaged device", "ip": None, "status": "online", "inferred": True,
                "serial": None, "model": None, "version": None, "uptime": None,
                "description": f"{len(items)} MAC addresses learned on {port}", "location": "",
            }
            parent[target] = owner
            edge[target] = {"label": port, "status": "up", "inferred": True}
        elif not owner_is_switch and nodes[owner]["kind"] == "endpoint" and len(items) > 1:
            nodes[owner]["kind"] = "bridge"
        for sw_key, entry in items:
            ident = idents.get(entry.mac) or {}
            ip = arp.get(entry.mac) or extras.unifi_ips.get(entry.mac)
            name = pick_name(names, ident, ident.get("hostname"), ip, entry.mac, ha_names.get(entry.mac))
            wired_by_parent[target].append(
                {
                    "id": f"wired-{entry.mac}",
                    "kind": "wired",
                    "name": name,
                    "subtitle": " · ".join(x for x in (ip if name != ip else None, f"port {entry.port}", f"VLAN {entry.vlan}" if entry.vlan else None) if x),
                    "icon": ident.get("icon") if ident.get("type") not in (None, "unknown") else "mdi:ethernet",
                    "logo": ident.get("logo"),
                    "status": "online",
                    "mac": entry.mac,
                    "ip": ip,
                    "hostname": ident.get("hostname"),
                    "vendor": ident.get("brand") or ident.get("vendor"),
                    "type_label": ident.get("type_label"),
                    "dev_type": ident.get("type"),
                    "where": f"{nodes[sw_key]['name']} port {entry.port}",
                    "vlan": int(entry.vlan) if (entry.vlan or "").isdigit() else None,
                    "editable": True,
                    "services": extras.services.get(ip or ""),
                    "details": [
                        [k, str(v)]
                        for k, v in (
                            ("MAC", entry.mac),
                            ("IP", ip),
                            ("Hostname", ident.get("hostname")),
                            ("HA name", ha_names.get(entry.mac)),
                            ("DNS name", names.dns_full(ip)),
                            ("Switch", nodes[sw_key]["name"]),
                            ("Port", entry.port),
                            ("VLAN", entry.vlan),
                            ("Entry type", entry.type),
                            *identity_details(ident),
                        )
                        if v
                    ],
                    "groups": [],
                }
            )

    # ---- manually added devices ------------------------------------------------
    ip_to_mac = {ip: mac for mac, ip in arp.items()}
    ip_to_mac.update({ip: mac for mac, ip in extras.unifi_ips.items() if ip not in ip_to_mac})
    manual_by_parent: dict[str, list[dict]] = defaultdict(list)
    for dev in extras.manual:
        if dev.get("site_id") and dev["site_id"] != site_id:
            continue
        mac = norm_mac(dev.get("mac")) or ip_to_mac.get(dev.get("ip") or "")
        ident = idents.get(mac or "") or {}
        loc, how = None, None
        sw_from_cfg = serial_to_key.get(dev.get("switch") or "")
        if sw_from_cfg and dev.get("port"):
            loc, how = (sw_from_cfg, dev["port"]), "configured"
        elif mac and mac in mac_location:
            loc, how = mac_location[mac], "MAC table"
        if dev.get("switch") and not sw_from_cfg:
            continue  # belongs to a switch on another site
        if loc:
            sw_key, port = loc
            neighbor = port_neighbor.get((sw_key, port))
            owner = neighbor if neighbor and nodes.get(neighbor, {}).get("kind") in ("bridge", "endpoint") else sw_key
            pv = port_vlan(sw_key, port) or {}
            link = {"label": port, "status": "up", "inferred": how != "configured",
                    "vlans": sorted(set(pv.get("untagged", []) + pv.get("tagged", []))),
                    "links": [{"label": port, "speed": "", "status": "up", "untagged": pv.get("untagged", []),
                               "tagged": pv.get("tagged", [])}] if pv else []}
        elif root_switch:
            owner = root_switch
            link = {"label": "location unknown", "status": "unknown", "inferred": True}
        else:
            continue
        dtype = dev.get("type") or ident.get("type") or "server"
        from .identify import TYPES  # local import keeps module import order simple
        label, icon = TYPES.get(dtype, TYPES["server"])
        svc = extras.services.get(dev.get("ip") or "")
        details = [
            ("Added", "manually"),
            ("IP", dev.get("ip")),
            ("MAC", mac),
            ("Device type", label),
            ("Location", f"{nodes[owner]['name']} {link['label']}" if loc else None),
            ("Located by", how),
            ("DNS name", names.dns_full(dev.get("ip"))),
            ("Detected role", (svc or {}).get("role")),
            ("Detected OS", (svc or {}).get("os")),
            ("Notes", dev.get("notes")),
            *identity_details(ident),
        ]
        manual_by_parent[owner].append({
            "id": f"manual-{dev.get('id') or dev.get('ip') or mac}",
            "kind": "manual",
            "name": dev.get("name") or pick_name(names, ident, None, dev.get("ip"), mac),
            "subtitle": " · ".join(x for x in (dev.get("ip"), (svc or {}).get("role") or label) if x),
            "icon": icon,
            "logo": ident.get("logo"),
            "status": "online" if (mac and mac in mac_location) or svc and svc.get("services") else "unknown",
            "mac": mac,
            "ip": dev.get("ip"),
            "vendor": ident.get("brand") or ident.get("vendor"),
            "type_label": label,
            "dev_type": dtype,
            "where": f"{nodes[owner]['name']} {link['label']}",
            "link": link,
            "services": svc,
            "discover": bool(dev.get("discover")),
            "manual": {k: dev.get(k) for k in ("id", "name", "ip", "mac", "type", "switch", "port", "scan", "notes", "discover")},
            "details": [[k, str(v)] for k, v in details if v not in (None, "")],
            "groups": [],
        })

    # ---- convert to card tree --------------------------------------------------
    children: dict[str | None, list[str]] = defaultdict(list)
    for key, par in parent.items():
        children[par].append(key)

    def infra_card(key: str) -> dict:
        node = nodes[key]
        name = node["name"] or "Unknown device"
        sub = [node.get("ip") or "", node.get("model") or ""]
        if not node.get("ip") and not node.get("model") and node.get("mac"):
            sub = [node["mac"]]
        details = [
            ("Type", {"gateway": "Gateway / router", "switch": "Switch", "ap": "Access point",
                      "bridge": "Unmanaged device", "endpoint": "Wired device (LLDP)"}.get(node["kind"], node["kind"])),
            ("IP", node.get("ip")),
            ("MAC", node.get("mac")),
            ("Model", node.get("model")),
            ("Firmware", node.get("version")),
            ("Serial", node.get("serial")),
            ("Uptime", _duration(node.get("uptime")) if node.get("uptime") else None),
            ("Location", node.get("location")),
            ("Managed by Cirrus", "yes" if node["managed"] else "no"),
            ("Description", node.get("description")),
            ("HA name", node.get("ha_name")),
            ("DNS name", node.get("dns")),
            ("Uplink", (edge.get(key) or {}).get("label")),
            *[
                (f"Link {m.get('port') or m['label']}",
                 " · ".join(x for x in (f"to {m['far']}" if m.get("far") else None, m["speed"], m["status"], vlan_text(m, extras.vlans)) if x))
                for m in (edge.get(key) or {}).get("links") or []
            ],
        ]
        node_wan = node.get("wan") or {}
        if node_wan:
            details += [("WAN IP", node_wan.get("wan_ip")), ("ISP", node_wan.get("isp")),
                        ("WAN status", node_wan.get("status"))]
        groups_by_kind: dict[str, list[dict]] = defaultdict(list)
        for child in sorted(children.get(key, []), key=lambda k: (nodes[k]["name"] or "~").lower()):
            groups_by_kind[nodes[child]["kind"]].append(infra_card(child))
        groups = []
        for kind, label, icon in GROUP_ORDER:
            if groups_by_kind.get(kind):
                groups.append({"id": f"{key}-{kind}", "label": label, "icon": icon, "leaf": False,
                               "children": groups_by_kind[kind]})
        manual = sorted(manual_by_parent.get(key, []), key=lambda n: n["name"].lower())
        if manual:
            kind, label, icon = MANUAL_GROUP
            groups.append({"id": f"{key}-{kind}", "label": label, "icon": icon, "leaf": False, "children": manual})
        wired = sorted(wired_by_parent.get(key, []), key=lambda n: n["name"].lower())
        if wired:
            groups.append({"id": f"{key}-wired", "label": f"Wired clients ({len(wired)})",
                           "icon": "mdi:ethernet", "leaf": True, "children": wired})
        by_ssid: dict[str, list[dict]] = defaultdict(list)
        for client in clients_by_ap.get(key, []):
            cmac = norm_mac(client.get("clientMac"))
            by_ssid[client.get("ssid") or "Unknown SSID"].append(
                _wireless_node(client, ha_names, names, idents.get(cmac or ""))
            )
        for ssid in sorted(by_ssid):
            kids = sorted(by_ssid[ssid], key=lambda n: n["name"].lower())
            groups.append({"id": f"{key}-ssid-{ssid}", "label": f"{ssid} ({len(kids)})",
                           "icon": "mdi:wifi", "leaf": True, "children": kids})
        client_count = len(clients_by_ap.get(key, []))
        if client_count:
            details.insert(1, ("Wireless clients", client_count))
        radios = extras.radios.get(key) or []
        for i, r in enumerate(radios):
            band = r.get("band") or r.get("radio") or "Radio"
            clients_on = sum(1 for c in clients_by_ap.get(key, []) if (c.get("band") or "").lower() == band.lower())
            details.insert(2 + i, (f"{band} radio", radio_text(r) + (f" · {clients_on} clients" if clients_on else "")))
        if extras.rf_profiles.get(key):
            details.append(("RF profile", extras.rf_profiles[key]))
        graphic = None
        if node["kind"] == "switch":
            labels = {}
            for (sw, port), nb in port_neighbor.items():
                if sw == key and nb in nodes:
                    labels[port] = nodes[nb]["name"] or nodes[nb].get("mac") or "LLDP neighbour"
            for (owner, port), items in port_buckets.items():
                if owner == key and port not in labels:
                    labels[port] = f"{len(items)} wired client{'s' if len(items) != 1 else ''}"
            serial = next((s for s, k in serial_to_key.items() if k == key), None)
            graphic = switch_graphic(node.get("model"), ports_by_serial.get(serial, []), labels,
                                     extras.port_layouts.get(model_slug(node.get("model"))),
                                     extras.port_vlans.get(serial or ""))
            ups = [p for p in graphic["ports"].values() if p.get("up")]
            if graphic["known"]:
                details.insert(1, ("Ports up", f"{len(ups)} of {len(graphic['ports'])}"))
        elif node["kind"] == "ap":
            graphic = ap_graphic(node.get("model"))
        elif node["kind"] == "gateway":
            graphic = {"type": "gateway"}
        image = (images or {}).get(model_slug(node.get("model")))
        if image:
            graphic = {**(graphic or {"type": node["kind"]}), "image": image}
        return {
            "id": key,
            "kind": node["kind"],
            "name": name,
            "subtitle": " · ".join(s for s in sub if s),
            "icon": KIND_ICONS.get(node["kind"], "mdi:help-network"),
            "status": node["status"],
            "badge": client_count or None,
            "graphic": graphic,
            "mac": node.get("mac"),
            "ip": node.get("ip"),
            "services": extras.services.get(node.get("ip") or "") if node["kind"] in ("endpoint", "bridge") else None,
            "link": edge.get(key),
            "details": [[k, str(v)] for k, v in details if v not in (None, "")],
            "groups": groups,
        }

    top_cards = [infra_card(k) for k in children.get(None, [])]
    has_gateway = any(c["kind"] == "gateway" for c in top_cards)
    internet = {
        "id": "internet",
        "kind": "internet",
        "name": "Internet",
        "subtitle": "",
        "icon": KIND_ICONS["internet"],
        "status": "online" if has_gateway else "unknown",
        "details": [["Note", "Drawn automatically; Cirrus does not report the WAN link."]],
        "groups": [],
    }
    if wan:
        state = (wan.get("internet") or wan.get("status") or "").lower()
        internet["status"] = {"ok": "online", "error": "offline"}.get(state, internet["status"])
        internet["subtitle"] = wan.get("isp") or ""
        internet["wan"] = wan
        internet["details"] = [[k, str(v)] for k, v in (
            ("WAN status", wan.get("status")),
            ("Internet", wan.get("internet")),
            ("WAN IP", wan.get("wan_ip")),
            ("ISP", wan.get("isp")),
            ("Source", "UniFi gateway"),
        ) if v]
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for card in top_cards:
        by_kind[card["kind"]].append(card)
    for kind, label, icon in GROUP_ORDER:
        if by_kind.get(kind):
            internet["groups"].append({"id": f"internet-{kind}", "label": label, "icon": icon,
                                       "leaf": False, "children": by_kind.pop(kind)})
    for kind, cards in by_kind.items():
        internet["groups"].append({"id": f"internet-{kind}", "label": kind.title(), "icon": "mdi:lan",
                                   "leaf": False, "children": cards})

    wired_total = sum(len(v) for v in wired_by_parent.values())
    ssid_counts: dict[str, int] = defaultdict(int)
    for c in site_clients:
        ssid_counts[c.get("ssid") or "Unknown"] += 1
    used: set[int] = set()
    for e in edge.values():
        used.update(e.get("vlans") or [])
    for c in site_clients:
        if c.get("vlanId"):
            used.add(c["vlanId"])
    vlan_catalogue = {
        str(v): {"name": extras.vlans.get(str(v), {}).get("name") or f"VLAN {v}", "up": extras.vlans.get(str(v), {}).get("up", True)}
        for v in sorted(used)
    }
    return {
        "vlans": vlan_catalogue,
        "entry_id": extras.entry_id,
        "site_id": site_id,
        "site_name": site.get("name"),
        "mac_table": bool(switch_tables),
        "summary": {
            "wireless_clients": len(site_clients),
            "wired_clients": wired_total,
            "access_points": sum(1 for n in nodes.values() if n["kind"] == "ap"),
            "switches": sum(1 for n in nodes.values() if n["kind"] == "switch"),
            "manual_devices": sum(len(v) for v in manual_by_parent.values()),
            "ssids": dict(ssid_counts),
        },
        "root": internet,
    }
