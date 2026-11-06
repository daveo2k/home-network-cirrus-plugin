"""Light-touch service discovery for a single host the user chose.

A TCP connect check of common service ports, plus a banner / HTTP title / TLS certificate
read on the ports that answer. No exploitation, no UDP sweep, no credential use. Only run
against devices the user has enabled discovery for, or asked to scan from the map.
"""

from __future__ import annotations

import asyncio
import logging
import re
import ssl
import time

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 1.2
READ_TIMEOUT = 2.0
CONCURRENCY = 24

# port: (service, is_tls_http, is_plain_http)
PORTS: dict[int, tuple[str, bool, bool]] = {
    21: ("FTP", False, False), 22: ("SSH", False, False), 23: ("Telnet", False, False),
    25: ("SMTP", False, False), 53: ("DNS", False, False), 80: ("HTTP", False, True),
    111: ("RPC portmapper", False, False), 139: ("NetBIOS", False, False), 443: ("HTTPS", True, False),
    445: ("SMB file sharing", False, False), 548: ("AFP file sharing", False, False),
    554: ("RTSP video", False, False), 631: ("IPP printing", False, True), 853: ("DNS over TLS", False, False),
    1400: ("Sonos", False, True), 1883: ("MQTT", False, False), 2049: ("NFS", False, False),
    2375: ("Docker API", False, True), 3000: ("Web app (Grafana?)", False, True),
    3306: ("MySQL / MariaDB", False, False), 3389: ("Remote Desktop", False, False),
    5000: ("Web app (Synology DSM?)", False, True), 5001: ("Web app TLS (Synology DSM?)", True, False),
    5432: ("PostgreSQL", False, False), 5900: ("VNC", False, False), 6379: ("Redis", False, False),
    6443: ("Kubernetes API", True, False), 7878: ("Radarr", False, True), 8000: ("Web app", False, True),
    8006: ("Proxmox VE", True, False), 8080: ("Web app", False, True), 8081: ("Web app", False, True),
    8096: ("Jellyfin / Emby", False, True), 8123: ("Home Assistant", False, True),
    8443: ("Web app TLS", True, False), 8883: ("MQTT over TLS", False, False), 8888: ("Web app", False, True),
    8989: ("Sonarr", False, True), 9000: ("Web app (Portainer?)", False, True), 9090: ("Web app (Cockpit?)", True, False),
    9100: ("Printer (RAW)", False, False), 9443: ("Web app TLS (Portainer?)", True, False),
    10000: ("Webmin", True, False), 32400: ("Plex", False, True), 51413: ("BitTorrent", False, False),
}

TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)

ROLE_RULES: list[tuple[str, callable]] = [
    ("Proxmox VE host", lambda s, t: 8006 in s or "proxmox" in t),
    ("Home Assistant", lambda s, t: 8123 in s or "home assistant" in t),
    ("Synology NAS", lambda s, t: "synology" in t or (5000 in s and 5001 in s)),
    ("Unraid server", lambda s, t: "unraid" in t),
    ("TrueNAS", lambda s, t: "truenas" in t or "freenas" in t),
    ("Plex media server", lambda s, t: 32400 in s),
    ("Jellyfin / Emby media server", lambda s, t: 8096 in s or "jellyfin" in t),
    ("Docker host", lambda s, t: 2375 in s or 9443 in s or "portainer" in t),
    ("Apple device (AirPlay)", lambda s, t: "airtunes" in t or "airplay" in t),
    ("Windows PC / server", lambda s, t: 3389 in s or ("windows" in t)),
    ("Printer", lambda s, t: 9100 in s or 631 in s),
    ("Camera / NVR", lambda s, t: 554 in s),
    ("NAS / file server", lambda s, t: 445 in s or 2049 in s or 548 in s),
    ("MQTT broker", lambda s, t: 1883 in s),
    ("DNS server (Pi-hole / AdGuard?)", lambda s, t: 53 in s or "pi-hole" in t or "adguard" in t),
    ("Linux server", lambda s, t: 22 in s and any(x in t for x in ("ubuntu", "debian", "raspbian", "linux"))),
    ("Web server", lambda s, t: 80 in s or 443 in s),
]


