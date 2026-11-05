"""Connectivity binary sensors for Cirrus-managed devices."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import CirrusCoordinator, device_status
from .entity import CirrusDeviceEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: CirrusCoordinator = entry.runtime_data
    entities = [CirrusOnline(coordinator, serial) for serial in coordinator.data.devices]
    if coordinator.unifi:
        entities.append(WanOnline(coordinator))
    async_add_entities(entities)


class CirrusOnline(CirrusDeviceEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "online"

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "online")

    @property
    def is_on(self) -> bool | None:
        if not self.device:
            return None
        status = device_status(self.coordinator.data, self.device)
        return {"online": True, "offline": False}.get(status)

    @property
    def extra_state_attributes(self):
        dev = self.device or {}
        return {
            "status": dev.get("deviceStatus"),
            "last_seen": dev.get("lastSeenTime"),
            "link_status": dev.get("linkStatus"),
        }


class WanOnline(CoordinatorEntity[CirrusCoordinator], BinarySensorEntity):
    """Internet reachability as reported by the UniFi gateway."""

    _attr_has_entity_name = True
    _attr_translation_key = "wan"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: CirrusCoordinator) -> None:
        super().__init__(coordinator)
        from .sensor import unifi_device_info

        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_wan_online"
        self._attr_device_info = unifi_device_info(coordinator)

    @property
    def is_on(self) -> bool | None:
        wan = self.coordinator.data.unifi.get("wan") or {}
        state = (wan.get("internet") or wan.get("status") or "").lower()
        return {"ok": True, "error": False}.get(state)
