#!/usr/bin/env python3
"""Rebuild custom_components/ovcirrus/iana_ports.json from the IANA port registry.

Run occasionally (the registry changes slowly) and commit the result:
    python3 scripts/update_ports.py
"""
import csv
import io
import json
import pathlib
import urllib.request

URL = "https://www.iana.org/assignments/service-names-port-numbers/service-names-port-numbers.csv"
OUT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ovcirrus" / "iana_ports.json"

with urllib.request.urlopen(URL, timeout=60) as resp:
    text = resp.read().decode("utf-8", "replace")

ports: dict[str, str] = {}
for row in csv.DictReader(io.StringIO(text)):
    if row["Transport Protocol"] != "tcp" or not row["Port Number"]:
        continue
    desc = (row["Description"] or row["Service Name"] or "").strip().splitlines()
    desc = desc[0].strip() if desc else ""
    if not desc or desc.lower() in ("reserved", "unassigned") or "IANA assigned this" in desc:
        continue
    first, _, last = row["Port Number"].partition("-")
    for port in range(int(first), int(last or first) + 1):
        ports.setdefault(str(port), desc[:60])

OUT.write_text(json.dumps(ports, separators=(",", ":"), ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")
print(f"{len(ports)} TCP ports written to {OUT}")