async def _probe(host: str, port: int, sem: asyncio.Semaphore) -> dict | None:
    service, is_tls, is_http = PORTS[port]
    async with sem:
        ctx = None
        if is_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=ctx), CONNECT_TIMEOUT + (1.5 if is_tls else 0)
            )
        except (OSError, asyncio.TimeoutError, ssl.SSLError):
            return None
        result: dict = {"port": port, "service": service}
        try:
            if is_tls or is_http:
                writer.write(f"GET / HTTP/1.0\r\nHost: {host}\r\nUser-Agent: ovcirrus-ha\r\n\r\n".encode())
                await writer.drain()
                data = await asyncio.wait_for(reader.read(16384), READ_TIMEOUT)
                head, _, body = data.partition(b"\r\n\r\n")
                server = re.search(rb"^Server:\s*(.+)$", head, re.I | re.M)
                title = TITLE_RE.search(body)
                if server:
                    result["server"] = server.group(1).decode("latin-1").strip()[:80]
                if title:
                    result["title"] = re.sub(r"\s+", " ", title.group(1).decode("utf-8", "replace")).strip()[:80]
                result["url"] = f"{'https' if is_tls else 'http'}://{host}{'' if port in (80, 443) else f':{port}'}/"
                if is_tls:
                    cn = _cert_name(writer)
                    if cn:
                        result["certificate"] = cn
            else:
                data = await asyncio.wait_for(reader.read(256), READ_TIMEOUT)
                banner = data.decode("utf-8", "replace").strip().splitlines()
                if banner and banner[0].isprintable():
                    result["banner"] = banner[0][:100]
        except (OSError, asyncio.TimeoutError, ssl.SSLError, UnicodeError):
            pass
        finally:
            writer.close()
        return result


def _cert_name(writer) -> str | None:
    sslobj = writer.get_extra_info("ssl_object")
    if not sslobj:
        return None
    der = sslobj.getpeercert(binary_form=True)
    if not der:
        return None
    try:
        from cryptography import x509  # shipped with Home Assistant
        from cryptography.x509.oid import NameOID

        cert = x509.load_der_x509_certificate(der)
        names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        return names[0].value if names else None
    except Exception:  # noqa: BLE001 - optional detail
        return None


def guess_role(services: list[dict]) -> str | None:
    ports = {s["port"] for s in services}
    text = " ".join(
        str(s.get(k, "")) for s in services for k in ("title", "server", "banner", "certificate")
    ).lower()
    for role, test in ROLE_RULES:
        if test(ports, text):
            return role
    return None


def guess_os(services: list[dict]) -> str | None:
    text = " ".join(str(s.get("banner", "")) + " " + str(s.get("server", "")) for s in services).lower()
    for needle, name in (
        ("ubuntu", "Ubuntu"), ("debian", "Debian"), ("raspbian", "Raspberry Pi OS"), ("freebsd", "FreeBSD"),
        ("microsoft", "Windows"), ("win32", "Windows"), ("openssh", "Linux / Unix"),
    ):
        if needle in text:
            return name
    if any(s["port"] == 3389 for s in services):
        return "Windows"
    return None


async def discover(host: str) -> dict:
    sem = asyncio.Semaphore(CONCURRENCY)
    started = time.time()
    results = await asyncio.gather(*(_probe(host, p, sem) for p in PORTS), return_exceptions=True)
    services = sorted((r for r in results if isinstance(r, dict)), key=lambda r: r["port"])
    return {
        "ip": host,
        "ts": started,
        "duration": round(time.time() - started, 1),
        "mode": "quick",
        "engine": "built-in",
        "services": services,
        "role": guess_role(services),
        "os": guess_os(services),
    }


# ---------------------------------------------------------------- full TCP scan
import shutil  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402

FULL_CONCURRENCY = 600
FULL_TIMEOUT = 0.6
NMAP_TIMEOUT = 900

