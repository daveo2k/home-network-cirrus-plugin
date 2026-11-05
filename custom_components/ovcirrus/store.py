"""Persistent per-entry state: devices seen before (new-device detection), user overrides
(name / type per MAC) and service-discovery results."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .identify import is_generic_hostname, is_private_mac

STORAGE_VERSION = 1
NEW_WINDOW = 24 * 3600
SAVE_DELAY = 30


class EntryStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"ovcirrus.{entry_id}")
        self.known: dict[str, dict] = {}
        self.overrides: dict[str, dict] = {}
        self.services: dict[str, dict] = {}
        self.positions: dict[str, dict[str, list[float]]] = {}  # site_id -> node_id -> [dx, dy]
        self.baseline_done = False

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.known = data.get("known", {})
        self.overrides = data.get("overrides", {})
        self.services = data.get("services", {})
        self.positions = data.get("positions", {})
        self.baseline_done = data.get("baseline_done", False)

    def _data(self) -> dict:
        return {
            "known": self.known,
            "overrides": self.overrides,
            "services": self.services,
            "positions": self.positions,
            "baseline_done": self.baseline_done,
        }

    def schedule_save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    async def async_save(self) -> None:
        await self._store.async_save(self._data())

    def observe(self, seen: dict[str, dict]) -> list[dict]:
        """Record currently-connected devices; return the ones never seen before.

        seen: mac -> {"name", "hostname", "vendor", "where", "ip", ...}
        The first run only records a baseline, so installing the integration does not
        announce every existing device as new.
        """
        now = time.time()
        new: list[dict] = []
        private_by_host = {
            (v.get("hostname") or "").lower(): mac
            for mac, v in self.known.items()
            if is_private_mac(mac) and v.get("hostname") and not is_generic_hostname(v.get("hostname"))
        }
        for mac, info in seen.items():
            entry = self.known.get(mac)
            if entry:
                entry["last"] = now
                for key in ("name", "hostname", "vendor", "ip", "where"):
                    if info.get(key):
                        entry[key] = info[key]
                continue
            host = (info.get("hostname") or "").lower()
            rotated_from = private_by_host.get(host) if is_private_mac(mac) and host else None
            self.known[mac] = {
                "first": now if self.baseline_done else 0,
                "last": now,
                **{k: info.get(k) for k in ("name", "hostname", "vendor", "ip", "where")},
            }
            if rotated_from:
                # Same distinctive hostname on a new private MAC: the device rotated its MAC.
                self.known[mac]["first"] = self.known.get(rotated_from, {}).get("first", 0)
                self.known[mac]["previous_mac"] = rotated_from
                continue
            if self.baseline_done:
                new.append({"mac": mac, **info})
        self.baseline_done = True
        self.schedule_save()
        return new

    def is_new(self, mac: str) -> bool:
        first = (self.known.get(mac) or {}).get("first") or 0
        return bool(first) and time.time() - first < NEW_WINDOW

    def recent_new(self) -> list[dict]:
        now = time.time()
        return sorted(
            ({"mac": m, **v} for m, v in self.known.items() if v.get("first") and now - v["first"] < NEW_WINDOW),
            key=lambda v: -v["first"],
        )

    def set_override(self, mac: str, name: str | None, dev_type: str | None) -> None:
        entry = {k: v for k, v in (("name", name), ("type", dev_type)) if v}
        if entry:
            self.overrides[mac] = entry
        else:
            self.overrides.pop(mac, None)
        self.schedule_save()

    def set_position(self, site_id: str, node_id: str, offset: list[float] | None) -> None:
        """Offset of a node (and the devices under it) from its automatic position."""
        site = self.positions.setdefault(site_id, {})
        if offset is None or (abs(offset[0]) < 0.5 and abs(offset[1]) < 0.5):
            site.pop(node_id, None)
        else:
            site[node_id] = [round(offset[0], 1), round(offset[1], 1)]
        self.schedule_save()

    def reset_positions(self, site_id: str) -> None:
        self.positions.pop(site_id, None)
        self.schedule_save()

    def set_services(self, ip: str, result: dict) -> None:
        self.services[ip] = result
        self.schedule_save()
