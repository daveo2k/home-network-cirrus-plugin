# OmniVista Cirrus for Home Assistant

Brings Alcatel-Lucent Enterprise **OmniVista Cirrus** data into Home Assistant, with an automatically drawn **network map** card.

## What you get

| Where | Entities / data |
|---|---|
| Each AP / switch (HA device) | Online (connectivity), uptime, firmware, IP; APs also get client count (with per-SSID / per-band attributes) and *Channels* (e.g. `1 / 36`) |
| Each AP radio (2.4 / 5 / 6 GHz) | Channel, clients, channel utilisation, TX power, noise floor, channel width. Radios that appear later are added automatically |
| Each site | Wireless clients, wired clients (needs SSH MAC tables), one sensor per SSID |
| Tracked clients (opt-in) | `device_tracker` per chosen client: home/away plus IP, hostname, SSID, AP, RSSI, band, auth type, 802.1X user, VLAN, OS, traffic |
| Network map card | Internet → gateway → switches → APs / wired devices → clients grouped by SSID, with port + link-speed labels |

## Network map

The map is built from:

1. **Cirrus L2 topology + LLDP**: switch ports, neighbours (gateway, APs, LLDP-speaking hosts), link speed and state.
2. **Cirrus wireless sessions**: each client is attached to its AP and grouped by SSID.
3. **Switch MAC tables (optional, SSH)**: `show mac-learning` plus `show arp`, read directly from each OmniSwitch. This lets the map:
   - show wired clients on the right switch port, with IP from ARP,
   - place devices Cirrus has no LLDP link for (e.g. an AP behind an unmanaged switch) under the correct port,
   - draw an *Unmanaged device* when a port has several MACs and no LLDP neighbour.
4. **HA device registry**: if another integration knows a MAC (router, ESPHome, etc.), its name is used.

Inferred links (from MAC tables rather than LLDP) are dashed. Cirrus does not report the WAN link, so *Internet* is always drawn.

### Network map in the sidebar

The setup wizard's last step adds **Network** to Home Assistant's sidebar: a full-screen page with just the map, so you don't need to build a dashboard. Change the title, restrict it to administrators, or turn it off under the integration's **Configure** → **General**.

### Adding the map to a dashboard

The card is registered automatically when the integration loads; there is no resource to add.

**On an existing dashboard:** open the dashboard → ✏️ **Edit** → **Add card** → search for **OmniVista Cirrus network map** (or choose **Manual** and enter `type: custom:ovcirrus-network-map`).

**As its own full-screen dashboard:** Settings → **Dashboards** → **Add dashboard** → *New dashboard from scratch*, name it (e.g. "Network"), open it → ✏️ → ⋮ → **Raw configuration editor**, paste this and save:

```yaml
views:
  - title: Network
    path: network
    type: panel        # one card filling the whole view
    cards:
      - type: custom:ovcirrus-network-map
        height: 820
```

If the card shows "Custom element doesn't exist", refresh the browser (or clear the frontend cache in the HA app) after restarting Home Assistant.

### Card

The card is registered automatically. Add it to a dashboard:

```yaml
type: custom:ovcirrus-network-map
# all optional:
title: Network map
site: Home                # site name or id (default: first; a picker appears if several)
height: 640               # px
refresh: 30               # seconds
expand_clients: false     # start with AP client lists expanded
max_columns: 3            # client chips per row inside an SSID box
text_size: 12             # pt size of device names (8-30); other map labels scale with it; viewers can change it in the View menu
layout_editing: admin     # who can drag devices and save the layout: admin (default), true or false
snap_to_grid: true        # snap dragged devices to a grid (viewers can toggle it in edit mode)
grid_size: 20             # grid spacing in px
device_size: 1            # starting size of gateways, switches, APs and wired devices (0.5-2); viewers can change it
client_size: 1            # starting size of wireless and wired clients (0.25-2); viewers can change it
show_vlans: true          # VLAN legend above the map
```

Click a node for details, use **+ / −** on a node to expand or collapse it, and the zoom buttons (or **fit**) to size the map. **View** opens the size settings: **Text** (map names and labels only), **Clients** (0.75× to 2.5×) and **Devices** (1× to 3×: rings, photos, the AP client-count bubble and brand logos). Changes are remembered in each browser. Admins can **Save as default for all devices**, which stores the sizes in Home Assistant so every browser and the app start with them. **Reset** returns to the saved default.

