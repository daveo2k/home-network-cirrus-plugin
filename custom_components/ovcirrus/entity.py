"""Base entities for OmniVista Cirrus."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CirrusCoordinator


class CirrusDeviceEntity(CoordinatorEntity[CirrusCoordinator]):
    """Entity attached to a Cirrus-managed AP or switch."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CirrusCoordinator, serial: str, key: str) -> None:
        super().__init__(coordinator)
        self.serial = serial
        self._attr_unique_id = f"{serial}_{key}"
        dev = coordinator.data.devices[serial]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            connections={(dr.CONNECTION_NETWORK_MAC, dev["macAddress"].lower())} if dev.get("macAddress") else set(),
            name=dev.get("name") or serial,
            manufacturer="Alcatel-Lucent Enterprise",
            model=dev.get("modelName"),
            serial_number=serial,
            sw_version=dev.get("currentSwVer"),
            suggested_area=(dev.get("floor") or {}).get("name"),
            configuration_url=f"https://{coordinator.client._host}/",
        )

    @property
    def device(self) -> dict | None:
        return self.coordinator.data.devices.get(self.serial)

    @property
    def available(self) -> bool:
        return super().available and self.device is not None


class CirrusSiteEntity(CoordinatorEntity[CirrusCoordinator]):
    """Entity attached to a Cirrus site."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CirrusCoordinator, site_id: str, key: str) -> None:
        super().__init__(coordinator)
        self.site_id = site_id
        self._attr_unique_id = f"site_{site_id}_{key}"
        site = coordinator.data.sites.get(site_id, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"site_{site_id}")},
            name=f"Cirrus {site.get('name', site_id)}",
            manufacturer="Alcatel-Lucent Enterprise",
            model="OmniVista Cirrus site",
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def site_map(self) -> dict:
        return self.coordinator.data.maps.get(self.site_id) or {}