# Extra well-known ports (beyond PORTS) used to name results of a full scan.
WELL_KNOWN: dict[int, str] = {
    20: "FTP data", 69: "TFTP", 88: "Kerberos", 110: "POP3", 119: "NNTP", 123: "NTP", 135: "MS RPC",
    143: "IMAP", 161: "SNMP", 389: "LDAP", 465: "SMTPS", 500: "IPsec IKE", 514: "Syslog", 515: "LPD printing",
    587: "SMTP submission", 636: "LDAPS", 873: "rsync", 993: "IMAPS", 995: "POP3S", 1080: "SOCKS proxy",
    1194: "OpenVPN", 1433: "MS SQL", 1521: "Oracle DB", 1723: "PPTP", 1900: "UPnP SSDP", 2222: "SSH (alt)",
    3128: "HTTP proxy", 3478: "STUN", 3483: "Squeezebox", 4000: "Web app", 4443: "Web app TLS",
    5060: "SIP", 5222: "XMPP", 5353: "mDNS", 5555: "Android debug (ADB)", 5683: "CoAP", 6052: "ESPHome dashboard",
    6053: "ESPHome API", 6443: "Kubernetes API", 6881: "BitTorrent", 7000: "AirPlay", 7100: "AirPlay",
    7443: "Web app TLS", 8008: "Chromecast", 8009: "Chromecast", 8086: "InfluxDB", 8088: "Web app",
    8200: "Duplicati / Vault", 8443: "Web app TLS (UniFi?)", 8883: "MQTT over TLS", 8880: "UniFi portal",
    9001: "Web app", 9091: "Transmission", 9093: "Alertmanager", 9200: "Elasticsearch", 9999: "Web app",
    10001: "UniFi discovery", 11211: "Memcached", 21064: "HomeKit (HA bridge)", 27017: "MongoDB",
    49152: "UPnP", 50000: "Web app", 62078: "Apple iDevice sync",
}


def port_name(port: int) -> str | None:
    if port in PORTS:
        return PORTS[port][0]
    return WELL_KNOWN.get(port)


def nmap_available() -> bool:
    return shutil.which("nmap") is not None


def parse_nmap_xml(xml_text: str) -> list[dict]:
    services: list[dict] = []
    root = ET.fromstring(xml_text)
    for port in root.iter("port"):
        state = port.find("state")
        if state is None or state.get("state") != "open" or port.get("protocol") != "tcp":
            continue
        num = int(port.get("portid"))
        svc = port.find("service")
        entry: dict = {"port": num}
        name = svc.get("name") if svc is not None else None
        product = " ".join(
            x for x in ((svc.get("product") if svc is not None else None), (svc.get("version") if svc is not None else None)) if x
        )
        tunnel = svc.get("tunnel") if svc is not None else None
        known = port_name(num)
        if name and name not in ("unknown", "tcpwrapped"):
            entry["service"] = known or name
            entry["nmap_service"] = f"{tunnel}/{name}" if tunnel else name
        elif known:
            entry["service"] = known
        if product:
            entry["server"] = product[:100]
        if svc is not None and svc.get("extrainfo"):
            entry["banner"] = svc.get("extrainfo")[:100]
        if (name or "").startswith("http") or tunnel == "ssl" and (name or "").startswith("http"):
            scheme = "https" if tunnel == "ssl" or name == "https" else "http"
            entry["url"] = f"{scheme}://{{host}}{'' if num in (80, 443) else f':{num}'}/"
        entry["unknown"] = "service" not in entry
        if entry["unknown"]:
            entry["service"] = "Unknown service"
        services.append(entry)
    return services


