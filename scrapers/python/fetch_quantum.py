#!/usr/bin/env python3
"""
Quantum Mutual Fund — download **monthly combined** portfolio `.xlsx` for given YYYY-MM.

Source (server-rendered HTML):
  https://www.quantumamc.com/portfolio/combined/-1/1/0/0

Each row’s `<a>` uses an `onclick` like:
  GTMcodeforxml('<xlsx url>', '...', 'February 2026 - All Funds', 'All Funds Portfolio');

We match labels of the form `<Month> <YYYY> - All Funds` and download the corresponding file.

Note: Quantum is a small AMC; this page lists **one combined workbook per month** (all funds),
not separate files per scheme.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from disclosure_date import extract_dates, extract_year_month

BASE = "https://www.quantumamc.com"
LISTING_URL = f"{BASE}/portfolio/combined/-1/1/0/0"
LISTING_URL_FORTNIGHTLY = f"{BASE}/portfolio/combined/-1/3/0/0"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
}

MONTH_NAMES_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# onclick="GTMcodeforxml('https://...uuid.xlsx', '...', 'January 2026 - All Funds', 'All Funds Portfolio');"
GTM_RE = re.compile(
    r"GTMcodeforxml\(\s*'(https://www\.quantumamc\.com/FileCDN/FactSheet/[a-f0-9\-]+\.xlsx)'\s*,"
    r"\s*'[^']*'\s*,\s*'([^']+)'\s*,\s*'All Funds Portfolio'\s*\)",
    re.I,
)

LABEL_MONTH_RE = re.compile(
    r"^([A-Za-z]+)\s+(\d{4})\s*-\s*All\s+Funds\s*$",
    re.I,
)
LABEL_FORTNIGHTLY_RE = re.compile(
    r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s*-\s*All\s+Funds\s*$",
    re.I,
)


def _ssl_context(insecure: bool) -> ssl.SSLContext:
    if insecure:
        return ssl._create_unverified_context()
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def month_key_to_parts(month_key: str) -> tuple[int, int]:
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    y, m = int(parts[0]), int(parts[1].zfill(2))
    if not (1 <= m <= 12):
        raise ValueError(f"Bad month in {month_key!r}")
    return y, m


def label_to_year_month(label: str) -> tuple[int, int] | None:
    return extract_year_month(label)


def label_to_as_of(label: str) -> tuple[int, int, int] | None:
    dates = extract_dates(label)
    if not dates:
        return None
    d = dates[0]
    return d.year, d.month, d.day


def parse_as_of(as_of: str) -> tuple[int, int, int] | None:
    parts = as_of.strip().split("-")
    if len(parts) != 3:
        return None
    try:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    return y, m, d


def fetch_listing_html(*, ctx: ssl.SSLContext, url: str = LISTING_URL) -> str:
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def parse_all_funds_rows(html: str, *, fortnightly: bool = False) -> dict[tuple, tuple[str, str]]:
    """Map key -> (url, label). Monthly: (year, month); fortnightly: (year, month, day)."""
    out: dict[tuple, tuple[str, str]] = {}
    for url, label in GTM_RE.findall(html):
        if fortnightly:
            key = label_to_as_of(label)
        else:
            key = label_to_year_month(label)
        if key is None:
            continue
        out[key] = (url, label.strip())
    return out


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = unquote(path.rsplit("/", 1)[-1].split("?")[0])
    if base.lower().endswith(".xlsx"):
        return re.sub(r"[^\w.\-]+", "_", base).strip("._")[:120] or "quantum_portfolio.xlsx"
    return "quantum_portfolio.xlsx"


def download(url: str, *, ctx: ssl.SSLContext) -> bytes:
    req = urllib.request.Request(
        url,
        headers={**HEADERS, "Accept": "*/*", "Referer": LISTING_URL},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Quantum MF combined monthly portfolio xlsx",
    )
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument(
        "--insecure-ssl",
        action="store_true",
        help="Disable TLS verification if your Python lacks CA certs",
    )
    parser.add_argument("--fortnightly", action="store_true", help="Fetch fortnightly debt portfolios when supported")
    parser.add_argument(
        "--as-of",
        dest="as_of",
        default="",
        help="Calendar as-of YYYY-MM-DD (fortnightly slice)",
    )
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "quantum-mutual-fund"

    listing_url = LISTING_URL_FORTNIGHTLY if args.fortnightly else LISTING_URL
    print(f"GET {listing_url} …", flush=True)
    try:
        html = fetch_listing_html(ctx=ctx, url=listing_url)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_quantum.py ... --insecure-ssl"
            ) from e
        raise
    index = parse_all_funds_rows(html, fortnightly=args.fortnightly)
    print(f"  Parsed {len(index)} row(s) (All Funds)", flush=True)

    as_of_target = parse_as_of(args.as_of) if args.as_of else None
    if args.fortnightly and not as_of_target and args.months:
        as_of_target = parse_as_of(f"{args.months[0]}-15")

    for mk in args.months:
        y, mon = month_key_to_parts(mk)
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in out_dir.iterdir():
            if p.is_file():
                p.unlink()
        row = None
        if args.fortnightly and as_of_target:
            row = index.get(as_of_target)
        elif args.fortnightly:
            # Best mid-month slice for the calendar month.
            for day in (15, 14, 16, 13, 17):
                row = index.get((y, mon, day))
                if row:
                    break
        else:
            row = index.get((y, mon))
        manifest: list[dict] = []
        print(f"\n{mk}:", flush=True)
        if not row:
            print(f"  No 'All Funds' row for this month (check live site).", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue
        url, label = row
        fn = safe_filename(url)
        rec = {
            "month": mk,
            "label": label,
            "kind": "combined_all_funds",
            "download_url": url,
            "saved_as": fn,
        }
        try:
            body = download(url, ctx=ctx)
            h = hashlib.sha256(body).hexdigest()
            (out_dir / fn).write_bytes(body)
            manifest.append({**rec, "sha256": h})
            print(f"  OK {fn} ({len(body)} bytes)", flush=True)
        except Exception as e:
            manifest.append({**rec, "sha256": "", "error": str(e)})
            print(f"  ERR {fn}: {e}", flush=True)

        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )
        print(f"  Wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
