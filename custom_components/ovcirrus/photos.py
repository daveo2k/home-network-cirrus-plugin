"""Your own pictures of individual clients and devices (e.g. a photo of the actual Shelly relay).

The card shrinks a picture before sending it; here it is checked (PNG, JPEG or WebP only, by its
first bytes, never SVG) and written to /config/ovcirrus_photos, which the integration serves at
/ovcirrus_photos. Each picture belongs to one MAC address or manual device id.
"""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path

from .const import PHOTO_MAX_BYTES

KEY_RE = re.compile(r"^[A-Za-z0-9:_.-]{1,64}$")
EXT = (".png", ".jpg", ".webp")


class PhotoError(Exception):
    """The upload is not a picture we accept."""


def _kind(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if raw.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return None


def file_stem(key: str) -> str:
    return "photo-" + re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")


def decode(data: str) -> tuple[bytes, str]:
    """Base64 (optionally a data: URL) -> (bytes, extension)."""
    if "," in data[:100] and data.startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise PhotoError("The picture could not be read.") from None
    if len(raw) > PHOTO_MAX_BYTES:
        raise PhotoError("The picture is too large (1 MB at most).")
    ext = _kind(raw)
    if not ext:
        raise PhotoError("Use a PNG, JPEG or WebP picture.")
    return raw, ext


def write(folder: Path, key: str, raw: bytes, ext: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    stem = file_stem(key)
    for old in EXT:  # one picture per device, whatever its format
        (folder / f"{stem}{old}").unlink(missing_ok=True)
    name = f"{stem}{ext}"
    (folder / name).write_bytes(raw)
    return name


def remove(folder: Path, name: str | None) -> None:
    if name:
        (folder / Path(name).name).unlink(missing_ok=True)
