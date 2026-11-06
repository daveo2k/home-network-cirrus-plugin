"""OmniVista Cirrus integration for Home Assistant."""

from __future__ import annotations

import ipaddress
import logging
import time
from pathlib import Path

import voluptuous as vol

from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import CirrusClient
from .assets import AssetCache
from .backup import BackupError, async_build_backup, async_restore_backup
from homeassistant.components import panel_custom

from .const import (
    CONF_PANEL_ADMIN,
    CONF_PANEL_TITLE,
    CONF_SHOW_PANEL,
    DEFAULT_PANEL_TITLE,
    PANEL_ELEMENT,
    PANEL_URL_PATH,
    CACHE_DIR,
    CACHE_URL,
    CARD_URL,
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_HOST,
    DOMAIN,
    IMAGES_URL,
    PHOTOS_DIR,
    PHOTOS_URL,
    USER_IMAGES_DIR,
    VERSION,
)
from .coordinator import CirrusCoordinator
from .history import RANGES
from .identify import TYPES
from .mac_table import norm_mac
from .photos import KEY_RE, PhotoError
from . import photos as photo_files
from .store import EntryStore
from .topology import model_slug

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CirrusConfigEntry = ConfigEntry  # runtime_data: CirrusCoordinator


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the map card and websocket API once."""
    frontend_dir = Path(__file__).parent / "frontend"
    cache_dir = Path(hass.config.path(CACHE_DIR))
    photos_dir = Path(hass.config.path(PHOTOS_DIR))
    await hass.async_add_executor_job(lambda: cache_dir.mkdir(parents=True, exist_ok=True))
    await hass.async_add_executor_job(lambda: photos_dir.mkdir(parents=True, exist_ok=True))
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(CARD_URL, str(frontend_dir / "ovcirrus-network-map.js"), cache_headers=False),
            StaticPathConfig(IMAGES_URL, str(frontend_dir / "images"), cache_headers=True),
            StaticPathConfig(CACHE_URL, str(cache_dir), cache_headers=True),
            StaticPathConfig(PHOTOS_URL, str(photos_dir), cache_headers=True),  # URLs carry ?v=
        ]
    )
    assets = AssetCache(
        async_get_clientsession(hass),
        cache_dir,
        CACHE_URL,
        hass.async_add_executor_job,
        lambda coro, name: hass.async_create_background_task(coro, name),
    )
    await assets.async_load()
    pending = {"handle": None}

    @callback
    def _assets_changed() -> None:
        # A logo/image/vendor list arrived: re-render maps once, a moment later.
        if pending["handle"]:
            return

        @callback
        def _run() -> None:
            pending["handle"] = None
            for entry in hass.config_entries.async_entries(DOMAIN):
                if entry.state is ConfigEntryState.LOADED:
                    entry.runtime_data.rebuild()

        pending["handle"] = hass.loop.call_later(3, _run)

    assets.on_change = _assets_changed
    hass.data.setdefault(DOMAIN, {})["assets"] = assets
    hass.data.setdefault(DOMAIN, {})["images"] = await hass.async_add_executor_job(
        _scan_images, frontend_dir / "images", Path(hass.config.path(USER_IMAGES_DIR))
    )
    frontend.add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
    websocket_api.async_register_command(hass, ws_topology)
    websocket_api.async_register_command(hass, ws_set_override)
    websocket_api.async_register_command(hass, ws_discover)
    websocket_api.async_register_command(hass, ws_set_position)
    websocket_api.async_register_command(hass, ws_save_device)
    websocket_api.async_register_command(hass, ws_remove_device)
    websocket_api.async_register_command(hass, ws_label_port)
    websocket_api.async_register_command(hass, ws_save_view)
    websocket_api.async_register_command(hass, ws_reset_positions)
    websocket_api.async_register_command(hass, ws_backup)
    websocket_api.async_register_command(hass, ws_rates)
    websocket_api.async_register_command(hass, ws_set_photo)
    websocket_api.async_register_command(hass, ws_restore)
    return True


def _scan_images(builtin: Path, user: Path) -> dict[str, str]:
    """Map model slug -> image URL. Files in /config/www/ovcirrus override the bundled ones."""
    images: dict[str, str] = {}
    for folder, base in ((builtin, IMAGES_URL), (user, "/local/ovcirrus")):
        if not folder.is_dir():
            continue
        for file in folder.iterdir():
            if file.suffix.lower() in (".png", ".webp", ".jpg", ".jpeg", ".svg"):
                images[model_slug(file.stem)] = f"{base}/{file.name}"
    return images


async def async_setup_entry(hass: HomeAssistant, entry: CirrusConfigEntry) -> bool:
    client = CirrusClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_APP_ID],
        entry.data[CONF_APP_SECRET],
    )
    store = EntryStore(hass, entry.entry_id)
    await store.async_load()
    coordinator = CirrusCoordinator(hass, entry, client, hass.data[DOMAIN]["assets"], store)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await coordinator.start_rate_history()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await _async_update_panel(hass, loading=entry.entry_id)
    return True


async def _async_update_panel(
    hass: HomeAssistant, unloading: str | None = None, loading: str | None = None
) -> None:
    """Show the full-screen map in the sidebar if any loaded entry asks for it.

    `loading` is the entry being set up right now: it is still SETUP_IN_PROGRESS at this point,
    so it must be counted explicitly (checking LOADED alone meant the panel was never added).
    """
    wanted = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != unloading
        and (e.entry_id == loading or e.state is ConfigEntryState.LOADED)
        and e.options.get(CONF_SHOW_PANEL, True)
    ]
    data = hass.data.setdefault(DOMAIN, {})
    current = data.get("panel")
    desired = None
    if wanted:
        first = wanted[0]
        desired = (
            first.options.get(CONF_PANEL_TITLE) or DEFAULT_PANEL_TITLE,
            bool(first.options.get(CONF_PANEL_ADMIN, False)),
        )
    if current == desired:
        return
    if current is not None:
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
        data["panel"] = None
    if desired is None:
        return
    title, admin_only = desired
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        module_url=f"{CARD_URL}?v={VERSION}",
        sidebar_title=title,
        sidebar_icon="mdi:lan",
        require_admin=admin_only,
        config={"title": title},
        embed_iframe=False,
    )
    data["panel"] = desired


async def async_unload_entry(hass: HomeAssistant, entry: CirrusConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
        await _async_update_panel(hass, unloading=entry.entry_id)
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: CirrusConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


@websocket_api.websocket_command(
    {vol.Required("type"): "ovcirrus/topology", vol.Optional("entry_id"): str}
)
@callback
def ws_topology(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Return the network map(s) for the Lovelace card."""
    result = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        if msg.get("entry_id") and entry.entry_id != msg["entry_id"]:
            continue
        coordinator: CirrusCoordinator = entry.runtime_data
        if not coordinator.data:
            continue
        result.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "updated": coordinator.last_update_success,
                "sites": list(coordinator.data.maps.values()),
            }
        )
    connection.send_result(
        msg["id"],
        {
            "entries": result,
            "is_admin": bool(connection.user and connection.user.is_admin),
            "nmap": bool(hass.data.get(DOMAIN, {}).get("nmap")),
            "version": VERSION,
            "view": next((dict(e.runtime_data.store.view) for e in hass.config_entries.async_entries(DOMAIN)
                          if e.state is ConfigEntryState.LOADED and e.runtime_data.store.view), {}),
            "types": {k: v[0] for k, v in TYPES.items()},
        },
    )


