#!/usr/bin/env python3
"""Add HQ street address, ZIP, and phone to data/companies.js(.json) from SEC EDGAR.

Input:  the EDGAR bulk submissions archive (submissions.zip, ~1.3GB), downloaded from
        https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip
Usage:  python3 scripts/enrich_contacts.py /path/to/submissions.zip

Each CIK##########.json in the archive holds the filer's tickers, business address,
and phone in its first few KB (the giant "filings" arrays come after), so we only
decompress the head of each entry instead of parsing ~1M full documents.

Rerunnable: run again after the Dell pipeline regenerates companies.js to re-apply.
"""
import io
import json
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HEAD_BYTES = 16384

RX_TICKERS = re.compile(r'"tickers"\s*:\s*\[([^\]]*)\]')
RX_PHONE = re.compile(r'"phone"\s*:\s*"([^"]*)"')
RX_BUSINESS = re.compile(r'"business"\s*:\s*\{([^}]*)\}')
RX_FIELD = {
    "street1": re.compile(r'"street1"\s*:\s*(?:"([^"]*)"|null)'),
    "street2": re.compile(r'"street2"\s*:\s*(?:"([^"]*)"|null)'),
    "zip": re.compile(r'"zipCode"\s*:\s*(?:"([^"]*)"|null)'),
}


def main(zip_path):
    companies = json.loads((REPO / "data" / "companies.json").read_text())
    wanted = {c["ticker"].upper() for c in companies if c.get("ticker")}
    print(f"{len(companies)} companies, {len(wanted)} unique tickers wanted")

    found = {}  # ticker -> {"addr":…, "zip":…, "phone":…}
    scanned = 0
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not name.startswith("CIK") or "-submissions-" in name:
                continue
            scanned += 1
            if scanned % 100000 == 0:
                print(f"scanned {scanned} filers, matched {len(found)} tickers", flush=True)
            with z.open(name) as f:
                head = f.read(HEAD_BYTES).decode("utf-8", "replace")
            m = RX_TICKERS.search(head)
            if not m or not m.group(1).strip():
                continue
            tickers = [t.strip().strip('"').upper() for t in m.group(1).split(",")]
            hits = [t for t in tickers if t in wanted and t not in found]
            if not hits:
                continue
            contact = {}
            bm = RX_BUSINESS.search(head)
            if bm:
                block = bm.group(1)
                street1 = (RX_FIELD["street1"].search(block) or [None, None])[1]
                street2 = (RX_FIELD["street2"].search(block) or [None, None])[1]
                zc = (RX_FIELD["zip"].search(block) or [None, None])[1]
                addr = ", ".join(s for s in (street1, street2) if s)
                if addr:
                    contact["addr"] = addr
                if zc:
                    contact["zip"] = zc
            pm = RX_PHONE.search(head)
            if pm and pm.group(1).strip():
                contact["phone"] = pm.group(1).strip()
            if not contact:
                continue
            for t in hits:
                found[t] = contact

    n_addr = n_phone = 0
    for c in companies:
        info = found.get((c.get("ticker") or "").upper())
        if not info:
            continue
        if "addr" in info:
            c["addr"] = info["addr"]
            n_addr += 1
        if "zip" in info:
            c["zip"] = info["zip"]
        if "phone" in info:
            c["phone"] = info["phone"]
            n_phone += 1

    print(f"scanned {scanned} filers total")
    print(f"coverage: {n_addr}/{len(companies)} addresses, {n_phone}/{len(companies)} phones")

    (REPO / "data" / "companies.json").write_text(json.dumps(companies))
    (REPO / "data" / "companies.js").write_text(
        "window.COMPANIES = " + json.dumps(companies) + ";\n")
    print("wrote data/companies.json and data/companies.js")


if __name__ == "__main__":
    main(sys.argv[1])
