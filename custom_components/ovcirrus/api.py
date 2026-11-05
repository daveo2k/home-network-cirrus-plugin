"""Async client for the OmniVista Cirrus REST API."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

PAGE_SIZE = 200
TIMEOUT = aiohttp.ClientTimeout(total=45)


class CirrusError(Exception):
    """Base error."""


class CirrusAuthError(CirrusError):
    """Credentials rejected."""


class CirrusApiError(CirrusError):
    """Request failed."""


class CirrusClient:
    """Minimal OmniVista Cirrus API client."""

    def __init__(
        self, session: aiohttp.ClientSession, host: str, app_id: str, app_secret: str
    ) -> None:
        self._session = session
        self._host = host.strip().removeprefix("https://").rstrip("/")
        self._base = f"https://{self._host}/api"
        self._app_id = app_id
        self._app_secret = app_secret
        self._token: str | None = None
        self._token_expiry = 0.0
        self._auth_lock = asyncio.Lock()

    @property
    def _headers(self) -> dict[str, str]:
        # Cirrus rejects requests without these (csrf_missing_validation_headers).
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": f"https://{self._host}",
            "Referer": f"https://{self._host}/",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def authenticate(self) -> None:
        """Obtain a bearer token."""
        async with self._auth_lock:
            self._token = None
            try:
                async with self._session.post(
                    f"{self._base}/ov/v1/applications/authenticate",
                    json={"appId": self._app_id, "appSecret": self._app_secret},
                    headers=self._headers,
                    timeout=TIMEOUT,
                ) as resp:
                    body = await _read_json(resp)
                    if resp.status in (400, 401, 403):
                        raise CirrusAuthError(_err_text(resp.status, body))
                    if resp.status != 200 or not isinstance(body, dict):
                        raise CirrusApiError(_err_text(resp.status, body))
            except aiohttp.ClientError as err:
                raise CirrusApiError(f"Cannot reach {self._host}: {err}") from err
            token = body.get("access_token")
            if not token:
                raise CirrusAuthError("No access_token in response")
            self._token = token
            # Refresh an hour early (tokens last ~15 days).
            self._token_expiry = time.monotonic() + int(body.get("expires_in", 3600)) - 3600

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self._token or time.monotonic() > self._token_expiry:
            await self.authenticate()
        query = _encode_params(params or {})
        for attempt in (1, 2):
            try:
                async with self._session.get(
                    f"{self._base}{path}", params=query, headers=self._headers, timeout=TIMEOUT
                ) as resp:
                    body = await _read_json(resp)
                    if resp.status in (401, 403) and attempt == 1:
                        await self.authenticate()
                        continue
                    if resp.status in (401, 403):
                        raise CirrusAuthError(_err_text(resp.status, body))
                    if resp.status != 200:
                        raise CirrusApiError(f"{path}: {_err_text(resp.status, body)}")
                    return body.get("data") if isinstance(body, dict) else body
            except aiohttp.ClientError as err:
                raise CirrusApiError(f"{path}: {err}") from err
        raise CirrusApiError(f"{path}: retry exhausted")

    async def get_organizations(self) -> list[dict]:
        return await self._get("/ov/v1/organizations") or []

    async def get_sites(self, org_id: str) -> list[dict]:
        return await self._get(f"/ov/v1/organizations/{org_id}/sites") or []

    async def get_devices(self, org_id: str) -> list[dict]:
        return await self._get(f"/ov/v1/organizations/{org_id}/sites/devices") or []

    async def get_topology(self, org_id: str, site_id: str) -> dict:
        return (
            await self._get(
                f"/ov/v1/organizations/{org_id}/sites/{site_id}/topology",
                {"scope": "site", "scopeId[]": [site_id]},
            )
            or {}
        )

    async def get_lldp(self, org_id: str, serial: str) -> list[dict]:
        return (
            await self._get(
                f"/ov/v1/organizations/{org_id}/lan-analytics/switch/lldp",
                {"serialNumber": serial},
            )
            or []
        )

    async def get_ports(self, org_id: str, serial: str) -> list[dict]:
        data = await self._get(
            f"/ov/v1/organizations/{org_id}/lan-analytics/switch/ports-list",
            {"serialNumber": serial},
        )
        return (data or {}).get("ports") or [] if isinstance(data, dict) else []

    async def get_port_vlans(self, org_id: str, serial: str) -> list[dict]:
        # The server requires serialNumber although the spec documents serials[]; send both.
        return (
            await self._get(
                f"/ov/v1/organizations/{org_id}/lan-analytics/switch/ports-vlan-list",
                {"serialNumber": serial, "serials[]": [serial]},
            )
            or []
        )

    async def get_vlans(self, org_id: str, site_ids: list[str]) -> list[dict]:
        vlans: list[dict] = []
        offset = 0
        while True:
            page = await self._get(
                f"/ov/v1/organizations/{org_id}/vlan",
                {"limit": 100, "offset": offset, "filters": "{}", "sort": "[]", "scope": "site", "scopeId[]": site_ids},
            ) or {}
            rows = page.get("result") or [] if isinstance(page, dict) else []
            vlans.extend(rows)
            if len(rows) < 100 or offset > 4000:
                return vlans
            offset += 100

    async def get_wireless_clients(self, org_id: str, site_ids: list[str]) -> list[dict]:
        clients: list[dict] = []
        offset = 0
        while True:
            page = await self._get(
                f"/ov/v1/organizations/{org_id}/wlan-analytics/sessions/wireless/current",
                {
                    "scope": "site",
                    "scopeId[]": site_ids,
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "filters": "{}",
                    "sort": "[]",
                },
            )
            page = page or []
            clients.extend(page)
            if len(page) < PAGE_SIZE or offset > 20000:
                return clients
            offset += PAGE_SIZE


def _encode_params(params: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            out.extend((key, str(v)) for v in value)
        elif isinstance(value, (dict,)):
            out.append((key, json.dumps(value)))
        else:
            out.append((key, str(value)))
    return out


async def _read_json(resp: aiohttp.ClientResponse) -> Any:
    try:
        return await resp.json(content_type=None)
    except (ValueError, aiohttp.ContentTypeError):
        return (await resp.text())[:300]


def _err_text(status: int, body: Any) -> str:
    if isinstance(body, dict):
        detail = body.get("errorDetailsCode") or body.get("errorMsg") or body.get("message")
        return f"HTTP {status}: {detail}"
    return f"HTTP {status}: {body}"