**Edit layout** (admins) lets you drag switches, APs, gateways, wired and manual devices. Everything under a device moves with it, so an AP's wireless clients stay with their AP. Links, labels and boxes follow. Positions are saved in Home Assistant and shared by every dashboard. **Snap to grid** (on by default) aligns devices to a dot grid shown while editing; hold **Alt** while dragging to place a device freely. **Reset layout** returns to the automatic layout. The choice is remembered per browser, and boxes resize to fit.

## Device identification

Every client, including ones the integration has never seen before, is identified live from:

| Source | Gives |
|---|---|
| IEEE MAC vendor registry (downloaded once, refreshed monthly) | Manufacturer from the MAC prefix |
| Hostname patterns | Type and brand (`iPhone`, `SonosZP`, `P110M`, `NIBE-…`, `amazon-…`) |
| Cirrus fingerprint | OS / device type |
| Home Assistant device registry | Exact model and integration, when another integration knows the MAC |
| UniFi gateway (optional) | Names you set in UniFi, hostnames and IPs |

Brand logos come from Home Assistant's brand images or Simple Icons. Switch and gateway images and exact OmniSwitch port layouts come from the [NetBox device-type library](https://github.com/netbox-community/devicetype-library) (CC0). All of these are downloaded the first time they are needed, then cached in `/config/ovcirrus_cache`. Your own photos in `/config/www/ovcirrus/<model>.png` always win.

Admins can rename a device or change its type from the map's details panel; this is stored against the MAC.

### New devices

The first run records a baseline. After that, any MAC never seen before:

- fires an `ovcirrus_new_client` event (`mac`, `name`, `vendor`, `ip`, `where`, `type`, `site`),
- gets a **NEW** badge on the map for 24 hours,
- is listed by the *New devices (24h)* sensor.

Phones that rotate private MACs are matched back by distinctive hostnames where possible.

```yaml
automation:
  - alias: Notify on new network device
    triggers:
      - trigger: event
        event_type: ovcirrus_new_client
    actions:
      - action: notify.mobile_app_phone
        data:
          title: New device on the network
          message: "{{ trigger.event.data.name }} ({{ trigger.event.data.vendor or 'unknown vendor' }}), {{ trigger.event.data.ip }}, {{ trigger.event.data.where }}"
```

## Servers & manual devices

**From the map (admins):** click **+ Add device**. Enter a name, an IP and/or MAC, a type and, optionally, the switch and port. Without a port, the device is placed from the switch MAC table (or ARP / UniFi). Choose how to discover its services:

- **Quick scan:** about 40 common service ports (SSH, HTTP/S, SMB, NFS, RDP, VNC, Proxmox, Synology, Plex, Jellyfin, Home Assistant, MQTT, databases, printers, RTSP…), with banners, page titles and TLS certificate names.
- **Full scan:** all 65,535 TCP ports. It uses **nmap** with service and version detection when nmap is available to Home Assistant, otherwise a built-in TCP connect scan that then identifies each open port. It can take a few minutes.

Any wired device the map already shows (from the MAC table or LLDP) has **Add to map as a device** in its details, pre-filled with its name, IP, MAC and port. Added devices have **Edit device** / **Remove from map**, plus **Quick scan** / **Full scan** buttons.

**Unknown ports:** open ports that nothing recognises are flagged *unknown*. Click **Name this port** to say what runs there, either for that device only or for that port on every device. Your names replace "Unknown service" in all future scans, and **rename** / **Clear name** change or remove them.

Devices can also be added under **Configure** → **Servers & manual devices**. Scans repeat every 24 h by default (**Configure** → **General**). Only private / link-local addresses can be scanned, and some intrusion-detection systems will notice a scan.

### Installing nmap

Full scans use nmap when Home Assistant can find it. The integration does not install system packages itself, because Home Assistant's container is replaced on every update. If a full scan is requested and nmap is missing, a notice appears under **Settings → Repairs**, and the scan uses the built-in scanner instead.

