"""Presence trackers for wireless clients the user opted in to."""

from __future__ import annotations

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_TRACKED_CLIENTS
from .coordinator import CirrusCoordinator
from .topology import client_name


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: CirrusCoordinator = entry.runtime_data
    async_add_entities(
        CirrusClientTracker(coordinator, mac.upper()) for mac in entry.options.get(CONF_TRACKED_CLIENTS, [])
    )


class CirrusClientTracker(CoordinatorEntity[CirrusCoordinator], ScannerEntity):
    """A wireless client seen by Cirrus."""

    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: CirrusCoordinator, mac: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"client_{mac}"
        self._last: dict = coordinator.data.clients.get(mac) or {}
        self._attr_name = client_name(self._last, {}) if self._last else mac

    @property
    def _client(self) -> dict | None:
        client = self.coordinator.data.clients.get(self._mac)
        if client:
            self._last = client
        return client

    @property
    def source_type(self) -> SourceType:
        return SourceType.ROUTER

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    @property
    def mac_address(self) -> str:
        return self._mac.lower()

    @property
    def ip_address(self) -> str | None:
        return (self._client or {}).get("clientIp")

    @property
    def hostname(self) -> str | None:
        last = self._last or {}
        return last.get("deviceName") or self.coordinator.data.dns_names.get(last.get("clientIp") or "") or None

    @property
    def extra_state_attributes(self):
        c = self._client or {}
        last = self._last or {}
        return {
            "ssid": c.get("ssid"),
            "ap": c.get("apLocation") or c.get("apName"),
            "ap_mac": c.get("apMac"),
            "rssi": c.get("rssi"),
            "band": c.get("band"),
            "channel": c.get("channel"),
            "wifi_mode": c.get("wifiMode"),
            "auth_type": c.get("authType"),
            "username": c.get("auth8021xName") or c.get("portalUsername"),
            "vlan": c.get("vlanId"),
            "os": c.get("osType"),
            "device_type": c.get("deviceType"),
            "session_seconds": c.get("sessionDuration"),
            "rx_bytes": c.get("rxBytes"),
            "tx_bytes": c.get("txBytes"),
            "last_ap": last.get("apLocation") or last.get("apName"),
            "last_ssid": last.get("ssid"),
        }
