"""Sensors for OmniVista Cirrus."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfFrequency,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import CirrusCoordinator, CirrusData
from .entity import CirrusDeviceEntity, CirrusSiteEntity
from .mac_table import norm_mac


def _ap_clients(data: CirrusData, dev: dict) -> list[dict]:
    mac = norm_mac(dev.get("macAddress"))
    return [c for c in data.clients.values() if norm_mac(c.get("apMac")) == mac]


@dataclass(frozen=True, kw_only=True)
class DeviceSensorDescription(SensorEntityDescription):
    value_fn: Callable[[CirrusData, dict], Any]
    families: tuple[str, ...] = ("AP", "AOS")
    attrs_fn: Callable[[CirrusData, dict], dict] | None = None


DEVICE_SENSORS: tuple[DeviceSensorDescription, ...] = (
    DeviceSensorDescription(
        key="clients",
        translation_key="clients",
        state_class=SensorStateClass.MEASUREMENT,
        families=("AP",),
        value_fn=lambda data, dev: len(_ap_clients(data, dev)),
        attrs_fn=lambda data, dev: {
            "by_ssid": dict(Counter(c.get("ssid") for c in _ap_clients(data, dev))),
            "by_band": dict(Counter(c.get("band") for c in _ap_clients(data, dev))),
        },
    ),
    DeviceSensorDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data, dev: dev.get("upTime"),
    ),
    DeviceSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data, dev: dev.get("currentSwVer"),
    ),
    DeviceSensorDescription(
        key="ip_address",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data, dev: dev.get("ipAddress"),
    ),
    DeviceSensorDescription(
        # Summary of the live channel on every radio, e.g. "1 / 36".
        key="channel",
        translation_key="channel",
        families=("AP",),
        icon="mdi:wifi-settings",
        value_fn=lambda data, dev: " / ".join(
            str(r["channel"]) for r in data.radios.get(dev.get("serialNumber"), []) if r.get("channel") is not None
        ) or None,
        attrs_fn=lambda data, dev: {
            "radios": [
                {k: r.get(k) for k in ("radio", "band", "channel", "bandwidth", "txPower", "channelUtilization", "noiseFloor")}
                for r in data.radios.get(dev.get("serialNumber"), [])
            ],
            "rf_profile": data.rf_profiles.get(dev.get("serialNumber")),
        },
    ),
)


@dataclass(frozen=True, kw_only=True)
class RadioSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict, list[dict]], Any]  # (radio, clients on this AP) -> value


RADIO_SENSORS: tuple[RadioSensorDescription, ...] = (
    RadioSensorDescription(key="channel", icon="mdi:wifi", value_fn=lambda r, c: r.get("channel")),
    RadioSensorDescription(
        key="clients", icon="mdi:account-multiple", state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda r, c: sum(1 for x in c if (x.get("band") or "").lower() == (r.get("band") or "").lower()),
    ),
    RadioSensorDescription(
        key="utilization", icon="mdi:chart-donut", native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT, value_fn=lambda r, c: r.get("channelUtilization"),
    ),
    RadioSensorDescription(
        key="tx_power", device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT, state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC, value_fn=lambda r, c: r.get("txPower"),
    ),
    RadioSensorDescription(
        key="noise_floor", device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT, state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC, value_fn=lambda r, c: r.get("noiseFloor"),
    ),
    RadioSensorDescription(
        key="bandwidth", icon="mdi:arrow-expand-horizontal", device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ, entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r, c: r.get("bandwidth"),
    ),
)
RADIO_NAMES = {
    "channel": "channel", "clients": "clients", "utilization": "channel utilisation",
    "tx_power": "TX power", "noise_floor": "noise floor", "bandwidth": "channel width",
}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: CirrusCoordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for serial, dev in coordinator.data.devices.items():
        family = dev.get("deviceFamily")
        entities.extend(
            CirrusDeviceSensor(coordinator, serial, desc) for desc in DEVICE_SENSORS if family in desc.families
        )
    for site_id in coordinator.data.sites:
        entities.append(SiteClientsSensor(coordinator, site_id, "wireless"))
        entities.append(SiteClientsSensor(coordinator, site_id, "wired"))
    entities.append(NewDevicesSensor(coordinator))
    if coordinator.unifi:
        entities.extend(WanSensor(coordinator, desc) for desc in WAN_SENSORS)
    async_add_entities(entities)

    known_ssids: set[tuple[str, str]] = set()

    @callback
    def _add_ssids() -> None:
        new = []
        for site_id, site_map in coordinator.data.maps.items():
            for ssid in site_map["summary"]["ssids"]:
                if (site_id, ssid) not in known_ssids:
                    known_ssids.add((site_id, ssid))
                    new.append(SsidClientsSensor(coordinator, site_id, ssid))
        if new:
            async_add_entities(new)

    _add_ssids()
    entry.async_on_unload(coordinator.async_add_listener(_add_ssids))

    known_radios: set[tuple[str, str]] = set()

    @callback
    def _add_radios() -> None:
        # One set of sensors per active radio; radios that appear later are added automatically.
        new = []
        for serial, radios in coordinator.data.radios.items():
            if serial not in coordinator.data.devices:
                continue
            bands = [r.get("band") for r in radios]
            for radio in radios:
                rid = radio.get("radio") or radio.get("band")
                if not rid or (serial, rid) in known_radios:
                    continue
                known_radios.add((serial, rid))
                label = radio.get("band") or rid
                if bands.count(radio.get("band")) > 1:
                    label = f"{label} ({rid})"  # e.g. two 5 GHz radios on a tri-radio AP
                new.extend(CirrusRadioSensor(coordinator, serial, rid, label, d) for d in RADIO_SENSORS)
        if new:
            async_add_entities(new)

    _add_radios()
    entry.async_on_unload(coordinator.async_add_listener(_add_radios))


class CirrusDeviceSensor(CirrusDeviceEntity, SensorEntity):
    entity_description: DeviceSensorDescription

    def __init__(self, coordinator, serial, description: DeviceSensorDescription) -> None:
        super().__init__(coordinator, serial, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data, self.device) if self.device else None

    @property
    def extra_state_attributes(self):
        if self.entity_description.attrs_fn and self.device:
            return self.entity_description.attrs_fn(self.coordinator.data, self.device)
        return None


class SiteClientsSensor(CirrusSiteEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, site_id: str, kind: str) -> None:
        super().__init__(coordinator, site_id, f"{kind}_clients")
        self.kind = kind
        self._attr_translation_key = f"{kind}_clients"

    @property
    def available(self) -> bool:
        if self.kind == "wired" and not self.site_map.get("mac_table"):
            return False
        return super().available and bool(self.site_map)

    @property
    def native_value(self):
        return self.site_map.get("summary", {}).get(f"{self.kind}_clients")

    @property
    def extra_state_attributes(self):
        if self.kind == "wireless":
            return {"by_ssid": self.site_map.get("summary", {}).get("ssids", {})}
        return None


class SsidClientsSensor(CirrusSiteEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wifi"

    def __init__(self, coordinator, site_id: str, ssid: str) -> None:
        super().__init__(coordinator, site_id, f"ssid_{ssid}")
        self.ssid = ssid
        self._attr_name = f"{ssid} clients"

    @property
    def native_value(self):
        return self.site_map.get("summary", {}).get("ssids", {}).get(self.ssid, 0)


# ---------------------------------------------------------------- new devices / WAN
from datetime import datetime, timezone  # noqa: E402

from homeassistant.const import UnitOfDataRate  # noqa: E402
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo  # noqa: E402
from homeassistant.helpers.update_coordinator import CoordinatorEntity  # noqa: E402

from .const import DOMAIN  # noqa: E402


class NewDevicesSensor(CoordinatorEntity[CirrusCoordinator], SensorEntity):
    """Devices first seen in the last 24 hours (attributes list them)."""

    _attr_has_entity_name = True
    _attr_translation_key = "new_devices"
    _attr_icon = "mdi:new-box"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: CirrusCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_new_devices"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"entry_{entry.entry_id}")},
            name=f"Cirrus {entry.title}",
            manufacturer="Alcatel-Lucent Enterprise",
            model="OmniVista Cirrus organization",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> int:
        return len(self.coordinator.store.recent_new())

    @property
    def extra_state_attributes(self):
        return {
            "devices": [
                {
                    "mac": d["mac"],
                    "name": d.get("name"),
                    "vendor": d.get("vendor"),
                    "ip": d.get("ip"),
                    "where": d.get("where"),
                    "first_seen": datetime.fromtimestamp(d["first"], timezone.utc).isoformat(),
                }
                for d in self.coordinator.store.recent_new()[:50]
            ]
        }


@dataclass(frozen=True, kw_only=True)
class WanSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], Any]


WAN_SENSORS: tuple[WanSensorDescription, ...] = (
    WanSensorDescription(
        key="wan_download", translation_key="wan_download", device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND, state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0, value_fn=lambda w: w.get("download_mbps"),
    ),
    WanSensorDescription(
        key="wan_upload", translation_key="wan_upload", device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND, state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0, value_fn=lambda w: w.get("upload_mbps"),
    ),
    WanSensorDescription(
        key="wan_latency", translation_key="wan_latency", device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS, state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda w: w.get("latency_ms"),
    ),
    WanSensorDescription(
        key="speedtest_time", translation_key="speedtest_time", device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda w: datetime.fromtimestamp(w["speedtest_lastrun"], timezone.utc) if w.get("speedtest_lastrun") else None,
    ),
    WanSensorDescription(
        key="wan_ip", translation_key="wan_ip", entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda w: w.get("wan_ip"),
    ),
    WanSensorDescription(key="isp", translation_key="isp", value_fn=lambda w: w.get("isp")),
)


def unifi_device_info(coordinator: CirrusCoordinator) -> DeviceInfo:
    wan = (coordinator.data.unifi.get("wan") or {}) if coordinator.data else {}
    return DeviceInfo(
        identifiers={(DOMAIN, f"unifi_{coordinator.config_entry.entry_id}")},
        name=wan.get("gateway_name") or "UniFi gateway",
        manufacturer="Ubiquiti",
        model=wan.get("gateway_model"),
        sw_version=wan.get("gateway_version"),
    )


class WanSensor(CoordinatorEntity[CirrusCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: WanSensorDescription

    def __init__(self, coordinator: CirrusCoordinator, description: WanSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"
        self._attr_device_info = unifi_device_info(coordinator)

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.data.unifi.get("wan"))

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data.unifi.get("wan") or {})


class CirrusRadioSensor(CirrusDeviceEntity, SensorEntity):
    """A value for one radio of an AP (e.g. "2.4GHz channel", "5GHz channel utilisation")."""

    entity_description: RadioSensorDescription

    def __init__(self, coordinator, serial: str, radio_id: str, label: str, description: RadioSensorDescription) -> None:
        super().__init__(coordinator, serial, f"radio_{radio_id}_{description.key}")
        self.entity_description = description
        self.radio_id = radio_id
        self._attr_name = f"{label} {RADIO_NAMES[description.key]}"

    @property
    def _radio(self) -> dict | None:
        return next(
            (r for r in self.coordinator.data.radios.get(self.serial, []) if (r.get("radio") or r.get("band")) == self.radio_id),
            None,
        )

    @property
    def available(self) -> bool:
        return super().available and self._radio is not None

    @property
    def native_value(self):
        radio = self._radio
        if radio is None or not self.device:
            return None
        return self.entity_description.value_fn(radio, _ap_clients(self.coordinator.data, self.device))

    @property
    def extra_state_attributes(self):
        radio = self._radio or {}
        if self.entity_description.key != "channel":
            return None
        return {
            "radio": radio.get("radio"),
            "band": radio.get("band"),
            "bandwidth_mhz": radio.get("bandwidth"),
            "tx_power_dbm": radio.get("txPower"),
            "channel_utilisation": radio.get("channelUtilization"),
            "noise_floor_dbm": radio.get("noiseFloor"),
            "updated": radio.get("timestamp"),
        }
