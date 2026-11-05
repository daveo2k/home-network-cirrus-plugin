"""Identify client devices: vendor (IEEE OUI), brand, device type, icon and logo.

Pure Python so it can be tested without Home Assistant. Nothing here is a list of *your*
devices: identification comes from the MAC vendor registry, hostname patterns, the
Cirrus fingerprint, Home Assistant's device registry and (optionally) UniFi.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TYPES: dict[str, tuple[str, str]] = {
    # type: (label, mdi icon)
    "phone": ("Phone", "mdi:cellphone"),
    "tablet": ("Tablet", "mdi:tablet"),
    "laptop": ("Laptop", "mdi:laptop"),
    "computer": ("Computer", "mdi:desktop-tower-monitor"),
    "watch": ("Watch", "mdi:watch"),
    "speaker": ("Speaker", "mdi:speaker"),
    "voice": ("Voice assistant", "mdi:speaker-message"),
    "tv": ("TV / streamer", "mdi:television"),
    "plug": ("Smart plug", "mdi:power-socket-uk"),
    "light": ("Light", "mdi:lightbulb"),
    "camera": ("Camera", "mdi:cctv"),
    "doorbell": ("Doorbell", "mdi:doorbell-video"),
    "appliance": ("Appliance", "mdi:washing-machine"),
    "heatpump": ("Heat pump", "mdi:heat-pump"),
    "thermostat": ("Thermostat", "mdi:thermostat"),
    "blinds": ("Blinds / windows", "mdi:blinds"),
    "printer": ("Printer", "mdi:printer"),
    "hub": ("Smart-home hub", "mdi:hub"),
    "iot": ("IoT device", "mdi:chip"),
    "console": ("Games console", "mdi:gamepad-variant"),
    "nas": ("NAS", "mdi:nas"),
    "server": ("Server", "mdi:server"),
    "network": ("Network device", "mdi:router-wireless"),
    "ev": ("EV charger", "mdi:ev-station"),
    "vacuum": ("Robot vacuum", "mdi:robot-vacuum"),
    "unknown": ("Device", "mdi:devices"),
}

# Hostname patterns -> (type, brand or None). First match wins; order matters.
HOST_RULES: list[tuple[re.Pattern, str, str | None]] = [
    (re.compile(p, re.I), t, b)
    for p, t, b in [
        (r"ipad", "tablet", "Apple"),
        (r"iphone", "phone", "Apple"),
        (r"watch", "watch", None),
        (r"macbook|^mac(-|$)|imac|mac-?mini|mac-?studio", "laptop", "Apple"),
        (r"homepod", "speaker", "Apple"),
        (r"apple-?tv", "tv", "Apple"),
        (r"galaxy-?tab|sm-[tx]\d", "tablet", "Samsung"),
        (r"galaxy|s\d\d-?(ultra|plus|fe)?$|-s\d\d-|sm-[gsa]\d", "phone", "Samsung"),
        (r"pixel", "phone", "Google"),
        (r"android|oneplus|redmi|poco|huawei|oppo|motorola|moto-", "phone", None),
        (r"tab(let)?[-_]|fire-?tab|kindle", "tablet", None),
        (r"sonos", "speaker", "Sonos"),
        (r"echo|alexa|^amazon-[0-9a-f]+$", "voice", "Amazon"),
        (r"fire-?tv|firestick|aft[a-z]", "tv", "Amazon"),
        (r"chromecast|google-?home|nest-?(audio|hub|mini)", "speaker", "Google"),
        (r"nest", "thermostat", "Google"),
        (r"roku", "tv", "Roku"),
        (r"bravia|lgwebos|webos|samsung-?tv|^tv[-_]|-tv$|shield|tizen|vidaa", "tv", None),
        (r"doorbell|^ring-", "doorbell", None),
        (r"cam(era)?[-_\d]|reolink|hikvision|dahua|wyze|eufy|tapo-?c\d", "camera", None),
        (r"^p1\d\dm?$|^p3\d\d|^hs1\d\d|^kp\d|^ep\d\d|tapo|plug|sonoff|shelly-?plug", "plug", None),
        (r"^l\d\d\d|bulb|lamp|light|wled|hue", "light", None),
        (r"nibe", "heatpump", "NIBE"),
        (r"velux", "blinds", "VELUX"),
        (r"ecobee|tado|hive|thermostat|netatmo", "thermostat", None),
        (r"printer|^hp[0-9a-f]{6}|^npi[0-9a-f]+|epson|brother|canon|^et[0-9a-f]{12}", "printer", None),
        (r"roomba|roborock|dreame|deebot|vacuum", "vacuum", None),
        (r"wallbox|zappi|easee|ohme|charger", "ev", None),
        (r"ps[45]|playstation|xbox|nintendo|steamdeck", "console", None),
        (r"synology|diskstation|qnap|truenas|unraid|-nas$|^nas", "nas", None),
        (r"proxmox|^pve|server|^srv|ubuntu|debian|docker|k8s|unraid", "server", None),
        (r"tasmota|esphome|^esp[-_]|espressif|tuya|^wlan\d|shelly", "iot", None),
        (r"desktop|-pc$|^pc-|workstation", "computer", None),
        (r"laptop|thinkpad|xps|surface|chromebook|notebook", "laptop", None),
    ]
]

# Vendor (normalised) -> default type when nothing better is known.
VENDOR_TYPES: dict[str, str] = {
    "Google": "speaker", "Amazon": "voice", "Sonos": "speaker",
    "Bosch": "appliance", "Siemens": "appliance", "Miele": "appliance", "Netatmo": "hub", "Aqara": "hub",
    "Philips Hue": "light", "Signify": "light", "TP-Link": "iot", "Xiaomi": "iot", "Tuya": "iot",
    "Shelly": "iot", "HP": "printer", "Epson": "printer", "Brother": "printer", "Canon": "printer",
    "Raspberry Pi": "server", "Synology": "nas", "QNAP": "nas", "Ubiquiti": "network",
    "Ring": "doorbell", "Roku": "tv", "Sony": "tv", "LG": "tv", "Hisense": "tv", "Nintendo": "console",
    "NIBE": "heatpump", "VELUX": "blinds", "Dyson": "appliance", "iRobot": "vacuum", "Roborock": "vacuum",
}

# Companies that make Wi-Fi modules for other brands; their name is not the product brand.
CHIP_VENDORS = {
    "Espressif", "Texas Instruments", "Murata", "AzureWave", "Realtek", "MediaTek", "Qualcomm",
    "Broadcom", "Foxconn", "Lite-On", "Universal Global Scientific", "Silicon Labs", "Beken",
    "Nordic Semiconductor", "Infineon", "Cypress", "Gaoshengda", "Chongqing Fugui", "Wistron NeWeb",
    "Hon Hai", "Tonly", "Barrot",
}

VENDOR_ALIASES = [
    (r"^BSH Hausger", "Bosch"),
    (r"^Lumi United", "Aqara"),
    (r"Xiaomi", "Xiaomi"),
    (r"^Hewlett Packard|^HP Inc", "HP"),
    (r"^Amazon", "Amazon"),
    (r"^TP-?Link", "TP-Link"),
    (r"^Apple", "Apple"),
    (r"^Samsung", "Samsung"),
    (r"^Google", "Google"),
    (r"^Raspberry Pi", "Raspberry Pi"),
    (r"^Ubiquiti", "Ubiquiti"),
    (r"^Hon Hai|Foxconn", "Foxconn"),
    (r"^Intel", "Intel"),
    (r"^Liteon|^Lite-On", "Lite-On"),
    (r"^Signify|^Philips Lighting", "Philips Hue"),
    (r"^Sonos", "Sonos"),
    (r"^Espressif", "Espressif"),
    (r"^Texas Instruments", "Texas Instruments"),
    (r"^Tuya", "Tuya"),
    (r"^Shelly|^Allterco", "Shelly"),
    (r"^LG Electronics|^LG Innotek", "LG"),
    (r"^Sony", "Sony"),
    (r"^Microsoft", "Microsoft"),
    (r"^Dell", "Dell"),
    (r"^Lenovo", "Lenovo"),
    (r"^ASUSTek", "Asus"),
    (r"^Synology", "Synology"),
    (r"^QNAP", "QNAP"),
    (r"^Ring", "Ring"),
    (r"^Nintendo", "Nintendo"),
    (r"^Netatmo", "Netatmo"),
    (r"^Roku", "Roku"),
    (r"^Hisense", "Hisense"),
    (r"^iRobot", "iRobot"),
    (r"^Dyson", "Dyson"),
    (r"^Miele", "Miele"),
    (r"^Siemens", "Siemens"),
    (r"^Murata", "Murata"),
    (r"^AzureWave", "AzureWave"),
    (r"^Realtek", "Realtek"),
    (r"^MediaTek", "MediaTek"),
    (r"^Universal Global Scientific", "Universal Global Scientific"),
    (r"^Silicon Lab", "Silicon Labs"),
    (r"Gaoshengda", "Gaoshengda"),
    (r"Chongqing Fugui", "Chongqing Fugui"),
]
_SUFFIX = re.compile(
    r"[,.]?\s+(inc|ltd|llc|gmbh|co|corp|corporation|company|limited|technologies|technology|"
    r"electronics|systems|international|holdings|group|ag|sa|bv|nv|oy|ab|spa|plc|kg)\b\.?",
    re.I,
)

# Brand -> logo sources, tried in order: Home Assistant brand images, then Simple Icons.
BRAND_LOGOS: dict[str, list[str]] = {
    "Apple": ["si:apple"], "Amazon": ["ha:alexa_devices"], "Sonos": ["ha:sonos"],
    "TP-Link": ["ha:tplink"], "Xiaomi": ["ha:xiaomi_miio"], "Espressif": ["si:espressif"],
    "ESPHome": ["ha:esphome"], "Netatmo": ["ha:netatmo"], "HP": ["si:hp"], "Bosch": ["ha:home_connect", "si:bosch"],
    "Siemens": ["ha:home_connect"], "Aqara": ["ha:xiaomi_aqara"], "Samsung": ["ha:samsungtv", "si:samsung"],
    "Google": ["ha:google", "si:google"], "NIBE": ["ha:nibe_heatpump"], "VELUX": ["ha:velux"],
    "Tuya": ["ha:tuya"], "Shelly": ["ha:shelly"], "IKEA": ["si:ikea"], "LG": ["ha:lg_thinq", "si:lg"],
    "Sony": ["si:sony"], "Roku": ["ha:roku"], "Ring": ["ha:ring"], "Philips Hue": ["ha:hue"],
    "Ubiquiti": ["ha:unifi"], "Synology": ["ha:synology_dsm"], "Raspberry Pi": ["si:raspberrypi"],
    "Dell": ["si:dell"], "Lenovo": ["si:lenovo"], "Asus": ["si:asus"], "Intel": ["si:intel"],
    "Microsoft": ["ha:microsoft"], "Hisense": ["ha:hisense"], "Honeywell": ["ha:honeywell"],
    "Roborock": ["ha:roborock"], "iRobot": ["ha:roomba"], "Dyson": [], "Nintendo": [],
}

# Home Assistant integration domain -> device type.
DOMAIN_TYPES = {
    "sonos": "speaker", "nibe_heatpump": "heatpump", "velux": "blinds", "esphome": "iot",
    "mobile_app": "phone", "alexa_devices": "voice", "ring": "doorbell", "hue": "light",
    "home_connect": "appliance", "roborock": "vacuum", "roomba": "vacuum", "cast": "speaker",
    "androidtv_remote": "tv", "webostv": "tv", "samsungtv": "tv", "apple_tv": "tv", "roku": "tv",
    "ipp": "printer", "brother": "printer", "synology_dsm": "nas", "proxmoxve": "server",
    "shelly": "iot", "tuya": "iot", "netatmo": "hub", "xiaomi_aqara": "hub", "tado": "thermostat",
}

GENERIC_HOSTNAMES = re.compile(
    r"^(iphone|ipad|android(-[0-9a-f]+)?|galaxy.*|watch|mac|macbook.*|unknown.*|localhost|espressif|"
    r"esp[-_]?[0-9a-f]*|amazon-[0-9a-f]+|p1\d\dm?|wlan\d*|)$",
    re.I,
)


def normalise_vendor(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw.lower() == "private":
        return None
    for pattern, name in VENDOR_ALIASES:
        if re.search(pattern, raw, re.I):
            return name
    short = _SUFFIX.sub("", raw)
    short = _SUFFIX.sub("", short).strip(" ,.")
    return short[:40] or None


def is_private_mac(mac: str | None) -> bool:
    try:
        return bool(int((mac or "")[:2], 16) & 0x02)
    except ValueError:
        return False


@dataclass
class HaDevice:
    name: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    domain: str | None = None


@dataclass
class Identity:
    vendor: str | None = None  # from the MAC registry
    brand: str | None = None
    type: str = "unknown"
    model: str | None = None
    private_mac: bool = False
    logo_sources: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def type_label(self) -> str:
        return TYPES.get(self.type, TYPES["unknown"])[0]

    @property
    def icon(self) -> str:
        return TYPES.get(self.type, TYPES["unknown"])[1]

    def describe(self) -> str:
        """Human label when there is no name, e.g. 'Espressif IoT device'."""
        if self.model and self.brand:
            return f"{self.brand} {self.model}"
        maker = self.brand or self.vendor
        if maker:
            return f"{maker} {self.type_label}" if self.type != "unknown" else f"{maker} device"
        return self.type_label


def identify(
    mac: str | None,
    hostname: str | None = None,
    oui_vendor: str | None = None,
    cirrus_os: str | None = None,
    cirrus_type: str | None = None,
    ha: HaDevice | None = None,
    unifi_vendor: str | None = None,
    override_type: str | None = None,
) -> Identity:
    ident = Identity(private_mac=is_private_mac(mac))
    vendor = normalise_vendor(oui_vendor) or normalise_vendor(unifi_vendor)
    ident.vendor = vendor
    if vendor:
        ident.sources.append("MAC vendor")
    host = (hostname or "").strip()

    host_type = host_brand = None
    for pattern, typ, brand in HOST_RULES:
        if host and pattern.search(host):
            host_type, host_brand = typ, brand
            ident.sources.append("hostname")
            break

    cirrus = None
    ctype, cos = (cirrus_type or "").lower(), (cirrus_os or "").lower()
    if ctype == "ipad" or "ipados" in cos:
        cirrus = "tablet"
    elif ctype == "mobile" or cos in ("ios", "android"):
        cirrus = "phone"
    elif ctype == "computer" or "windows" in cos or "mac os" in cos or "linux" in cos:
        cirrus = "laptop"
    if cirrus:
        ident.sources.append("Cirrus fingerprint")

    ha_type = None
    if ha and (ha.domain or ha.model):
        ident.sources.append("Home Assistant")
        ha_type = DOMAIN_TYPES.get(ha.domain or "")
        ident.model = ha.model
        if ha.domain:
            ident.logo_sources.append(f"ha:{ha.domain}")

    # Brand: HA manufacturer > hostname hint > vendor (unless it is only the Wi-Fi chip maker).
    brand = None
    if ha and ha.manufacturer:
        brand = normalise_vendor(ha.manufacturer)
    brand = brand or host_brand
    if not brand and vendor and vendor not in CHIP_VENDORS:
        brand = vendor
    if not brand and cirrus in ("phone", "tablet", "laptop") and ("ios" in cos or "mac os" in cos):
        brand = "Apple"
    ident.brand = brand

    vendor_type = "iot" if vendor in CHIP_VENDORS else VENDOR_TYPES.get(vendor or "")
    # A phone hostname beats Cirrus calling it a computer (Cirrus guesses OS from traffic).
    if host_type in ("phone", "tablet", "watch") and cirrus == "laptop":
        cirrus = None
    ident.type = (
        override_type
        or ha_type
        or host_type
        or cirrus
        or VENDOR_TYPES.get(brand or "")
        or vendor_type
        or "unknown"
    )
    if override_type:
        ident.sources.insert(0, "your override")

    if brand:
        ident.logo_sources.extend(BRAND_LOGOS.get(brand, []))
    elif vendor in ("Espressif",):
        ident.logo_sources.extend(BRAND_LOGOS["Espressif"])
    return ident


def is_generic_hostname(hostname: str | None) -> bool:
    return bool(GENERIC_HOSTNAMES.match((hostname or "").strip()))
