"""Backup and restore of one OmniVista Cirrus entry: its options, the map/device store and your device images.

Credentials (Cirrus app ID / secret, UniFi password) are never written to a backup. On restore the values
already set up in this Home Assistant are kept, so a backup can be restored onto a fresh install once the
integration has been added again with its credentials.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_HOST,
    CONF_ORG_ID,
    CONF_ORG_NAME,
    CONF_SITE_IDS,
    CONF_UNIFI_PASSWORD,
    PHOTOS_DIR,
    USER_IMAGES_DIR,
    VERSION,
)

FORMAT = "ovcirrus-backup"
FORMAT_VERSION = 1
SECRET_DATA = {CONF_APP_ID, CONF_APP_SECRET}
SECRET_OPTIONS = {CONF_UNIFI_PASSWORD}
STORE_KEYS = ("known", "overrides", "services", "positions", "manual", "port_labels", "view", "photos", "baseline_done")
IMAGE_EXT = {".png", ".webp", ".jpg", ".jpeg", ".svg"}
# Backups travel over the HA websocket, which caps a message at a few MB, so images are kept to this total.
IMAGE_BUDGET = 2 * 1024 * 1024


class BackupError(Exception):
    """The file is not a backup this version can restore."""


def _read_images(folder: Path, used: int = 0) -> tuple[dict[str, str], list[str]]:
    images: dict[str, str] = {}
    skipped: list[str] = []
    if not folder.is_dir():
        return images, skipped
    for file in sorted(folder.iterdir()):
        if not file.is_file() or file.suffix.lower() not in IMAGE_EXT:
            continue
        raw = file.read_bytes()
        if used + len(raw) > IMAGE_BUDGET:
            skipped.append(file.name)
            continue
        used += len(raw)
        images[file.name] = base64.b64encode(raw).decode()
    return images, skipped


def _write_images(folder: Path, images: dict[str, str], exts: set[str] = IMAGE_EXT) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, data in images.items():
        safe = Path(name).name  # never write outside the images folder
        if not safe or Path(safe).suffix.lower() not in exts:
            continue
        (folder / safe).write_bytes(base64.b64decode(data))
        written += 1
    return written


def summarize(store: dict, images: dict | None = None) -> dict[str, int]:
    return {
        "manual_devices": len(store.get("manual") or []),
        "named_devices": len(store.get("overrides") or {}),
        "moved_devices": sum(len(v) for v in (store.get("positions") or {}).values()),
        "port_names": sum(len(v) for v in (store.get("port_labels") or {}).values()),
        "known_devices": len(store.get("known") or {}),
        "service_scans": len(store.get("services") or {}),
        "images": len(images or {}),
        "photos": len(store.get("photos") or {}),
    }


async def async_build_backup(hass: HomeAssistant, entry, store) -> dict[str, Any]:
    images, skipped = await hass.async_add_executor_job(_read_images, Path(hass.config.path(USER_IMAGES_DIR)))
    used = sum(len(v) * 3 // 4 for v in images.values())
    photos, photos_skipped = await hass.async_add_executor_job(_read_images, Path(hass.config.path(PHOTOS_DIR)), used)
    data = store._data()
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "integration_version": VERSION,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entry": {
            "title": entry.title,
            "data": {k: v for k, v in entry.data.items() if k not in SECRET_DATA},
            "options": {k: v for k, v in entry.options.items() if k not in SECRET_OPTIONS},
        },
        "store": {k: data.get(k) for k in STORE_KEYS},
        "images": images,
        "images_skipped": skipped + photos_skipped,
        "photos": photos,
        "summary": summarize(data, images),
    }


async def async_restore_backup(hass: HomeAssistant, entry, store, backup: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(backup, dict) or backup.get("format") != FORMAT:
        raise BackupError("This file is not an OmniVista Cirrus backup.")
    if int(backup.get("format_version") or 0) > FORMAT_VERSION:
        raise BackupError("This backup was made by a newer version of the integration. Update it first.")
    saved = backup.get("store") or {}
    src = backup.get("entry") or {}
    notes: list[str] = []

    # Map and device state: replace what is here with the backup.
    store.known = saved.get("known") or {}
    store.overrides = saved.get("overrides") or {}
    store.services = saved.get("services") or {}
    store.positions = saved.get("positions") or {}
    store.manual = saved.get("manual") or []
    store.port_labels = saved.get("port_labels") or {}
    store.view = saved.get("view") or {}
    store.photos = saved.get("photos") or {}
    store.baseline_done = bool(saved.get("baseline_done", True))
    await store.async_save()

    images = backup.get("images") or {}
    written = await hass.async_add_executor_job(_write_images, Path(hass.config.path(USER_IMAGES_DIR)), images) if images else 0
    photos = backup.get("photos") or {}
    if photos:
        written += await hass.async_add_executor_job(_write_images, Path(hass.config.path(PHOTOS_DIR)), photos, {".png", ".jpg", ".webp"})

    # Options: take the backup's, but keep this install's secrets.
    options = {k: v for k, v in (src.get("options") or {}).items() if k not in SECRET_OPTIONS}
    for k in SECRET_OPTIONS:
        if k in entry.options:
            options[k] = entry.options[k]

    # Entry data: keep the host and credentials; the site choice only applies to the same organisation.
    data = dict(entry.data)
    src_data = src.get("data") or {}
    if src_data.get(CONF_ORG_ID) and src_data.get(CONF_ORG_ID) == entry.data.get(CONF_ORG_ID):
        if CONF_SITE_IDS in src_data:
            data[CONF_SITE_IDS] = src_data[CONF_SITE_IDS]
    elif src_data.get(CONF_ORG_ID):
        notes.append(
            f"The backup is from organisation “{src_data.get(CONF_ORG_NAME) or src_data.get(CONF_ORG_ID)}”, "
            "so the site selection here was kept."
        )
    if src_data.get(CONF_HOST) and src_data.get(CONF_HOST) != entry.data.get(CONF_HOST):
        notes.append(f"The backup used Cirrus host {src_data.get(CONF_HOST)}; this install keeps {entry.data.get(CONF_HOST)}.")

    changed = options != dict(entry.options) or data != dict(entry.data)
    if changed:
        hass.config_entries.async_update_entry(entry, data=data, options=options)  # the update listener reloads
    else:
        hass.config_entries.async_schedule_reload(entry.entry_id)

    return {"ok": True, "summary": summarize(saved, images), "images_written": written, "notes": notes}
