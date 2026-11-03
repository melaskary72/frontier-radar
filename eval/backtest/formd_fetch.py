"""Fetch every SEC Form D filing from 2024 Q1 to today, for the Form D outcome backtest.

Two public SEC sources, both bulk files (no per-company lookups, so no query can name a
company):
- Form D structured data sets (quarterly zips, issuer name, CIK, filing date, amendment flag),
  2024 Q1 through the latest published quarter.
- EDGAR full-index form.idx for later quarters (form type D or D/A, company name, CIK, date).

Raw files are cached in data/cache/formd/ with fetched_at and SHA-256 in manifest.json, the same
provenance rule as raw_snapshots. The SEC contact comes from SEC_USER_AGENT in .env. Requests are
paced well under SEC's 10 per second.

Writes data/cache/formd/filings.csv: cik, entity_name, filed_on, is_amendment, source.
Run: uv run python eval/backtest/formd_fetch.py
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sys
import time
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

import radar.config  # noqa: E402,F401  (loads .env)

CACHE = ROOT / "data" / "cache" / "formd"
MANIFEST = CACHE / "manifest.json"
DATASETS = "https://www.sec.gov/files/structureddata/data/form-d-data-sets/{q}_d.zip"
FULL_INDEX = "https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{n}/form.idx"
START = (2024, 1)


def quarters(until: date) -> list[tuple[int, int]]:
    out, (y, n) = [], START
    while (y, n) <= (until.year, (until.month - 1) // 3 + 1):
        out.append((y, n))
        y, n = (y + 1, 1) if n == 4 else (y, n + 1)
    return out


def fetch(client: httpx.Client, url: str, name: str, manifest: dict, *, refresh: bool) -> bytes | None:
    path = CACHE / name
    if path.exists() and not refresh:
        return path.read_bytes()
    time.sleep(0.5)
    r = client.get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    path.write_bytes(r.content)
    manifest[name] = {"url": url, "fetched_at": datetime.now(UTC).isoformat(),
                      "sha256": hashlib.sha256(r.content).hexdigest(), "bytes": len(r.content)}
    MANIFEST.write_text(json.dumps(manifest, indent=1))
    return r.content


def parse_dataset(raw: bytes) -> list[dict[str, str]]:
    z = zipfile.ZipFile(io.BytesIO(raw))
    names = {n.split("/")[-1].upper(): n for n in z.namelist()}

    def table(t: str) -> list[dict[str, str]]:
        with z.open(names[t]) as f:
            return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"), delimiter="\t"))

    subs = {r["ACCESSIONNUMBER"]: r for r in table("FORMDSUBMISSION.TSV")}
    out = []
    for r in table("ISSUERS.TSV"):
        if r.get("IS_PRIMARYISSUER_FLAG", "YES").upper() != "YES":
            continue
        s = subs.get(r["ACCESSIONNUMBER"])
        if not s:
            continue
        filed = datetime.strptime(s["FILING_DATE"][:11], "%d-%b-%Y").date().isoformat()
        out.append({"cik": r["CIK"], "entity_name": r["ENTITYNAME"], "filed_on": filed,
                    "is_amendment": "1" if s["SUBMISSIONTYPE"].upper() == "D/A" else "0", "source": "dataset"})
    return out


def parse_form_idx(raw: bytes) -> list[dict[str, str]]:
    out = []
    for line in raw.decode("latin-1").splitlines():
        form = line[:12].strip()
        if form not in ("D", "D/A"):
            continue
        parts = line[12:].rstrip().rsplit(None, 3)  # company name, CIK, date, file name
        if len(parts) != 4:
            continue
        name, cik, filed, _ = parts
        out.append({"cik": cik, "entity_name": name.strip(), "filed_on": filed,
                    "is_amendment": "1" if form == "D/A" else "0", "source": "full-index"})
    return out


def main() -> None:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        sys.exit("SEC_USER_AGENT is not set in .env")
    CACHE.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    today = date.today()
    current = (today.year, (today.month - 1) // 3 + 1)
    rows: list[dict[str, str]] = []
    with httpx.Client(headers={"User-Agent": ua}, timeout=120, follow_redirects=True) as client:
        for y, n in quarters(today):
            raw = fetch(client, DATASETS.format(q=f"{y}q{n}"), f"{y}q{n}_d.zip", manifest, refresh=False)
            if raw is not None:
                got = parse_dataset(raw)
            else:  # data set not published yet: use the filing index (refetch the open quarter)
                raw = fetch(client, FULL_INDEX.format(y=y, n=n), f"{y}QTR{n}_form.idx", manifest,
                            refresh=(y, n) == current)
                got = parse_form_idx(raw) if raw is not None else []
            print(f"{y} Q{n}: {len(got)} Form D filings ({got[0]['source'] if got else 'none'})")
            rows += got
    with open(CACHE / "filings.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["cik", "entity_name", "filed_on", "is_amendment", "source"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} filings, latest {max(r['filed_on'] for r in rows)}")


if __name__ == "__main__":
    main()