- **Home Assistant OS / Supervised / Container:** nmap normally ships in the Home Assistant image. If it is missing, update Home Assistant. For a custom container image, add `nmap` (e.g. `apk add nmap`).
- **Home Assistant Core (Python venv):** `sudo apt install nmap` (or your distro's equivalent), then restart Home Assistant.

## Speed test & WAN

- **Home Assistant Speedtest.net integration:** `sensor.*speedtest*_download / _upload / _ping` are picked up automatically by the card. Set `speedtest: {download: sensor.x, upload: sensor.y, ping: sensor.z}` in the card to choose others.
- **UniFi gateway** (Options → *UniFi gateway*, local read-only account): the gateway's last speed test, WAN status, WAN IP and ISP. Adds sensors (WAN download/upload/latency, last speed test, WAN IP, ISP) and an *Internet* connectivity binary sensor.

The newest result is shown on the Internet → gateway link, and both appear in the Internet node's details.

## Backup & restore

Admins get **Backup & restore** in the map's **View** menu. **Download backup** saves one JSON file with the
integration's options, site selection, manual devices and scan results, device names and types, the map layout,
port names, the saved view and your own device images from `/config/www/ovcirrus` (up to 2 MB). Credentials (Cirrus
app ID / secret, UniFi password) are never included.

**Restore from file…** replaces those settings with the backup's, keeps the credentials already set up here, and
reloads the integration. To move to a new Home Assistant, add the integration with your credentials first, then restore.

## Installation

1. HACS → Integrations → ⋮ → *Custom repositories* → add `https://github.com/daveo2k/home-network-cirrus-plugin` (category *Integration*), install, restart HA.
   Or copy `custom_components/ovcirrus` into `/config/custom_components/` and restart.
2. Settings → Devices & services → *Add integration* → **OmniVista Cirrus**.
3. Choose your instance (default `uk.manage.ovcirrus.com`) and enter an **Application ID and secret**. Create these in Cirrus under your user profile → *Applications*. The application only sees the organizations its user can see.
4. Pick the organization and sites.
5. Optionally enable **Switch MAC tables (SSH)**.

### SSH / MAC tables

- Authentication is key-based only. Put the private key somewhere the **Home Assistant container** can read, e.g. `/config/.ssh/id_ed25519`. Keys inside the *Terminal & SSH* add-on are not visible to HA Core.
- For **SSH certificates**, place the signed certificate next to the key as `<key>-cert.pub` (e.g. `/config/.ssh/id_ed25519-cert.pub`); it is loaded automatically.
- The account only needs read access (`show` commands).
- Each switch gets its own **SSH address** field. It lists every address Cirrus knows for the switch: the device IP, LLDP management IPs, and every IPv4 VLAN interface that is up (labelled with VLAN id and name). Addresses that answer on the SSH port from Home Assistant come first, and one on Home Assistant's own subnet is pre-selected. Type any IP or hostname HA can reach, or clear the field to skip that switch. Every switch with an address is tested before saving, and the exact SSH error is shown if one fails.
- *known_hosts*: leave blank to skip host-key checking (LAN use), or point at a known_hosts file to verify.

### Presence tracking

Options → *Tracked clients* lets you pick which wireless clients get a `device_tracker`. Phones using private (randomised) MACs keep a stable MAC per SSID, but it changes if the user resets it.

## Notes

- Cirrus is polled every 60 s by default (Options → *Polling*). Topology and LLDP refresh every 5 minutes, and MAC tables every 120 s by default.
- Cirrus requires `X-Requested-With`, `Origin` and `Referer` headers on API calls. The client sends them.
- The Cirrus *wired/UNP sessions* API only lists clients that authenticate through UNP, which is why wired clients come from switch MAC tables instead.
- Diagnostics (Settings → device → *Download diagnostics*) redact the application secret and user names.

## Releases

Versions follow `MAJOR.MINOR.PATCH`. HACS shows the version from GitHub releases and offers updates when a new one is published. To publish one:

```bash
scripts/release.sh            # next patch version, e.g. 0.2.1 -> 0.2.2
scripts/release.sh minor      # 0.2.1 -> 0.3.0
scripts/release.sh 0.4.0      # a specific version
```

The script updates `manifest.json` and the card's cache-busting version, then commits, tags, pushes and creates the GitHub release with generated notes. It needs the GitHub CLI (`gh`) logged in.

## Licence

MIT. See [LICENSE](LICENSE).
