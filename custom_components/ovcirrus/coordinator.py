"""Data coordinator for OmniVista Cirrus."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CirrusApiError, CirrusAuthError, CirrusClient
from .assets import AssetCache
from .history import RateHistory
from .const import (
    CONF_DISCOVERY_INTERVAL,
    CONF_DNS_MODE,
    CONF_DNS_PREFER,
    CONF_DNS_SERVERS,
    CONF_DNS_SHORT,
    CONF_MANUAL_DEVICES,
    CONF_ORG_ID,
    CONF_SCAN_INTERVAL,
    CONF_SITE_IDS,
    CONF_SSH_ARP,
    CONF_SSH_ENABLED,
    CONF_SSH_HOSTS,
    CONF_SSH_INTERVAL,
    CONF_SSH_KEY_PATH,
    CONF_SSH_KNOWN_HOSTS,
    CONF_SSH_PORT,
    CONF_SSH_USERNAME,
    PHOTOS_URL,
    CONF_RATE_FINE_HOURS,
    CONF_RATE_INTERVAL,
    CONF_RATE_LONG_DAYS,
    CONF_RATE_PERSIST,
    DEFAULT_RATE_FINE_HOURS,
    DEFAULT_RATE_INTERVAL,
    DEFAULT_RATE_LONG_DAYS,
    CONF_UNIFI_ENABLED,
    CONF_UNIFI_HOST,
    CONF_UNIFI_PASSWORD,
    CONF_UNIFI_SITE,
    CONF_UNIFI_USERNAME,
    CONF_UNIFI_VERIFY_SSL,
    DEFAULT_DISCOVERY_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSH_INTERVAL,
    DEFAULT_SSH_PORT,
    DOMAIN,
    EVENT_NEW_CLIENT,
    TOPOLOGY_INTERVAL,
)
from homeassistant.helpers import issue_registry as ir

from .discovery import discover, full_scan, nmap_available
from .dns import ReverseResolver, parse_servers
from .identify import HaDevice, identify, is_private_mac
from .mac_table import SshError, SwitchTables, fetch_switch_tables, norm_mac
from .store import EntryStore
from .topology import MapExtras, build_site_map, model_slug, walk_nodes
from .unifi import UnifiClient, UnifiError

_LOGGER = logging.getLogger(__name__)


@dataclass
class CirrusData:
    """Snapshot of everything fetched in one refresh."""

    sites: dict[str, dict] = field(default_factory=dict)
    devices: dict[str, dict] = field(default_factory=dict)  # serial -> device
    clients: dict[str, dict] = field(default_factory=dict)  # MAC -> wireless session
    topology: dict[str, dict] = field(default_factory=dict)  # site_id -> raw topology
    node_status: dict[str, str] = field(default_factory=dict)  # MAC -> online/offline
    lldp: dict[str, list] = field(default_factory=dict)  # switch serial -> lldp entries
    ports: dict[str, list] = field(default_factory=dict)  # switch serial -> port status
    port_vlans: dict[str, dict] = field(default_factory=dict)  # serial -> port -> {"untagged", "tagged"}
    vlans: dict[str, dict] = field(default_factory=dict)  # VLAN id -> {"name", "up"}
    dns_names: dict[str, str] = field(default_factory=dict)  # ip -> PTR name
    radios: dict[str, list] = field(default_factory=dict)  # AP serial -> [radio dicts]
    rf_profiles: dict[str, str] = field(default_factory=dict)  # AP serial -> RF profile name
    switch_tables: dict[str, SwitchTables] = field(default_factory=dict)
    ssh_errors: dict[str, str] = field(default_factory=dict)
    unifi: dict = field(default_factory=dict)  # {"wan": {...}, "clients": {mac: {...}}}
    unifi_error: str | None = None
    maps: dict[str, dict] = field(default_factory=dict)  # site_id -> card tree


def parse_port_vlans(rows: list[dict]) -> dict[str, dict]:
    """Cirrus ports-vlan-list -> {port: {"untagged": [ids], "tagged": [ids]}} (active VLANs only)."""
    result: dict[str, dict] = {}
    for row in rows or []:
        port = row.get("slot")
        if not port:
            continue
        untagged = [v["vlanNumber"] for v in row.get("untaggedVlans") or [] if v.get("vlanNumber") is not None]
        tagged = [
            v["vlanNumber"]
            for v in row.get("taggedVlans") or []
            if v.get("vlanNumber") is not None and v.get("status") != "inactive"
        ]
        if not untagged and row.get("defaultVlan") is not None:
            untagged = [row["defaultVlan"]]
        result[port] = {"untagged": untagged, "tagged": sorted(set(tagged))}
    return result


def parse_ssh_hosts(value: Any) -> dict[str, str]:
    """SSH addresses per switch serial.

    Current format: {serial: host} from the setup form ('' means do not SSH to that switch).
    Older format: 'SERIAL=host' lines; a bare address applies to every switch ('*').
    """
    if isinstance(value, dict):
        return {str(k): str(v or "").strip() for k, v in value.items()}
    result: dict[str, str] = {}
    for part in (value or "").replace("\n", ",").split(","):
        part = part.strip()
        if "=" in part:
            serial, host = part.split("=", 1)
            if serial.strip() and host.strip():
                result[serial.strip()] = host.strip()
        elif part:
            result["*"] = part
    return result


class CirrusCoordinator(DataUpdateCoordinator[CirrusData]):
    """Polls Cirrus (plus switches over SSH, the UniFi gateway and DNS, when enabled)."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: CirrusClient,
        assets: AssetCache,
        store: EntryStore,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        )
        self.client = client
        self.assets = assets
        self.store = store
        self.org_id: str = entry.data[CONF_ORG_ID]
        self._topology_at = 0.0
        self._ssh_at = 0.0
        self._cache = CirrusData()
        self._scanning: set[str] = set()
        opts = entry.options
        mode = opts.get(CONF_DNS_MODE, "off")
        self.resolver: ReverseResolver | None = (
            ReverseResolver(mode, parse_servers(opts.get(CONF_DNS_SERVERS))) if mode in ("system", "server") else None
        )
        self.unifi: UnifiClient | None = (
            UnifiClient(
                opts.get(CONF_UNIFI_HOST, ""),
                opts.get(CONF_UNIFI_USERNAME, ""),
                opts.get(CONF_UNIFI_PASSWORD, ""),
                opts.get(CONF_UNIFI_SITE, "default"),
                bool(opts.get(CONF_UNIFI_VERIFY_SSL, False)),
            )
            if opts.get(CONF_UNIFI_ENABLED) and opts.get(CONF_UNIFI_HOST)
            else None
        )

        self.rate_interval = int(opts.get(CONF_RATE_INTERVAL, DEFAULT_RATE_INTERVAL))
        self.rates = RateHistory(
            fine_seconds=int(float(opts.get(CONF_RATE_FINE_HOURS, DEFAULT_RATE_FINE_HOURS)) * 3600),
            long_seconds=int(float(opts.get(CONF_RATE_LONG_DAYS, DEFAULT_RATE_LONG_DAYS)) * 86400),
        )
        self._rates_unsub = None
        self._rates_busy = False
        self._rate_store: Store | None = (
            Store(hass, 1, f"{DOMAIN}.rates.{entry.entry_id}") if opts.get(CONF_RATE_PERSIST, True) else None
        )
        self._rate_save_pending = False

    RATE_SAVE_DELAY = 300  # write the saved history at most every 5 minutes (and when HA stops)

    async def start_rate_history(self) -> None:
        """Read the gateway's port rates into the history every sample interval (UniFi only)."""
        if not self.unifi or self._rates_unsub:
            return
        if self._rate_store:
            try:
                self.rates.load(await self._rate_store.async_load())
            except Exception as err:  # a damaged file must not stop the integration
                _LOGGER.warning("Could not read the saved bandwidth history: %s", err)
        else:  # saving turned off: drop any history saved earlier
            await Store(self.hass, 1, f"{DOMAIN}.rates.{self.config_entry.entry_id}").async_remove()
        self._rates_unsub = async_track_time_interval(
            self.hass, self._poll_rates, timedelta(seconds=max(5, self.rate_interval))
        )

    def _rate_data(self) -> dict:
        self._rate_save_pending = False
        return self.rates.to_dict()

    async def _poll_rates(self, _now=None) -> None:
        if self._rates_busy or not self.unifi:
            return
        self._rates_busy = True
        try:
            ports = await self.unifi.gateway_ports()
        except UnifiError as err:
            _LOGGER.debug("UniFi rate poll failed: %s", err)
            return
        finally:
            self._rates_busy = False
        for p in ports:
            key = p.get("ifname") or str(p.get("idx"))
            self.rates.names[key] = {"name": p.get("name"), "role": p.get("role"), "speed": p.get("speed"), "up": p.get("up")}
            if p.get("up"):
                self.rates.add(key, p.get("rx"), p.get("tx"))
        if self._rate_store and not self._rate_save_pending:
            self._rate_save_pending = True
            self._rate_store.async_delay_save(self._rate_data, self.RATE_SAVE_DELAY)

    @property
    def site_ids(self) -> list[str]:
        return list(self.config_entry.data.get(CONF_SITE_IDS) or [])

    @property
    def manual_devices(self) -> list[dict]:
        """Devices added from the map or the options (older options entries are still honoured)."""
        stored = {d.get("id"): d for d in self.store.manual}
        for dev in self.config_entry.options.get(CONF_MANUAL_DEVICES) or []:
            stored.setdefault(dev.get("id"), dev)
        return list(stored.values())

    def services_view(self) -> dict[str, dict]:
        """Scan results with your port names applied."""
        view: dict[str, dict] = {}
        for ip, result in self.store.services.items():
            services = []
            for svc in result.get("services") or []:
                label, scope = self.store.port_label(ip, svc["port"])
                if label:
                    svc = {**svc, "service": label, "label_scope": scope, "unknown": False, "labelled": True}
                services.append(svc)
            view[ip] = {**result, "services": services}
        return view

    async def async_shutdown(self) -> None:
        if self._rates_unsub:
            self._rates_unsub()
            self._rates_unsub = None
        if self._rate_store and self.unifi:
            await self._rate_store.async_save(self._rate_data())
        await super().async_shutdown()
        if self.unifi:
            await self.unifi.close()
        await self.store.async_save()

    async def _async_update_data(self) -> CirrusData:
        try:
            return await self._update()
        except CirrusAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CirrusApiError as err:
            raise UpdateFailed(str(err)) from err

    # ---------------------------------------------------------------- fetch
    async def _update(self) -> CirrusData:
        data = CirrusData()
        prev = self._cache
        now = time.monotonic()

        sites = await self.client.get_sites(self.org_id)
        wanted = set(self.site_ids) or {s["id"] for s in sites}
        data.sites = {s["id"]: s for s in sites if s["id"] in wanted}

        devices = await self.client.get_devices(self.org_id)
        data.devices = {
            d["serialNumber"]: d
            for d in devices
            if d.get("serialNumber") and (d.get("site") or {}).get("id") in data.sites
        }
        clients = await self.client.get_wireless_clients(self.org_id, list(data.sites))
        data.clients = {
            norm_mac(c.get("clientMac")) or c.get("clientMac"): c for c in clients if not c.get("terminated")
        }

        if now - self._topology_at > TOPOLOGY_INTERVAL or not prev.topology:
            await self._update_topology(data, prev)
            self._topology_at = now
        else:
            data.topology, data.lldp, data.ports = prev.topology, prev.lldp, prev.ports
            data.port_vlans, data.vlans = prev.port_vlans, prev.vlans

        for topo in data.topology.values():
            for node in topo.get("nodes") or []:
                mac = norm_mac(node.get("macAddress"))
                if mac:
                    reach = (node.get("reachable") or "").lower()
                    data.node_status[mac] = {"on": "online", "off": "offline"}.get(reach, "unknown")

        await self._update_radios(data, prev)
        await self._update_ssh(data, prev, now)
        await self._update_unifi(data, prev)
        if self.resolver:
            data.dns_names = await self._resolve_names(data)

        self.assets.ensure_oui()
        data.maps = self._build_maps(data)
        self._observe(data)
        self._schedule_discovery(data)
        self._cache = data
        return data

    async def _update_topology(self, data: CirrusData, prev: CirrusData) -> None:
        switches = [s for s, d in data.devices.items() if d.get("deviceFamily") == "AOS"]
        topo_results, lldp_results, port_results, vlan_results, vlan_list = await asyncio.gather(
            asyncio.gather(*(self.client.get_topology(self.org_id, s) for s in data.sites), return_exceptions=True),
            asyncio.gather(*(self.client.get_lldp(self.org_id, s) for s in switches), return_exceptions=True),
            asyncio.gather(*(self.client.get_ports(self.org_id, s) for s in switches), return_exceptions=True),
            asyncio.gather(*(self.client.get_port_vlans(self.org_id, s) for s in switches), return_exceptions=True),
            self.client.get_vlans(self.org_id, list(data.sites)),
            return_exceptions=True,
        )
        for serial, res in zip(switches, vlan_results if isinstance(vlan_results, list) else []):
            data.port_vlans[serial] = prev.port_vlans.get(serial, {}) if isinstance(res, Exception) else parse_port_vlans(res)
        if isinstance(vlan_list, Exception):
            _LOGGER.debug("VLAN list failed: %s", vlan_list)
            data.vlans = prev.vlans
        else:
            data.vlans = {
                str(v["vlanId"]): {
                    "name": v.get("description") or f"VLAN {v['vlanId']}",
                    "up": bool((v.get("operStatus") or {}).get("enabled")),
                }
                for v in vlan_list
                if v.get("vlanId") is not None
            }
        for sid, res in zip(data.sites, topo_results):
            if isinstance(res, Exception):
                _LOGGER.warning("Topology fetch failed for site %s: %s", sid, res)
                if sid in prev.topology:
                    data.topology[sid] = prev.topology[sid]
            else:
                data.topology[sid] = res
        for serial, res in zip(switches, lldp_results):
            data.lldp[serial] = prev.lldp.get(serial, []) if isinstance(res, Exception) else res
        for serial, res in zip(switches, port_results):
            data.ports[serial] = prev.ports.get(serial, []) if isinstance(res, Exception) else res

    async def _update_radios(self, data: CirrusData, prev: CirrusData) -> None:
        aps = {s: d for s, d in data.devices.items() if d.get("deviceFamily") == "AP" and d.get("macAddress")}
        sem = asyncio.Semaphore(8)

        async def one(serial: str, dev: dict):
            async with sem:
                return serial, await self.client.get_rf_details(self.org_id, dev["macAddress"])

        results = await asyncio.gather(*(one(s, d) for s, d in aps.items()), return_exceptions=True)
        for (serial, _), res in zip(aps.items(), results):
            if isinstance(res, Exception):
                _LOGGER.debug("RF details failed for %s: %s", serial, res)
                data.radios[serial] = prev.radios.get(serial, [])
                if serial in prev.rf_profiles:
                    data.rf_profiles[serial] = prev.rf_profiles[serial]
                continue
            rf = res[1] or {}
            data.radios[serial] = sorted(rf.get("radios") or [], key=lambda r: r.get("radio") or "")
            if rf.get("rfProfileName"):
                data.rf_profiles[serial] = rf["rfProfileName"]

    async def _update_ssh(self, data: CirrusData, prev: CirrusData, now: float) -> None:
        opts = self.config_entry.options
        if not opts.get(CONF_SSH_ENABLED):
            return
        interval = opts.get(CONF_SSH_INTERVAL, DEFAULT_SSH_INTERVAL)
        if now - self._ssh_at < interval and prev.switch_tables:
            data.switch_tables, data.ssh_errors = prev.switch_tables, prev.ssh_errors
            return
        overrides = parse_ssh_hosts(opts.get(CONF_SSH_HOSTS))
        targets = {}
        for serial, dev in data.devices.items():
            if dev.get("deviceFamily") != "AOS":
                continue
            host = overrides[serial] if serial in overrides else overrides.get("*") or dev.get("ipAddress")
            if host:  # an empty address in the form means "skip this switch"
                targets[serial] = host

        async def one(serial: str, host: str):
            return serial, await fetch_switch_tables(
                host,
                opts.get(CONF_SSH_USERNAME, ""),
                opts.get(CONF_SSH_KEY_PATH) or None,
                opts.get(CONF_SSH_KNOWN_HOSTS) or None,
                int(opts.get(CONF_SSH_PORT, DEFAULT_SSH_PORT)),
                bool(opts.get(CONF_SSH_ARP, True)),
            )

        results = await asyncio.gather(*(one(s, h) for s, h in targets.items()), return_exceptions=True)
        for (serial, host), res in zip(targets.items(), results):
            if isinstance(res, Exception):
                msg = str(res) if isinstance(res, SshError) else f"{host}: {res!r}"
                data.ssh_errors[serial] = msg
                _LOGGER.warning("MAC table read failed for %s: %s", serial, msg)
                if serial in prev.switch_tables:
                    data.switch_tables[serial] = prev.switch_tables[serial]
            else:
                data.switch_tables[serial] = res[1]
        self._ssh_at = now

    async def _update_unifi(self, data: CirrusData, prev: CirrusData) -> None:
        if not self.unifi:
            return
        try:
            snap = await self.unifi.snapshot()
        except UnifiError as err:
            data.unifi, data.unifi_error = prev.unifi, str(err)
            _LOGGER.warning("UniFi gateway read failed: %s", err)
            return
        data.unifi = {
            "wan": snap["wan"],
            "gateway": snap.get("gateway"),
            "clients": {
                norm_mac(c.get("mac")): c for c in snap["clients"] if norm_mac(c.get("mac"))
            },
        }

    async def _resolve_names(self, data: CirrusData) -> dict[str, str]:
        ips: set[str] = {c.get("clientIp") for c in data.clients.values() if c.get("clientIp")}
        for tables in data.switch_tables.values():
            ips.update(tables.arp.values())
        for topo in data.topology.values():
            for node in topo.get("nodes") or []:
                for addr in node.get("managementAddresses") or []:
                    if addr.get("address"):
                        ips.add(addr["address"])
        ips.update(d.get("ipAddress") for d in data.devices.values() if d.get("ipAddress"))
        ips.update(c.get("ip") for c in (data.unifi.get("clients") or {}).values() if c.get("ip"))
        ips.update(m.get("ip") for m in self.manual_devices if m.get("ip"))
        try:
            return await self.resolver.resolve_many(ips)
        except Exception as err:  # noqa: BLE001 - DNS must never break polling
            _LOGGER.debug("Reverse DNS failed: %s", err)
            return {}

    # ---------------------------------------------------------------- identity
    def _ha_devices(self) -> dict[str, HaDevice]:
        """MAC -> what other HA integrations know about the device."""
        result: dict[str, HaDevice] = {}
        entries = {e.entry_id: e.domain for e in self.hass.config_entries.async_entries()}
        for device in dr.async_get(self.hass).devices.values():
            if any(ident[0] == DOMAIN for ident in device.identifiers):
                continue
            macs = [norm_mac(v) for t, v in device.connections if t == dr.CONNECTION_NETWORK_MAC]
            macs = [m for m in macs if m]
            if not macs:
                continue
            domain = next((entries[e] for e in device.config_entries if e in entries), None)
            info = HaDevice(
                name=device.name_by_user or device.name,
                manufacturer=device.manufacturer,
                model=device.model,
                domain=domain,
            )
            for mac in macs:
                result.setdefault(mac, info)
        return result

    def _identities(self, data: CirrusData, ha: dict[str, HaDevice]) -> dict[str, dict]:
        unifi_clients = data.unifi.get("clients") or {}
        candidates: dict[str, dict] = {}
        for mac, c in data.clients.items():
            candidates[mac] = {"hostname": c.get("deviceName"), "os": c.get("osType"), "type": c.get("deviceType")}
        for tables in data.switch_tables.values():
            for entry in tables.macs:
                candidates.setdefault(entry.mac, {})
        for mac in unifi_clients:
            candidates.setdefault(mac, {})
        for dev in self.manual_devices:
            mac = norm_mac(dev.get("mac"))
            if mac:
                candidates.setdefault(mac, {})

        identities: dict[str, dict] = {}
        for mac, info in candidates.items():
            uc = unifi_clients.get(mac) or {}
            override = self.store.overrides.get(mac) or {}
            hostname = info.get("hostname") or uc.get("hostname")
            oui = None if is_private_mac(mac) else self.assets.vendor(mac)
            ident = identify(
                mac,
                hostname,
                oui_vendor=oui,
                cirrus_os=info.get("os"),
                cirrus_type=info.get("type"),
                ha=ha.get(mac),
                unifi_vendor=uc.get("oui"),
                override_type=override.get("type"),
            )
            if uc.get("name"):
                ident.sources.append("UniFi")
            identities[mac] = {
                "name": override.get("name") or uc.get("name"),  # user-set names win
                "hostname": hostname,
                "ip": uc.get("ip") or uc.get("last_ip"),
                "label": ident.describe(),
                "type": ident.type,
                "type_label": ident.type_label,
                "icon": ident.icon,
                "vendor": ident.vendor,
                "brand": ident.brand,
                "model": ident.model,
                "private_mac": ident.private_mac,
                "logo": self.assets.logo(ident.logo_sources),
                "sources": list(dict.fromkeys(ident.sources)),
                "overridden": bool(override),
            }
        return identities

    def _device_images(self, data: CirrusData) -> tuple[dict[str, str], dict[str, dict]]:
        """Model slug -> image URL (your files > bundled > NetBox) and NetBox port layouts."""
        images = dict(self.hass.data.get(DOMAIN, {}).get("images", {}))
        layouts: dict[str, dict] = {}
        models = {(d.get("modelName"), "Alcatel-Lucent") for d in data.devices.values() if d.get("deviceFamily") == "AOS"}
        gw_model = (data.unifi.get("wan") or {}).get("gateway_model")
        if gw_model:
            models.add((gw_model, "Ubiquiti"))
        for model, vendor in models:
            if not model:
                continue
            slug = model_slug(model)
            if slug not in images:
                url = self.assets.netbox_image(vendor, model)
                if url:
                    images[slug] = url
            if vendor == "Alcatel-Lucent":
                ports = self.assets.netbox_ports(vendor, model)
                if ports:
                    layouts[slug] = ports
        return images, layouts

    # ---------------------------------------------------------------- map
    def _build_maps(self, data: CirrusData) -> dict[str, dict]:
        ha = self._ha_devices()
        ha_names = {mac: d.name for mac, d in ha.items() if d.name}
        identities = self._identities(data, ha)
        images, layouts = self._device_images(data)
        opts = self.config_entry.options
        extras = MapExtras(
            identities=identities,
            manual=self.manual_devices,
            services=self.services_view(),
            wan=(data.unifi.get("wan") or None),
            gateway=(data.unifi.get("gateway") or None),
            port_layouts=layouts,
            port_vlans=data.port_vlans,
            vlans=data.vlans,
            radios={norm_mac(data.devices[s].get("macAddress")): r for s, r in data.radios.items()},
            rf_profiles={norm_mac(data.devices[s].get("macAddress")): p for s, p in data.rf_profiles.items()},
            entry_id=self.config_entry.entry_id,
            unifi_ips={m: c.get("ip") for m, c in (data.unifi.get("clients") or {}).items() if c.get("ip")},
        )
        maps: dict[str, dict] = {}
        for sid, site in data.sites.items():
            try:
                maps[sid] = build_site_map(
                    site,
                    list(data.devices.values()),
                    data.topology.get(sid) or {},
                    data.lldp,
                    list(data.clients.values()),
                    data.switch_tables,
                    ha_names,
                    ports_by_serial=data.ports,
                    dns_names=data.dns_names,
                    prefer_dns=bool(opts.get(CONF_DNS_PREFER, False)),
                    dns_short=bool(opts.get(CONF_DNS_SHORT, True)),
                    images=images,
                    extras=extras,
                )
                self._apply_photos(maps[sid])
                maps[sid]["ssh_errors"] = list(data.ssh_errors.values())
                maps[sid]["positions"] = dict(self.store.positions.get(sid, {}))
                maps[sid]["switches"] = [
                    {"serial": s, "name": d.get("name") or s}
                    for s, d in data.devices.items()
                    if d.get("deviceFamily") == "AOS" and (d.get("site") or {}).get("id") == sid
                ]
                if data.unifi_error:
                    maps[sid]["unifi_error"] = data.unifi_error
            except Exception:  # noqa: BLE001 - never let the map break polling
                _LOGGER.exception("Failed to build network map for site %s", sid)
        return maps

    def _observe(self, data: CirrusData) -> None:
        """New-device detection over everything placed on the maps."""
        seen: dict[str, dict] = {}
        for site_map in data.maps.values():
            for node in walk_nodes(site_map["root"]):
                mac = node.get("mac")
                if mac and node.get("kind") in ("wireless", "wired", "manual"):
                    seen[mac] = {
                        "name": node["name"],
                        "hostname": node.get("hostname"),
                        "vendor": node.get("vendor"),
                        "ip": node.get("ip"),
                        "where": node.get("where"),
                        "type": node.get("type_label"),
                        "site": site_map.get("site_name"),
                    }
        for new in self.store.observe(seen):
            _LOGGER.info("New device on the network: %s", new)
            self.hass.bus.async_fire(EVENT_NEW_CLIENT, {"entry_id": self.config_entry.entry_id, **new})
        for site_map in data.maps.values():
            for node in walk_nodes(site_map["root"]):
                if node.get("mac") and self.store.is_new(node["mac"]):
                    node["is_new"] = True
            site_map["summary"]["new_devices"] = len(self.store.recent_new())

    def rebuild(self) -> None:
        """Re-render maps from cached data (after an override, scan result or new asset)."""
        if not self.data:
            return
        self.data.maps = self._build_maps(self.data)
        self._observe(self.data)
        self.async_set_updated_data(self.data)

    def _apply_photos(self, site_map: dict) -> None:
        """Your uploaded pictures, by MAC (so they follow a client between Wi-Fi and cable) or map id."""
        photos = self.store.photos
        for node in walk_nodes(site_map["root"]):
            if node.get("kind") in ("internet", "ap", "switch", "gateway"):
                continue
            key = norm_mac(node.get("mac")) or node.get("id")
            node["photo_key"] = key
            p = photos.get(key or "") or photos.get(node.get("id") or "")
            if p:
                node["photo"] = f"{PHOTOS_URL}/{p['file']}?v={p.get('v', 0)}"

    # ---------------------------------------------------------------- discovery
    def _schedule_discovery(self, data: CirrusData) -> None:
        if "nmap" not in self.hass.data.get(DOMAIN, {}):
            self.check_nmap(wanted=any(d.get("scan") == "full" for d in self.manual_devices))
        hours = float(self.config_entry.options.get(CONF_DISCOVERY_INTERVAL, DEFAULT_DISCOVERY_INTERVAL))
        for dev in self.manual_devices:
            ip = dev.get("ip")
            if not dev.get("discover") or not ip or ip in self._scanning:
                continue
            last = (self.store.services.get(ip) or {}).get("ts", 0)
            if time.time() - last > hours * 3600:
                mode = "full" if dev.get("scan") == "full" else "quick"
                self.hass.async_create_background_task(self.async_discover(ip, mode), f"ovcirrus discover {ip}")

    def check_nmap(self, wanted: bool) -> bool:
        """Raise / clear the Repairs notice about nmap. Returns True when nmap is installed."""
        present = nmap_available()
        self.hass.data.setdefault(DOMAIN, {})["nmap"] = present
        if present or not wanted:
            ir.async_delete_issue(self.hass, DOMAIN, "nmap_missing")
        else:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                "nmap_missing",
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="nmap_missing",
                learn_more_url="https://github.com/daveo2k/home-network-cirrus-plugin#installing-nmap",
            )
        return present

    async def async_discover(self, ip: str, mode: str = "quick") -> dict:
        if ip in self._scanning:
            return self.services_view().get(ip) or {}
        self._scanning.add(ip)
        if mode == "full":
            self.check_nmap(wanted=True)
        try:
            result = await (full_scan(ip) if mode == "full" else discover(ip))
            self.store.set_services(ip, result)
            self.rebuild()
            return self.services_view().get(ip, result)
        finally:
            self._scanning.discard(ip)

    def wired_count(self, data: CirrusData | None = None) -> int:
        data = data or self.data
        return sum(m["summary"]["wired_clients"] for m in data.maps.values())


def device_status(data: CirrusData, device: dict[str, Any]) -> str:
    mac = norm_mac(device.get("macAddress"))
    return data.node_status.get(mac or "", "unknown")