def _coordinator(hass: HomeAssistant, entry_id: str) -> CirrusCoordinator | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    if not entry or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        return None
    return entry.runtime_data


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "ovcirrus/set_override",
        vol.Required("entry_id"): str,
        vol.Required("mac"): str,
        vol.Optional("name"): vol.Any(None, str),
        vol.Optional("dev_type"): vol.Any(None, vol.In(list(TYPES))),
    }
)
@callback
def ws_set_override(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Rename a device or change its type from the map; stored against its MAC."""
    coordinator = _coordinator(hass, msg["entry_id"])
    mac = norm_mac(msg["mac"])
    if not coordinator or not mac:
        connection.send_error(msg["id"], "not_found", "Unknown entry or MAC")
        return
    coordinator.store.set_override(mac, (msg.get("name") or "").strip() or None, msg.get("dev_type"))
    coordinator.rebuild()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "ovcirrus/set_photo",
        vol.Required("entry_id"): str,
        vol.Required("key"): str,  # MAC address, or the map id of a device without one
        vol.Optional("data"): vol.Any(None, str),  # base64 PNG / JPEG / WebP; none removes the picture
    }
)
@websocket_api.async_response
async def ws_set_photo(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Your own picture of one client or device, shown on the map and in its details."""
    coordinator = _coordinator(hass, msg["entry_id"])
    key = norm_mac(msg["key"]) or msg["key"].strip()
    if not coordinator or not KEY_RE.match(key):
        connection.send_error(msg["id"], "not_found", "Unknown entry or device")
        return
    folder = Path(hass.config.path(PHOTOS_DIR))
    old = (coordinator.store.photos.get(key) or {}).get("file")
    if msg.get("data"):
        try:
            raw, ext = photo_files.decode(msg["data"])
        except PhotoError as err:
            connection.send_error(msg["id"], "invalid_photo", str(err))
            return
        name = await hass.async_add_executor_job(photo_files.write, folder, key, raw, ext)
        coordinator.store.set_photo(key, name, int(time.time()))
    else:
        await hass.async_add_executor_job(photo_files.remove, folder, old)
        coordinator.store.set_photo(key, None)
    coordinator.rebuild()
    connection.send_result(msg["id"], {"ok": True})


def _apply_positions(coordinator: CirrusCoordinator, site_id: str) -> None:
    if coordinator.data and site_id in coordinator.data.maps:
        coordinator.data.maps[site_id]["positions"] = dict(coordinator.store.positions.get(site_id, {}))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "ovcirrus/set_position",
        vol.Required("entry_id"): str,
        vol.Required("site_id"): str,
        vol.Required("node_id"): str,
        vol.Required("dx"): vol.Coerce(float),
        vol.Required("dy"): vol.Coerce(float),
    }
)
@callback
def ws_set_position(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Save where a device was dragged to on the map (shared by every dashboard)."""
    coordinator = _coordinator(hass, msg["entry_id"])
    if not coordinator:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    clamp = lambda v: max(-20000.0, min(20000.0, v))
    coordinator.store.set_position(msg["site_id"], msg["node_id"], [clamp(msg["dx"]), clamp(msg["dy"])])
    _apply_positions(coordinator, msg["site_id"])
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "ovcirrus/reset_positions", vol.Required("entry_id"): str, vol.Required("site_id"): str}
)
@callback
def ws_reset_positions(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    coordinator = _coordinator(hass, msg["entry_id"])
    if not coordinator:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    coordinator.store.reset_positions(msg["site_id"])
    _apply_positions(coordinator, msg["site_id"])
    connection.send_result(msg["id"], {"ok": True})


def _local_ip(value: str | None) -> str | None:
    """Only private / link-local addresses may be scanned or added for scanning."""
    if not value:
        return None
    try:
        addr = ipaddress.ip_address(value.strip())
    except ValueError:
        raise vol.Invalid("Not an IP address") from None
    if not (addr.is_private or addr.is_link_local) or addr.is_loopback:
        raise vol.Invalid("Only devices on your local network can be added or scanned")
    return str(addr)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "ovcirrus/save_device",
        vol.Required("entry_id"): str,
        vol.Optional("id"): str,
        vol.Required("name"): vol.All(str, vol.Length(min=1, max=80)),
        vol.Optional("ip"): vol.Any(None, str),
        vol.Optional("mac"): vol.Any(None, str),
        vol.Optional("dev_type", default="server"): vol.In(list(TYPES)),
        vol.Optional("switch"): vol.Any(None, str),
        vol.Optional("port"): vol.Any(None, str),
        vol.Optional("scan", default="quick"): vol.In(["none", "quick", "full"]),
        vol.Optional("notes"): vol.Any(None, str),
    }
)
@websocket_api.async_response
async def ws_save_device(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Add (or update) a device on the map, optionally scanning it straight away."""
    coordinator = _coordinator(hass, msg["entry_id"])
    if not coordinator:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    try:
        ip = _local_ip(msg.get("ip"))
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid_ip", str(err))
        return
    mac = norm_mac(msg.get("mac")) if msg.get("mac") else None
    if msg.get("mac") and not mac:
        connection.send_error(msg["id"], "invalid_mac", "Not a valid MAC address")
        return
    if not ip and not mac:
        connection.send_error(msg["id"], "ip_or_mac", "Enter an IP address, a MAC address, or both")
        return
    import uuid

    device = {
        "id": msg.get("id") or uuid.uuid4().hex[:8],
        "name": msg["name"].strip(),
        "ip": ip,
        "mac": mac,
        "type": msg["dev_type"],
        "switch": msg.get("switch") or None,
        "port": (msg.get("port") or "").strip() or None,
        "discover": msg["scan"] != "none" and bool(ip),
        "scan": msg["scan"] if msg["scan"] != "none" else "quick",
        "notes": (msg.get("notes") or "").strip() or None,
    }
    coordinator.store.add_manual(device)
    coordinator.rebuild()
    if device["discover"]:
        hass.async_create_background_task(coordinator.async_discover(ip, device["scan"]), f"ovcirrus scan {ip}")
    connection.send_result(msg["id"], {"device": device})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "ovcirrus/remove_device", vol.Required("entry_id"): str, vol.Required("id"): str}
)
@callback
def ws_remove_device(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    coordinator = _coordinator(hass, msg["entry_id"])
    if not coordinator:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    coordinator.store.remove_manual(msg["id"])
    coordinator.rebuild()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "ovcirrus/save_view",
        vol.Required("entry_id"): str,
        vol.Optional("text"): vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=6, max=40))),
        vol.Optional("clients"): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=0.1, max=3))),
        vol.Optional("devices"): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=0.1, max=3))),
        vol.Optional("font"): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=0.5, max=3))),
        vol.Optional("client"): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=0.5, max=3))),
        vol.Optional("device"): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=0.5, max=4))),
    }
)
@callback
def ws_save_view(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Save text (pt) / client / device sizes as the default for every browser (None clears)."""
    coordinator = _coordinator(hass, msg["entry_id"])
    if not coordinator:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    coordinator.store.view = {k: msg[k] for k in ("text", "clients", "devices") if msg.get(k) is not None}
    coordinator.store.schedule_save()
    connection.send_result(msg["id"], {"view": coordinator.store.view})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "ovcirrus/label_port",
        vol.Required("entry_id"): str,
        vol.Required("ip"): str,
        vol.Required("port"): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        vol.Optional("label"): vol.Any(None, vol.All(str, vol.Length(max=60))),
        vol.Optional("all_devices", default=False): bool,
    }
)
@callback
def ws_label_port(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Name a port the scan could not identify, for this device or for every device."""
    coordinator = _coordinator(hass, msg["entry_id"])
    if not coordinator:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    label = (msg.get("label") or "").strip() or None
    scope_ip = None if msg["all_devices"] else msg["ip"]
    coordinator.store.set_port_label(scope_ip, msg["port"], label)
    if label is None:  # clearing: remove it from both scopes so the scan result shows again
        coordinator.store.set_port_label(None if scope_ip else msg["ip"], msg["port"], None)
    coordinator.rebuild()
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "ovcirrus/discover",
        vol.Required("entry_id"): str,
        vol.Required("ip"): str,
        vol.Optional("mode", default="quick"): vol.In(["quick", "full"]),
    }
)
@websocket_api.async_response
async def ws_discover(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Scan one LAN host for common services (only private / link-local addresses)."""
    coordinator = _coordinator(hass, msg["entry_id"])
    try:
        addr = ipaddress.ip_address(msg["ip"])
    except ValueError:
        connection.send_error(msg["id"], "invalid_ip", "Not an IP address")
        return
    if not (addr.is_private or addr.is_link_local) or addr.is_loopback:
        connection.send_error(msg["id"], "not_local", "Only devices on your local network can be scanned")
        return
    if not coordinator:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    try:
        result = await coordinator.async_discover(str(addr), msg["mode"])
    except Exception as err:  # noqa: BLE001 - tell the card why instead of "Unknown error"
        _LOGGER.exception("Scan of %s failed", addr)
        connection.send_error(msg["id"], "scan_failed", f"{type(err).__name__}: {err}" if str(err) else type(err).__name__)
        return
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "ovcirrus/backup", vol.Required("entry_id"): str})
@websocket_api.async_response
async def ws_backup(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Everything this entry keeps (options, map/device store, your device images), without credentials."""
    coordinator = _coordinator(hass, msg["entry_id"])
    if not coordinator:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    connection.send_result(msg["id"], await async_build_backup(hass, entry, coordinator.store))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "ovcirrus/restore", vol.Required("entry_id"): str, vol.Required("backup"): dict}
)
@websocket_api.async_response
async def ws_restore(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Replace this entry's options, map/device store and images with a backup; the entry then reloads."""
    coordinator = _coordinator(hass, msg["entry_id"])
    if not coordinator:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    try:
        result = await async_restore_backup(hass, entry, coordinator.store, msg["backup"])
    except BackupError as err:
        connection.send_error(msg["id"], "invalid_backup", str(err))
        return
    _LOGGER.info("ovcirrus: restored backup into %s: %s", entry.title, result["summary"])
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ovcirrus/rates",
        vol.Required("entry_id"): str,
        vol.Optional("range", default="2h"): vol.In(list(RANGES)),
    }
)
@callback
def ws_rates(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Traffic history of the UniFi gateway ports (bytes/s) for the bandwidth graphs."""
    coordinator = _coordinator(hass, msg["entry_id"])
    if not coordinator:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    connection.send_result(msg["id"], {
            **coordinator.rates.series(msg["range"]),
            "step": coordinator.rate_interval,
            "fine": coordinator.rates.fine_seconds,
            "long": coordinator.rates.long_seconds,
            "ranges": coordinator.rates.ranges(),
            "enabled": bool(coordinator.unifi),
        })
