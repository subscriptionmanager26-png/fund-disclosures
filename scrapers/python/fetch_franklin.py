#!/usr/bin/env python3
"""
Franklin Templeton Mutual Fund — download monthly portfolio (ISIN) workbook for given YYYY-MM.

Data source:
  GET https://www.franklintempletonindia.com/api/literature/v1/responseLitJson?type=report

From that JSON, monthly portfolio records are under:
  FirstDropDown[id == "MONTHLY-PORTFOLIO-DSCLR"].dataRecords.linkdata

Each row includes fields like:
  - dctermsTitle: "ISIN as on 27 February 2026"
  - literatureHref: "/en-in/monthly-portfolio-dsclr/.../Monthly-Portfolio-ISIN-27-Feb-2026.xlsx"

Download URL pattern (as used by site JS):
  https://www.franklintempletonindia.com/download + literatureHref
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from disclosure_date import extract_dates

API_URL = "https://www.franklintempletonindia.com/api/literature/v1/responseLitJson?type=report"
PAGE_REF = "https://www.franklintempletonindia.com/investor/reports?firstFilter-12"
BASE = "https://www.franklintempletonindia.com"

MONTHLY_CATEGORY_ID = "MONTHLY-PORTFOLIO-DSCLR"
FORTNIGHTLY_CATEGORY_ID = "FORTNIGHT-PORTFOLIO-DEBT-SCHEMES"


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    if not base or base in (".", ".."):
        base = "download.xlsx"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.xlsx"


def parse_title_date(*parts: str) -> tuple[str, str] | None:
    """
    Return (month_key YYYY-MM, full_date YYYY-MM-DD) from title and/or href.
    Example: "ISIN as on 27 February 2026" -> ("2026-02", "2026-02-27")
    Also accepts Fortnightly-Portfolio-ISIN-14-Aug-2026.xlsx.
    """
    dates = extract_dates(*parts)
    if not dates:
        return None
    dt = dates[0]
    return f"{dt.year:04d}-{dt.month:02d}", dt.strftime("%Y-%m-%d")


def fetch_report_json() -> dict:
    req = Request(
        API_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": PAGE_REF,
        },
    )
    with urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        return {}
    return payload


def get_category_rows(payload: dict, category_id: str) -> list[dict]:
    first = payload.get("FirstDropDown")
    if not isinstance(first, list):
        return []
    for item in first:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") != category_id:
            continue
        links = (item.get("dataRecords") or {}).get("linkdata")
        if not isinstance(links, list):
            return []
        return [x for x in links if isinstance(x, dict)]
    return []


def get_monthly_rows(payload: dict) -> list[dict]:
    return get_category_rows(payload, MONTHLY_CATEGORY_ID)


def select_for_months(
    rows: list[dict], month_keys: list[str], *, keep_all: bool = False
) -> dict[str, list[dict]]:
    """
    Pick row(s) per month based on date parsed from dctermsTitle.
    By default keep latest full_date per month; with keep_all retain all matches.
    """
    want = set(month_keys)
    best: dict[str, tuple[str, dict]] = {}
    all_hits: dict[str, list[dict]] = {mk: [] for mk in month_keys}
    seen_href: dict[str, set[str]] = {mk: set() for mk in month_keys}
    for row in rows:
        title = str(row.get("dctermsTitle") or "").strip()
        href = str(row.get("literatureHref") or "")
        parsed = parse_title_date(title, href)
        if not parsed:
            continue
        mk, full_date = parsed
        if mk not in want:
            continue
        href = str(row.get("literatureHref") or "").strip()
        if not href:
            continue
        if keep_all:
            if href in seen_href[mk]:
                continue
            seen_href[mk].add(href)
            all_hits[mk].append(row)
            continue
        prev = best.get(mk)
        if prev is None or full_date > prev[0]:
            best[mk] = (full_date, row)
    if keep_all:
        return all_hits
    return {k: [v[1]] for k, v in best.items()}


def row_to_download_url(row: dict) -> str:
    href = str(row.get("literatureHref") or "").strip()
    if href.startswith("http"):
        return href
    if not href.startswith("/"):
        href = "/" + href
    # Site code prefixes non-http paths with "download"
    return f"{BASE}/download{href}"


def download(url: str) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
            "Referer": PAGE_REF,
        },
    )
    with urlopen(req, timeout=120) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Franklin monthly portfolio xlsx")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="mf-monthly-holdings root",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--fortnightly",
        action="store_true",
        help=f"Use category {FORTNIGHTLY_CATEGORY_ID} instead of monthly",
    )
    args = parser.parse_args()

    amc_dir = args.root / "amcs" / "franklin-templeton-mutual-fund"
    category_id = FORTNIGHTLY_CATEGORY_ID if args.fortnightly else MONTHLY_CATEGORY_ID

    print(f"GET {API_URL} …")
    payload = fetch_report_json()
    rows = get_category_rows(payload, category_id)
    print(f"  … category {category_id} rows: {len(rows)}")

    selected = select_for_months(rows, list(args.months), keep_all=args.fortnightly)

    for month_key in args.months:
        out_dir = amc_dir / month_key
        out_dir.mkdir(parents=True, exist_ok=True)

        month_rows = selected.get(month_key) or []
        print(f"\n{month_key}: {len(month_rows)} file(s)", end=" ")
        if not month_rows:
            print("(no portfolio row matched)")
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue
        print()

        manifest: list[dict] = []
        for row in month_rows:
            file_url = row_to_download_url(row)
            fname = safe_filename(file_url)

            rec = {
                "month": month_key,
                "download_url": file_url,
                "saved_as": fname,
                "title": row.get("dctermsTitle"),
                "frk_reference_date": row.get("frkReferenceDate"),
                "literature_href": row.get("literatureHref"),
                "document_id": row.get("documentId"),
                "category_id": category_id,
            }

            if args.dry_run:
                print(f"  would save {fname}")
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue
            try:
                body = download(file_url)
                h = hashlib.sha256(body).hexdigest()
                (out_dir / fname).write_bytes(body)
                manifest.append({**rec, "sha256": h})
                print(f"  OK {fname} ({len(body)} bytes)")
            except Exception as e:
                manifest.append({**rec, "sha256": "", "error": str(e)})
                print(f"  ERR: {e}")

        man_path = out_dir / "manifest.json"
        man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Wrote {man_path}")


if __name__ == "__main__":
    main()
