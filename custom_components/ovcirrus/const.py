"""Constants for the OmniVista Cirrus integration."""

from __future__ import annotations

DOMAIN = "ovcirrus"
VERSION = "0.2.14"

DEFAULT_HOST = "uk.manage.ovcirrus.com"
REGION_HOSTS = [
    "uk.manage.ovcirrus.com",
    "eu.manage.ovcirrus.com",
    "us.manage.ovcirrus.com",
    "manage.ovcirrus.com",
]

CONF_HOST = "host"
CONF_APP_ID = "app_id"
CONF_APP_SECRET = "app_secret"
CONF_ORG_ID = "org_id"
CONF_ORG_NAME = "org_name"
CONF_SITE_IDS = "site_ids"

# Options
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TRACKED_CLIENTS = "tracked_clients"
CONF_SSH_ENABLED = "ssh_enabled"
CONF_SSH_USERNAME = "ssh_username"
CONF_SSH_KEY_PATH = "ssh_key_path"
CONF_SSH_KNOWN_HOSTS = "ssh_known_hosts"
CONF_SSH_PORT = "ssh_port"
CONF_SSH_HOSTS = "ssh_hosts"
CONF_SSH_INTERVAL = "ssh_interval"
CONF_SSH_ARP = "ssh_arp"

CONF_DNS_MODE = "dns_mode"  # off | system | server
CONF_DNS_SERVERS = "dns_servers"
CONF_DNS_PREFER = "dns_prefer"
CONF_DNS_SHORT = "dns_short"
DNS_MODES = ["off", "system", "server"]

DEFAULT_SCAN_INTERVAL = 60
DEFAULT_SSH_INTERVAL = 120
DEFAULT_SSH_PORT = 22
DEFAULT_SSH_KEY_PATH = "/config/.ssh/id_ed25519"
# Topology / LLDP change rarely; refresh them less often than clients.
TOPOLOGY_INTERVAL = 300

CARD_URL = "/ovcirrus_static/ovcirrus-network-map.js"
IMAGES_URL = "/ovcirrus_static/images"
USER_IMAGES_DIR = "www/ovcirrus"  # under /config, served at /local/ovcirrus

# UniFi gateway (optional)
CONF_UNIFI_ENABLED = "unifi_enabled"
CONF_UNIFI_HOST = "unifi_host"
CONF_UNIFI_USERNAME = "unifi_username"
CONF_UNIFI_PASSWORD = "unifi_password"
CONF_UNIFI_SITE = "unifi_site"
CONF_UNIFI_VERIFY_SSL = "unifi_verify_ssl"

# Bandwidth history of the gateway ports (UniFi only)
CONF_RATE_INTERVAL = "rate_interval"  # seconds between readings
CONF_RATE_FINE_HOURS = "rate_fine_hours"  # every reading kept this long
CONF_RATE_LONG_DAYS = "rate_long_days"  # 1-minute averages kept this long
CONF_RATE_PERSIST = "rate_persist"  # keep the history across restarts
DEFAULT_RATE_INTERVAL = 15
DEFAULT_RATE_FINE_HOURS = 2
DEFAULT_RATE_LONG_DAYS = 1

# Manually added devices and service discovery
CONF_MANUAL_DEVICES = "manual_devices"
CONF_DISCOVERY_INTERVAL = "discovery_interval_hours"
DEFAULT_DISCOVERY_INTERVAL = 24

EVENT_NEW_CLIENT = "ovcirrus_new_client"
CACHE_DIR = "ovcirrus_cache"
CACHE_URL = "/ovcirrus_cache"
# Your own pictures of individual clients and devices (uploaded from the map)
PHOTOS_DIR = "ovcirrus_photos"  # under /config
PHOTOS_URL = "/ovcirrus_photos"
PHOTO_MAX_BYTES = 1024 * 1024

# Sidebar panel with the full-screen network map
CONF_SHOW_PANEL = "show_panel"
CONF_PANEL_TITLE = "panel_title"
CONF_PANEL_ADMIN = "panel_admin_only"
DEFAULT_PANEL_TITLE = "Network"
PANEL_URL_PATH = "ovcirrus-network"
PANEL_ELEMENT = "ovcirrus-network-panel"