async def _nmap_scan(host: str) -> list[dict]:
    proc = await asyncio.create_subprocess_exec(
        "nmap", "-sT", "-Pn", "-p-", "-T4", "-sV", "--version-light", "--max-retries", "2", "-oX", "-", host,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), NMAP_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise ScanError(f"nmap took longer than {NMAP_TIMEOUT // 60} minutes") from None
    if proc.returncode != 0:
        msg = err.decode(errors="replace").strip().splitlines()
        raise ScanError(f"nmap stopped with code {proc.returncode}: {(msg[-1] if msg else 'no message')[:200]}")
    try:
        services = parse_nmap_xml(out.decode(errors="replace"))
    except ET.ParseError as exc:
        raise ScanError(f"nmap's output could not be read ({exc})") from None
    for s in services:
        if "url" in s:
            s["url"] = s["url"].replace("{host}", host)
    return services


async def _connect_scan(host: str) -> list[int]:
    """Plain TCP connect scan of 1-65535 (no nmap)."""
    sem = asyncio.Semaphore(FULL_CONCURRENCY)
    open_ports: list[int] = []

    async def one(port: int) -> None:
        async with sem:
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), FULL_TIMEOUT)
            except (OSError, asyncio.TimeoutError):
                return
            open_ports.append(port)
            writer.close()

    await asyncio.gather(*(one(p) for p in range(1, 65536)))
    return sorted(open_ports)


async def _identify_port(host: str, port: int, sem: asyncio.Semaphore) -> dict:
    """Name an open port: known list, then banner, then an HTTP request."""
    if port in PORTS:
        result = await _probe(host, port, sem)
        if result:
            return result
    entry: dict = {"port": port}
    async with sem:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            return {"port": port, "service": port_name(port) or "Unknown service", "unknown": port_name(port) is None}
        try:
            data = await asyncio.wait_for(reader.read(256), 1.2)  # servers that speak first (SSH, FTP, SMTP…)
            if not data:
                writer.write(f"GET / HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())
                await writer.drain()
                data = await asyncio.wait_for(reader.read(8192), READ_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            data = b""
        finally:
            writer.close()
    text = data.decode("utf-8", "replace")
    if text.startswith("HTTP/"):
        head, _, body = data.partition(b"\r\n\r\n")
        server = re.search(rb"^Server:\s*(.+)$", head, re.I | re.M)
        title = TITLE_RE.search(body)
        entry["service"] = port_name(port) or "Web server"
        if server:
            entry["server"] = server.group(1).decode("latin-1").strip()[:80]
        if title:
            entry["title"] = re.sub(r"\s+", " ", title.group(1).decode("utf-8", "replace")).strip()[:80]
        entry["url"] = f"http://{host}:{port}/"
    elif text.strip():
        first = text.strip().splitlines()[0]
        if first.isprintable():
            entry["banner"] = first[:100]
        if first.startswith("SSH-"):
            entry["service"] = port_name(port) or "SSH"
    entry.setdefault("service", port_name(port) or "Unknown service")
    entry["unknown"] = entry["service"] == "Unknown service"
    return entry


class ScanError(Exception):
    """A scan could not be completed; the message says why in plain words."""


async def full_scan(host: str) -> dict:
    """Every TCP port. Uses nmap (with version detection) when installed, else a connect scan.

    If nmap fails, the built-in scanner runs instead and the result notes why nmap was not used.
    """
    started = time.time()
    engine = "nmap" if nmap_available() else "built-in"
    note = None
    services: list[dict] = []
    if engine == "nmap":
        try:
            services = await _nmap_scan(host)
        except (ScanError, OSError) as err:
            _LOGGER.warning("Full scan of %s: %s; using the built-in scanner instead", host, err)
            note = f"nmap failed ({err}), so the built-in scanner was used."
            engine = "built-in"
    if engine == "built-in":
        sem = asyncio.Semaphore(CONCURRENCY)
        ports = await _connect_scan(host)
        services = sorted(await asyncio.gather(*(_identify_port(host, p, sem) for p in ports)), key=lambda s: s["port"])
    for s in services:
        s.setdefault("unknown", False)
    return {
        "ip": host,
        "ts": started,
        "duration": round(time.time() - started, 1),
        "mode": "full",
        "engine": engine,
        "note": note,
        "services": services,
        "role": guess_role(services),
        "os": guess_os(services),
    }
