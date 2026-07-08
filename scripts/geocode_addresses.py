#!/usr/bin/env python3
"""Geocode enriched HQ street addresses to rooftop coordinates.

Most pins were originally placed at ZIP-code centroids (loc:"zip"), so companies
sharing a ZIP stack at identical coordinates. This script sends every company
that has a street address (from enrich_contacts.py) to the US Census batch
geocoder (free, no key, built for bulk US addresses) and updates lat/lon to the
true street location, marking loc:"street".

Usage: python3 scripts/geocode_addresses.py
Rerunnable after the data pipeline regenerates companies.js (run enrich_contacts.py first).
"""
import csv
import io
import json
import subprocess
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
CHUNK = 2000


def census_batch(rows):
    """rows: list of (id, street, city, state, zip). Returns {id: (lat, lon)}."""
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in rows:
        w.writerow(r)
    payload = buf.getvalue().encode()
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        f.write(payload)
        tmp = Path(f.name)
    out = {}
    for attempt in range(3):
        p = subprocess.run(
            ["curl", "-s", "--max-time", "600",
             "--form", f"addressFile=@{tmp}",
             "--form", "benchmark=Public_AR_Current", URL],
            capture_output=True)
        text = p.stdout.decode("utf-8", "replace")
        if p.returncode == 0 and text.strip():
            break
        print(f"  chunk attempt {attempt + 1} failed (rc={p.returncode}), retrying…", flush=True)
        time.sleep(10)
    else:
        return out
    for rec in csv.reader(io.StringIO(text)):
        # Match rows: id, input, "Match", "Exact"/"Non_Exact", matched addr, "lon,lat", tiger, side
        if len(rec) >= 6 and rec[2] == "Match" and rec[5]:
            try:
                lon, lat = (float(v) for v in rec[5].split(","))
                out[rec[0]] = (round(lat, 5), round(lon, 5))
            except ValueError:
                pass
    return out


def main():
    companies = json.loads((REPO / "data" / "companies.json").read_text())
    todo = []
    for c in companies:
        if c.get("addr") and c.get("ticker") and c.get("loc") != "street":
            street = c["addr"].split(",")[0].strip()
            if not street or street.upper().startswith("P.O") or street.upper().startswith("PO BOX"):
                continue
            todo.append((c["ticker"], street, c.get("city", ""), c.get("state", ""), c.get("zip", "")))
    print(f"geocoding {len(todo)} street addresses in chunks of {CHUNK}", flush=True)

    coords = {}
    for i in range(0, len(todo), CHUNK):
        chunk = todo[i:i + CHUNK]
        got = census_batch(chunk)
        coords.update(got)
        print(f"chunk {i // CHUNK + 1}/{(len(todo) + CHUNK - 1) // CHUNK}: "
              f"{len(got)}/{len(chunk)} matched (total {len(coords)})", flush=True)

    n = 0
    for c in companies:
        hit = coords.get(c.get("ticker"))
        if hit:
            c["lat"], c["lon"] = hit
            c["loc"] = "street"
            n += 1
    print(f"updated {n}/{len(companies)} companies to rooftop coordinates", flush=True)

    (REPO / "data" / "companies.json").write_text(json.dumps(companies))
    (REPO / "data" / "companies.js").write_text(
        "window.COMPANIES = " + json.dumps(companies) + ";\n")
    print("wrote data/companies.json and data/companies.js", flush=True)


if __name__ == "__main__":
    main()
