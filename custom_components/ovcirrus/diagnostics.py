"""Diagnostics for OmniVista Cirrus."""

from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_APP_ID, CONF_APP_SECRET, CONF_UNIFI_PASSWORD, CONF_UNIFI_USERNAME

TO_REDACT = {
    CONF_APP_ID, CONF_APP_SECRET, CONF_UNIFI_PASSWORD, CONF_UNIFI_USERNAME, "wan_ip", "auth8021xName", "portalUsername", "devicePrivateKey",
    "manageRapVpnServerPrivateKey", "bridgeApWebPassword", "bridgeSshPassword", "meshPassphrase",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry) -> dict:
    coordinator = entry.runtime_data
    data = coordinator.data
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "sites": data.sites,
            "devices": data.devices,
            "client_count": len(data.clients),
            "topology": data.topology,
            "lldp": data.lldp,
            "mac_table_sizes": {s: len(t.macs) for s, t in data.switch_tables.items()},
            "ssh_errors": data.ssh_errors,
            "map_summaries": {sid: m.get("summary") for sid, m in data.maps.items()},
            "unifi_wan": data.unifi.get("wan"),
            "unifi_error": data.unifi_error,
            "known_devices": len(coordinator.store.known),
            "overrides": len(coordinator.store.overrides),
            "service_scans": list(coordinator.store.services),
        },
        TO_REDACT,
    )
