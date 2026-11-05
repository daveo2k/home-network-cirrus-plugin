"""Download-and-cache for the IEEE MAC vendor registry, brand logos and NetBox device data.

Everything is fetched the first time it is needed, saved under /config/ovcirrus_cache and
served to the card from /ovcirrus_cache, so the dashboard never loads third-party URLs and
keeps working offline once an asset has been seen.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

import aiohttp

_LOGGER = logging.getLogger(__name__)

OUI_URL = "https://standards-oui.ieee.org/oui/oui.csv"
OUI_MAX_AGE = 30 * 86400
NEGATIVE_RETRY = 7 * 86400
LISTING_MAX_AGE = 30 * 86400
MAX_IMAGE_BYTES = 2_000_000
USER_AGENT = "HomeAssistant-ovcirrus/0.2 (+https://www.home-assistant.io)"
SIMPLE_ICONS_VERSION = "16.30.0"
NETBOX_RAW = "https://raw.githubusercontent.com/netbox-community/devicetype-library/master"
NETBOX_API = "https://api.github.com/repos/netbox-community/devicetype-library/contents"
TIMEOUT = aiohttp.ClientTimeout(total=60)

# UniFi model codes -> NetBox slug (without the "ubiquiti-" prefix).
UNIFI_MODELS = {
    "UCGULTRA": "unifi-cloud-gateway-ultra",
    "UCG-ULTRA": "unifi-cloud-gateway-ultra",
    "UCGMAX": "unifi-cloud-gateway-max",
    "UCG-MAX": "unifi-cloud-gateway-max",
    "UCGF": "unifi-cloud-gateway-fiber",
    "UDMPRO": "unifi-dream-machine-pro",
    "UDMPROSE": "unifi-dream-machine-special-edition",
    "UDMPROMAX": "unifi-dream-machine-pro-max",
    "UDR": "unifi-dream-router",
    "UXGPRO": "unifi-gateway-pro",
    "UXG": "unifi-gateway-lite",
}


def _slug(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def best_netbox_file(names: list[str], vendor_slug: str, model: str) -> str | None:
    """Pick the front elevation image that best matches a model."""
    fronts = [n for n in names if re.search(r"\.front\.(png|jpe?g|svg)$", n)]
    slug = _slug(UNIFI_MODELS.get((model or "").upper().replace(" ", ""), model))
    for name in fronts:
        if name.startswith(f"{vendor_slug}-{slug}.front."):
            return name
    numbers = re.findall(r"\d+", slug)
    words = [w for w in re.findall(r"[a-z]+", slug) if len(w) > 2]
    best, best_score = None, 0
    for name in fronts:
        nums = re.findall(r"\d+", name)
        if numbers and numbers[0] not in nums:
            continue  # model family must match (e.g. 6860)
        if not numbers and not all(w in name for w in words):
            continue  # word-only models (UniFi) must match every word, never a sibling model
        score = len(set(numbers) & set(nums)) * 2 + sum(1 for w in words if w in name)
        if score > best_score:
            best, best_score = name, score
    return best


def parse_netbox_ports(text: str) -> dict | None:
    """Count front-panel port types from a NetBox device-type YAML."""
    import yaml  # bundled with Home Assistant

    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return None
    copper = sfp = qsfp = 0
    for iface in data.get("interfaces") or []:
        name, typ = str(iface.get("name", "")), str(iface.get("type", ""))
        if iface.get("mgmt_only") or name.lower() in ("emp", "mgmt", "management"):
            continue
        if "qsfp" in typ:
            qsfp += 1
        elif "sfp" in typ or "x-" in typ:
            sfp += 1
        elif "base-t" in typ:
            copper += 1
    if not (copper or sfp or qsfp):
        return None
    return {"copper": copper, "sfp": sfp, "stack": qsfp, "part_number": data.get("part_number")}


class AssetCache:
    """Lazy, persistent cache. Lookups never block: a miss schedules a download."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        folder: Path,
        url_prefix: str,
        run_in_executor: Callable,
        create_task: Callable,
    ) -> None:
        self._session = session
        self.folder = folder
        self.url_prefix = url_prefix
        self._executor = run_in_executor
        self._create_task = create_task
        self._index: dict = {"files": {}, "listings": {}, "ports": {}}
        self.oui: dict[str, str] = {}
        self._oui_ts = 0.0
        self._pending: set[str] = set()
        self.on_change: Callable[[], None] | None = None

    # ---- persistence ---------------------------------------------------------
    def _load_sync(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        idx = self.folder / "index.json"
        if idx.exists():
            try:
                self._index.update(json.loads(idx.read_text()))
            except ValueError:
                pass
        oui = self.folder / "oui.json"
        if oui.exists():
            try:
                payload = json.loads(oui.read_text())
                self.oui, self._oui_ts = payload["vendors"], payload["ts"]
            except (ValueError, KeyError):
                pass

    async def async_load(self) -> None:
        await self._executor(self._load_sync)

    def _save_index_sync(self) -> None:
        (self.folder / "index.json").write_text(json.dumps(self._index))

    async def _save_index(self) -> None:
        await self._executor(self._save_index_sync)

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    async def _get(self, url: str, binary: bool = True):
        async with self._session.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as resp:
            if resp.status != 200:
                return None, resp.status, None
            body = await resp.read()
            return body, 200, resp.headers.get("Content-Type", "")

    def _schedule(self, key: str, coro) -> None:
        if key in self._pending:
            coro.close()
            return
        self._pending.add(key)

        async def runner():
            try:
                await coro
            except Exception as err:  # noqa: BLE001 - background fetch
                _LOGGER.debug("Asset fetch %s failed: %s", key, err)
            finally:
                self._pending.discard(key)

        self._create_task(runner(), f"ovcirrus asset {key}")

    # ---- MAC vendor registry ------------------------------------------------------
    def vendor(self, mac: str | None) -> str | None:
        if not mac or not self.oui:
            if not self.oui:
                self.ensure_oui()
            return None
        return self.oui.get(mac.replace(":", "").replace("-", "").upper()[:6])

    def ensure_oui(self) -> None:
        if self.oui and time.time() - self._oui_ts < OUI_MAX_AGE:
            return
        self._schedule("oui", self._fetch_oui())

    async def _fetch_oui(self) -> None:
        body, status, _ = await self._get(OUI_URL)
        if not body:
            _LOGGER.warning("Could not download the IEEE MAC vendor registry (HTTP %s)", status)
            return

        def parse_and_save() -> dict:
            vendors = {}
            for row in csv.reader(io.StringIO(body.decode("utf-8", "replace"))):
                if len(row) > 2 and len(row[1]) == 6:
                    vendors[row[1].upper()] = row[2].strip()
            (self.folder / "oui.json").write_text(json.dumps({"ts": time.time(), "vendors": vendors}))
            return vendors

        self.oui = await self._executor(parse_and_save)
        self._oui_ts = time.time()
        _LOGGER.info("Loaded %d MAC vendor prefixes", len(self.oui))
        self._changed()

    # ---- files (logos, device images) ---------------------------------------------
    def _file_url(self, key: str) -> str | None:
        """Cached URL, None if missing (and schedule download), '' if known-missing."""
        entry = self._index["files"].get(key)
        if entry and entry.get("file"):
            return f"{self.url_prefix}/{entry['file']}"
        if entry and time.time() - entry.get("ts", 0) < NEGATIVE_RETRY:
            return ""
        return None

    async def _fetch_file(self, key: str, urls: list[str]) -> None:
        for url in urls:
            body, _, ctype = await self._get(url)
            if not body or len(body) > MAX_IMAGE_BYTES or not (ctype or "").startswith("image/"):
                continue
            ext = {"image/svg+xml": "svg", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(
                ctype.split(";")[0], "png"
            )
            name = f"{re.sub(r'[^a-z0-9]+', '_', key.lower())}.{ext}"
            await self._executor((self.folder / name).write_bytes, body)
            self._index["files"][key] = {"file": name, "ts": time.time()}
            await self._save_index()
            self._changed()
            return
        self._index["files"][key] = {"file": None, "ts": time.time()}
        await self._save_index()

    def logo(self, sources: list[str]) -> str | None:
        """First available logo among 'ha:<domain>' / 'si:<slug>' sources."""
        for src in sources:
            kind, _, name = src.partition(":")
            key = f"logo-{kind}-{name}"
            url = self._file_url(key)
            if url:
                return url
            if url is None:
                if kind == "ha":
                    urls = [f"https://brands.home-assistant.io/{name}/icon.png"]
                else:
                    urls = [
                        f"https://cdn.simpleicons.org/{name}",
                        f"https://cdn.jsdelivr.net/npm/simple-icons@{SIMPLE_ICONS_VERSION}/icons/{name}.svg",
                    ]
                self._schedule(key, self._fetch_file(key, urls))
                return None  # try again next refresh rather than falling through
        return None

    # ---- NetBox device-type library ---------------------------------------------------
    def netbox_image(self, vendor: str, model: str | None) -> str | None:
        if not model:
            return None
        key = f"netbox-{_slug(vendor)}-{_slug(model)}"
        url = self._file_url(key)
        if url is not None:
            return url or None
        self._schedule(key, self._fetch_netbox_image(key, vendor, model))
        return None

    async def _listing(self, vendor: str) -> list[str]:
        cached = self._index["listings"].get(vendor)
        if cached and time.time() - cached["ts"] < LISTING_MAX_AGE:
            return cached["names"]
        body, status, _ = await self._get(f"{NETBOX_API}/elevation-images/{vendor}")
        names = [item["name"] for item in json.loads(body)] if body else []
        if body or status == 404:
            self._index["listings"][vendor] = {"names": names, "ts": time.time()}
        return names

    async def _fetch_netbox_image(self, key: str, vendor: str, model: str) -> None:
        names = await self._listing(vendor)
        match = best_netbox_file(names, _slug(vendor), model)
        if not match:
            self._index["files"][key] = {"file": None, "ts": time.time()}
            await self._save_index()
            return
        await self._fetch_file(key, [f"{NETBOX_RAW}/elevation-images/{vendor}/{match}"])

    def netbox_ports(self, vendor: str, model: str | None) -> dict | None:
        if not model:
            return None
        key = f"{vendor}/{model}"
        if key in self._index["ports"]:
            entry = self._index["ports"][key]
            if entry.get("ports") or time.time() - entry.get("ts", 0) < NEGATIVE_RETRY:
                return entry.get("ports")
        self._schedule(f"ports-{key}", self._fetch_ports(key, vendor, model))
        return None

    async def _fetch_ports(self, key: str, vendor: str, model: str) -> None:
        body, _, _ = await self._get(f"{NETBOX_RAW}/device-types/{vendor}/{model}.yaml")
        ports = await self._executor(parse_netbox_ports, body.decode("utf-8", "replace")) if body else None
        self._index["ports"][key] = {"ports": ports, "ts": time.time()}
        await self._save_index()
        if ports:
            self._changed()
