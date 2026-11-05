# OmniVista Cirrus for Home Assistant

Brings Alcatel-Lucent Enterprise **OmniVista Cirrus** data into Home Assistant, with an automatically drawn **network map** card.

## What you get

| Where | Entities / data |
|---|---|
| Each AP / switch (HA device) | Online (connectivity), uptime, firmware, IP; APs also get client count (with per-SSID / per-band attributes) and channel |
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
font_scale: 1             # map text size multiplier (0.7-1.8); viewers can also use A− / A+
show_vlans: true          # VLAN legend above the map
```

Click a node for details, use **+ / −** on a node to expand or collapse it, and the zoom buttons (or **fit**) to size the map. **A− / A+** change the size of the text on the map (not the header, VLAN bar or details panel). The choice is remembered per browser, and boxes resize to fit.

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

Options → *Servers & manual devices* → *Add a device*: name, IP and/or MAC, type, and optionally the switch and port. Without a port, the device is located from the switch MAC table (or ARP / UniFi). Manual devices appear in their own group on the map.

**Service discovery** (optional per device) runs a quick TCP check of about 40 common ports (SSH, HTTP/S, SMB, NFS, RDP, VNC, Proxmox, Synology, Plex, Jellyfin, Home Assistant, MQTT, databases, printers, RTSP…). It reads banners, web page titles and TLS certificate names, and guesses the role and OS. It runs on enable, then every 24 h by default (Options → *Polling*). Admins can also press **Scan services** on any device with an IP in the details panel. Only private / link-local addresses can be scanned. Some intrusion-detection systems will notice a scan.

## Speed test & WAN

- **Home Assistant Speedtest.net integration:** `sensor.*speedtest*_download / _upload / _ping` are picked up automatically by the card. Set `speedtest: {download: sensor.x, upload: sensor.y, ping: sensor.z}` in the card to choose others.
- **UniFi gateway** (Options → *UniFi gateway*, local read-only account): the gateway's last speed test, WAN status, WAN IP and ISP. Adds sensors (WAN download/upload/latency, last speed test, WAN IP, ISP) and an *Internet* connectivity binary sensor.

The newest result is shown on the Internet → gateway link, and both appear in the Internet node's details.

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
- Switch addresses default to the IP Cirrus reports. Override per switch with `SERIAL=host`, one per line.
- *known_hosts*: leave blank to skip host-key checking (LAN use), or point at a known_hosts file to verify.

### Presence tracking

Options → *Tracked clients* lets you pick which wireless clients get a `device_tracker`. Phones using private (randomised) MACs keep a stable MAC per SSID, but it changes if the user resets it.

## Notes

- Cirrus is polled every 60 s by default (Options → *Polling*). Topology and LLDP refresh every 5 minutes, and MAC tables every 120 s by default.
- Cirrus requires `X-Requested-With`, `Origin` and `Referer` headers on API calls. The client sends them.
- The Cirrus *wired/UNP sessions* API only lists clients that authenticate through UNP, which is why wired clients come from switch MAC tables instead.
- Diagnostics (Settings → device → *Download diagnostics*) redact the application secret and user names.

## Licence

MIT. See [LICENSE](LICENSE).
