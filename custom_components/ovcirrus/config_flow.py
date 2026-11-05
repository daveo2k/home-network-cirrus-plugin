"""Config and options flow for OmniVista Cirrus."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CirrusApiError, CirrusAuthError, CirrusClient
from .const import (
    CONF_DISCOVERY_INTERVAL,
    CONF_MANUAL_DEVICES,
    CONF_UNIFI_ENABLED,
    CONF_UNIFI_HOST,
    CONF_UNIFI_PASSWORD,
    CONF_UNIFI_SITE,
    CONF_UNIFI_USERNAME,
    CONF_UNIFI_VERIFY_SSL,
    DEFAULT_DISCOVERY_INTERVAL,
    CONF_DNS_MODE,
    CONF_DNS_PREFER,
    CONF_DNS_SERVERS,
    CONF_DNS_SHORT,
    DNS_MODES,
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_HOST,
    CONF_ORG_ID,
    CONF_ORG_NAME,
    CONF_SCAN_INTERVAL,
    CONF_SITE_IDS,
    CONF_SSH_ARP,
    CONF_SSH_ENABLED,
    CONF_SSH_HOSTS,
    CONF_SSH_INTERVAL,
    CONF_SSH_KEY_PATH,
    CONF_SSH_KNOWN_HOSTS,
    CONF_SSH_PORT,
    CONF_SSH_USERNAME,
    CONF_TRACKED_CLIENTS,
    DEFAULT_HOST,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SSH_INTERVAL,
    DEFAULT_SSH_KEY_PATH,
    DEFAULT_SSH_PORT,
    DOMAIN,
    REGION_HOSTS,
)
import ipaddress
import uuid

from .dns import ReverseResolver, parse_servers
from .identify import TYPES
from .mac_table import norm_mac
from .unifi import UnifiAuthError, UnifiClient, UnifiError
from .mac_table import SshError, fetch_switch_tables
from .topology import client_name

_LOGGER = logging.getLogger(__name__)


def _ssh_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_SSH_ENABLED, default=defaults.get(CONF_SSH_ENABLED, False)): BooleanSelector(),
            vol.Optional(CONF_SSH_USERNAME, default=defaults.get(CONF_SSH_USERNAME, "")): TextSelector(),
            vol.Optional(
                CONF_SSH_KEY_PATH, default=defaults.get(CONF_SSH_KEY_PATH, DEFAULT_SSH_KEY_PATH)
            ): TextSelector(),
            vol.Optional(CONF_SSH_KNOWN_HOSTS, default=defaults.get(CONF_SSH_KNOWN_HOSTS, "")): TextSelector(),
            vol.Optional(CONF_SSH_HOSTS, default=defaults.get(CONF_SSH_HOSTS, "")): TextSelector(
                TextSelectorConfig(multiline=True)
            ),
            vol.Optional(CONF_SSH_PORT, default=defaults.get(CONF_SSH_PORT, DEFAULT_SSH_PORT)): NumberSelector(
                NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(
                CONF_SSH_INTERVAL, default=defaults.get(CONF_SSH_INTERVAL, DEFAULT_SSH_INTERVAL)
            ): NumberSelector(NumberSelectorConfig(min=30, max=3600, step=10, unit_of_measurement="s")),
            vol.Optional(CONF_SSH_ARP, default=defaults.get(CONF_SSH_ARP, True)): BooleanSelector(),
        }
    )


async def _test_ssh(hass, user_input: dict, devices: list[dict]) -> str | None:
    """Return an error key, or None if at least one switch answered."""
    if not user_input.get(CONF_SSH_ENABLED):
        return None
    from .coordinator import parse_ssh_hosts

    overrides = parse_ssh_hosts(user_input.get(CONF_SSH_HOSTS))
    hosts = [
        overrides.get(d.get("serialNumber")) or d.get("ipAddress")
        for d in devices
        if d.get("deviceFamily") == "AOS"
    ]
    hosts = [h for h in hosts if h]
    if not hosts:
        return "ssh_no_switch"
    try:
        await fetch_switch_tables(
            hosts[0],
            user_input.get(CONF_SSH_USERNAME, ""),
            user_input.get(CONF_SSH_KEY_PATH) or None,
            user_input.get(CONF_SSH_KNOWN_HOSTS) or None,
            int(user_input.get(CONF_SSH_PORT, DEFAULT_SSH_PORT)),
            include_arp=False,
        )
    except SshError as err:
        _LOGGER.warning("SSH test failed: %s", err)
        return "ssh_failed"
    return None


class CirrusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._client: CirrusClient | None = None
        self._orgs: list[dict] = []
        self._sites: list[dict] = []
        self._devices: list[dict] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = CirrusClient(
                async_get_clientsession(self.hass),
                user_input[CONF_HOST],
                user_input[CONF_APP_ID].strip(),
                user_input[CONF_APP_SECRET].strip(),
            )
            try:
                await client.authenticate()
                self._orgs = await client.get_organizations()
            except CirrusAuthError:
                errors["base"] = "invalid_auth"
            except CirrusApiError:
                errors["base"] = "cannot_connect"
            else:
                if not self._orgs:
                    errors["base"] = "no_orgs"
                else:
                    self._client = client
                    self._data = {
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_APP_ID: user_input[CONF_APP_ID].strip(),
                        CONF_APP_SECRET: user_input[CONF_APP_SECRET].strip(),
                    }
                    if len(self._orgs) == 1:
                        return await self.async_step_org({CONF_ORG_ID: self._orgs[0]["id"]})
                    return await self.async_step_org()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=DEFAULT_HOST): SelectSelector(
                    SelectSelectorConfig(options=REGION_HOSTS, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(CONF_APP_ID): TextSelector(),
                vol.Required(CONF_APP_SECRET): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_org(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            org = next(o for o in self._orgs if o["id"] == user_input[CONF_ORG_ID])
            await self.async_set_unique_id(org["id"])
            self._abort_if_unique_id_configured()
            self._data[CONF_ORG_ID] = org["id"]
            self._data[CONF_ORG_NAME] = org.get("name", org["id"])
            try:
                self._sites = await self._client.get_sites(org["id"])
                self._devices = await self._client.get_devices(org["id"])
            except CirrusApiError:
                return self.async_abort(reason="cannot_connect")
            if len(self._sites) <= 1:
                return await self.async_step_sites({CONF_SITE_IDS: [s["id"] for s in self._sites]})
            return await self.async_step_sites()

        options = [SelectOptionDict(value=o["id"], label=o.get("name", o["id"])) for o in self._orgs]
        return self.async_show_form(
            step_id="org",
            data_schema=vol.Schema({vol.Required(CONF_ORG_ID): SelectSelector(SelectSelectorConfig(options=options))}),
        )

    async def async_step_sites(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data[CONF_SITE_IDS] = user_input.get(CONF_SITE_IDS) or [s["id"] for s in self._sites]
            return await self.async_step_ssh()
        options = [SelectOptionDict(value=s["id"], label=s.get("name", s["id"])) for s in self._sites]
        return self.async_show_form(
            step_id="sites",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SITE_IDS, default=[s["id"] for s in self._sites]): SelectSelector(
                        SelectSelectorConfig(options=options, multiple=True)
                    )
                }
            ),
        )

    async def async_step_ssh(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            site_ids = set(self._data[CONF_SITE_IDS])
            devices = [d for d in self._devices if (d.get("site") or {}).get("id") in site_ids]
            error = await _test_ssh(self.hass, user_input, devices)
            if error:
                errors["base"] = error
            else:
                options = {CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL, CONF_TRACKED_CLIENTS: [], **user_input}
                return self.async_create_entry(title=self._data[CONF_ORG_NAME], data=self._data, options=options)
        switches = ", ".join(
            f"{d.get('name')} = {d.get('serialNumber')} ({d.get('ipAddress')})"
            for d in self._devices
            if d.get("deviceFamily") == "AOS"
        )
        return self.async_show_form(
            step_id="ssh",
            data_schema=_ssh_schema(user_input or {}),
            errors=errors,
            description_placeholders={"switches": switches or "none found"},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            client = CirrusClient(
                async_get_clientsession(self.hass),
                entry.data[CONF_HOST],
                user_input[CONF_APP_ID].strip(),
                user_input[CONF_APP_SECRET].strip(),
            )
            try:
                await client.authenticate()
            except CirrusAuthError:
                errors["base"] = "invalid_auth"
            except CirrusApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_APP_ID: user_input[CONF_APP_ID].strip(),
                        CONF_APP_SECRET: user_input[CONF_APP_SECRET].strip(),
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_APP_ID, default=entry.data.get(CONF_APP_ID, "")): TextSelector(),
                    vol.Required(CONF_APP_SECRET): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CirrusOptionsFlow()


class CirrusOptionsFlow(OptionsFlow):
    """Options: polling, tracked clients and switch SSH."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["general", "clients", "manual", "ssh", "dns", "unifi"])

    def _save(self, updates: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **updates})

    async def async_step_general(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(
                {
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_DISCOVERY_INTERVAL: int(user_input[CONF_DISCOVERY_INTERVAL]),
                }
            )
        opts = self.config_entry.options
        return self.async_show_form(
            step_id="general",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                    ): NumberSelector(NumberSelectorConfig(min=30, max=900, step=10, unit_of_measurement="s")),
                    vol.Required(
                        CONF_DISCOVERY_INTERVAL,
                        default=opts.get(CONF_DISCOVERY_INTERVAL, DEFAULT_DISCOVERY_INTERVAL),
                    ): NumberSelector(NumberSelectorConfig(min=1, max=168, step=1, unit_of_measurement="h")),
                }
            ),
        )

    # ---- manually added devices --------------------------------------------------
    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="manual", menu_options=["manual_add", "manual_remove"])

    async def async_step_manual_add(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        coordinator = self.config_entry.runtime_data
        switches = [
            d for d in (coordinator.data.devices.values() if coordinator.data else []) if d.get("deviceFamily") == "AOS"
        ]
        if user_input is not None:
            ip = (user_input.get("ip") or "").strip()
            mac = norm_mac(user_input.get("mac")) if user_input.get("mac") else None
            if ip:
                try:
                    ipaddress.ip_address(ip)
                except ValueError:
                    errors["ip"] = "invalid_ip"
            if user_input.get("mac") and not mac:
                errors["mac"] = "invalid_mac"
            if not ip and not mac:
                errors["base"] = "ip_or_mac"
            if user_input.get("discover") and not ip:
                errors["discover"] = "discover_needs_ip"
            if not errors:
                device = {
                    "id": uuid.uuid4().hex[:8],
                    "name": user_input["name"].strip(),
                    "ip": ip or None,
                    "mac": mac,
                    "type": user_input.get("type") or "server",
                    "switch": user_input.get("switch") or None,
                    "port": (user_input.get("port") or "").strip() or None,
                    "discover": bool(user_input.get("discover")),
                    "notes": (user_input.get("notes") or "").strip() or None,
                }
                devices = [*self.config_entry.options.get(CONF_MANUAL_DEVICES, []), device]
                return self._save({CONF_MANUAL_DEVICES: devices})
        defaults = user_input or {}
        switch_options = [SelectOptionDict(value="", label="Find it automatically (MAC table / ARP)")] + [
            SelectOptionDict(value=d["serialNumber"], label=f"{d.get('name')} ({d.get('ipAddress')})") for d in switches
        ]
        return self.async_show_form(
            step_id="manual_add",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=defaults.get("name", "")): TextSelector(),
                    vol.Optional("ip", default=defaults.get("ip", "")): TextSelector(),
                    vol.Optional("mac", default=defaults.get("mac", "")): TextSelector(),
                    vol.Required("type", default=defaults.get("type", "server")): SelectSelector(
                        SelectSelectorConfig(
                            options=[SelectOptionDict(value=k, label=v[0]) for k, v in TYPES.items()],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional("switch", default=defaults.get("switch", "")): SelectSelector(
                        SelectSelectorConfig(options=switch_options, mode=SelectSelectorMode.DROPDOWN)
                    ),
                    vol.Optional("port", default=defaults.get("port", "")): TextSelector(),
                    vol.Optional("discover", default=defaults.get("discover", True)): BooleanSelector(),
                    vol.Optional("notes", default=defaults.get("notes", "")): TextSelector(
                        TextSelectorConfig(multiline=True)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_manual_remove(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        devices = list(self.config_entry.options.get(CONF_MANUAL_DEVICES, []))
        if user_input is not None:
            remove = set(user_input.get("remove", []))
            return self._save({CONF_MANUAL_DEVICES: [d for d in devices if d["id"] not in remove]})
        if not devices:
            return self.async_abort(reason="no_manual_devices")
        return self.async_show_form(
            step_id="manual_remove",
            data_schema=vol.Schema(
                {
                    vol.Optional("remove", default=[]): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=d["id"], label=f"{d['name']} ({d.get('ip') or d.get('mac')})")
                                for d in devices
                            ],
                            multiple=True,
                        )
                    )
                }
            ),
        )

    # ---- UniFi gateway --------------------------------------------------------------
    async def async_step_unifi(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        opts = self.config_entry.options
        if user_input is not None:
            if user_input.get(CONF_UNIFI_ENABLED):
                password = user_input.get(CONF_UNIFI_PASSWORD) or opts.get(CONF_UNIFI_PASSWORD, "")
                client = UnifiClient(
                    user_input.get(CONF_UNIFI_HOST, ""),
                    user_input.get(CONF_UNIFI_USERNAME, ""),
                    password,
                    user_input.get(CONF_UNIFI_SITE) or "default",
                    bool(user_input.get(CONF_UNIFI_VERIFY_SSL)),
                )
                try:
                    await client.login()
                    await client.health()
                except UnifiAuthError:
                    errors["base"] = "unifi_auth"
                except UnifiError as err:
                    _LOGGER.warning("UniFi test failed: %s", err)
                    errors["base"] = "unifi_connect"
                finally:
                    await client.close()
                user_input[CONF_UNIFI_PASSWORD] = password
            if not errors:
                return self._save(user_input)
        defaults = {**opts, **(user_input or {})}
        gateway = ""
        coordinator = self.config_entry.runtime_data
        if coordinator.data:
            for site_map in coordinator.data.maps.values():
                for group in site_map["root"]["groups"]:
                    for child in group["children"]:
                        if child["kind"] == "gateway":
                            gateway = child.get("ip") or ""
        return self.async_show_form(
            step_id="unifi",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_UNIFI_ENABLED, default=defaults.get(CONF_UNIFI_ENABLED, False)): BooleanSelector(),
                    vol.Optional(CONF_UNIFI_HOST, default=defaults.get(CONF_UNIFI_HOST, gateway)): TextSelector(),
                    vol.Optional(CONF_UNIFI_USERNAME, default=defaults.get(CONF_UNIFI_USERNAME, "")): TextSelector(),
                    vol.Optional(CONF_UNIFI_PASSWORD, default=""): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(CONF_UNIFI_SITE, default=defaults.get(CONF_UNIFI_SITE, "default")): TextSelector(),
                    vol.Optional(
                        CONF_UNIFI_VERIFY_SSL, default=defaults.get(CONF_UNIFI_VERIFY_SSL, False)
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={"gateway": gateway or "your gateway"},
        )

    async def async_step_clients(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save({CONF_TRACKED_CLIENTS: user_input.get(CONF_TRACKED_CLIENTS, [])})
        coordinator = self.config_entry.runtime_data
        tracked = list(self.config_entry.options.get(CONF_TRACKED_CLIENTS, []))
        options: dict[str, str] = {}
        for mac, client in sorted(
            (coordinator.data.clients if coordinator.data else {}).items(),
            key=lambda kv: client_name(kv[1], {}).lower(),
        ):
            options[mac] = f"{client_name(client, {})} · {mac} · {client.get('ssid')}"
        for mac in tracked:
            options.setdefault(mac, f"{mac} (not connected now)")
        return self.async_show_form(
            step_id="clients",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_TRACKED_CLIENTS, default=tracked): SelectSelector(
                        SelectSelectorConfig(
                            options=[SelectOptionDict(value=k, label=v) for k, v in options.items()],
                            multiple=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_ssh(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        coordinator = self.config_entry.runtime_data
        devices = list(coordinator.data.devices.values()) if coordinator.data else []
        if user_input is not None:
            error = await _test_ssh(self.hass, user_input, devices)
            if error:
                errors["base"] = error
            else:
                return self._save(user_input)
        switches = ", ".join(
            f"{d.get('name')} = {d.get('serialNumber')} ({d.get('ipAddress')})"
            for d in devices
            if d.get("deviceFamily") == "AOS"
        )
        return self.async_show_form(
            step_id="ssh",
            data_schema=_ssh_schema(user_input or self.config_entry.options),
            errors=errors,
            description_placeholders={"switches": switches or "none found"},
        )

    async def async_step_dns(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        opts = self.config_entry.options
        coordinator = self.config_entry.runtime_data
        if user_input is not None:
            mode = user_input[CONF_DNS_MODE]
            servers = parse_servers(user_input.get(CONF_DNS_SERVERS))
            if mode == "server" and not servers:
                errors["base"] = "dns_no_server"
            elif mode == "server":
                probe = next(
                    (c.get("clientIp") for c in (coordinator.data.clients.values() if coordinator.data else []) if c.get("clientIp")),
                    servers[0][0],
                )
                if not await ReverseResolver(mode, servers).test(probe):
                    errors["base"] = "dns_failed"
            if not errors:
                return self._save(user_input)
        defaults = user_input or opts
        gateway = ""
        if coordinator.data:
            for site_map in coordinator.data.maps.values():
                for group in site_map["root"]["groups"]:
                    for child in group["children"]:
                        if child["kind"] == "gateway":
                            gateway = next((v for k, v in child["details"] if k == "IP"), "")
        return self.async_show_form(
            step_id="dns",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DNS_MODE, default=defaults.get(CONF_DNS_MODE, "off")): SelectSelector(
                        SelectSelectorConfig(options=DNS_MODES, translation_key="dns_mode", mode=SelectSelectorMode.LIST)
                    ),
                    vol.Optional(CONF_DNS_SERVERS, default=defaults.get(CONF_DNS_SERVERS, gateway)): TextSelector(),
                    vol.Optional(CONF_DNS_PREFER, default=defaults.get(CONF_DNS_PREFER, False)): BooleanSelector(),
                    vol.Optional(CONF_DNS_SHORT, default=defaults.get(CONF_DNS_SHORT, True)): BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={"gateway": gateway or "your gateway"},
        )
