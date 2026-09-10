#!/usr/bin/env python3
"""
Axis Mutual Fund — download the **consolidated** monthly portfolio workbook for given YYYY-MM months
(one file per month: all funds in a single spreadsheet, e.g. `Monthly Portfolio-31 01 26.xlsx`).

Uses the official CMS JSON API (same category as the statutory disclosures page).
Use `--all-schemes` only if you need every per-fund file again.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urljoin
from urllib.request import Request, urlopen

API_URL = (
    "https://www.axismf.com/cms/api/statutory-disclosures-scheme"
    "?cat=Monthly%20Scheme%20Portfolios"
)
BASE = "https://www.axismf.com/"
REFERER = "https://www.axismf.com/statutory-disclosures"

MONTH_NAMES = {
    "01": "January",
    "02": "February",
    "03": "March",
    "04": "April",
    "05": "May",
    "06": "June",
    "07": "July",
    "08": "August",
    "09": "September",
    "10": "October",
    "11": "November",
    "12": "December",
}

FILE_EXT_RE = re.compile(r"\.(pdf|xls|xlsx|xlsb)(\?.*)?$", re.I)

# Combined workbook title/filename. Per-scheme rows use "Monthly Portfolio - Axis …".
CONSOLIDATED_TITLE_LEGACY_RE = re.compile(
    r"(?i)^Monthly\s+Portfolio-(\d{1,2})\s+(\d{2})\s+(\d{2})$"
)
CONSOLIDATED_TITLE_ISO_RE = re.compile(
    r"(?i)^Monthly\s+Portfolio\s+(\d{1,2})-(\d{2})-(20\d{2})$"
)


def month_key_to_api_month(month_key: str) -> str:
    """2026-01 -> January"""
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    y, m = parts[0], parts[1]
    if y != "2026" and not y.isdigit():
        pass
    name = MONTH_NAMES.get(m.zfill(2))
    if not name:
        raise ValueError(f"Bad month in {month_key!r}")
    return name


def safe_filename(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    if not base or base in (".", ".."):
        base = "download.bin"
    # filesystem-safe
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.bin"


def load_api_rows() -> list[dict]:
    req = Request(
        API_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,application/xml;q=0.9,*/*;q=0.8",
            "Referer": REFERER,
        },
    )
    with urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise RuntimeError("Unexpected API shape")
    return data


def _strip_extension(name: str) -> str:
    return re.sub(r"\.(pdf|xls|xlsx|xlsb)$", "", name.strip(), flags=re.I)


def is_consolidated_monthly_workbook(
    row: dict, calendar_year: str, api_month: str, month_mm: str
) -> bool:
    """Single combined file: Monthly Portfolio-DD MM YY (MM/YY must match requested month/year)."""
    if str(row.get("field_year", "")).strip() != calendar_year:
        return False
    if str(row.get("field_months", "")).strip() != api_month:
        return False
    rel = str(row.get("field_related_file", "")).strip()
    if not rel or "weekly" in rel.lower():
        return False
    if not FILE_EXT_RE.search(rel):
        return False

    title = str(row.get("field_pdf_name_statutory", "")).strip()
    base = unquote(rel.rsplit("/", 1)[-1].split("?")[0])
    base_core = _strip_extension(base)

    want_mm = month_mm.zfill(2)
    want_yy = str(int(calendar_year, 10) % 100).zfill(2)

    for candidate in (title, base_core):
        if not candidate:
            continue
        cand = candidate.strip()
        m = CONSOLIDATED_TITLE_LEGACY_RE.match(cand) or CONSOLIDATED_TITLE_ISO_RE.match(
            cand
        )
        if not m:
            continue
        if CONSOLIDATED_TITLE_ISO_RE.match(cand):
            file_mm, file_yy = m.group(2), str(int(m.group(3)) % 100).zfill(2)
        else:
            file_mm, file_yy = m.group(2), m.group(3)
        if file_mm == want_mm and file_yy == want_yy:
            return True
    return False


def is_monthly_holdings_row(row: dict, calendar_year: str, api_month: str) -> bool:
    """Every per-scheme monthly portfolio row (legacy / --all-schemes)."""
    if str(row.get("field_year", "")).strip() != calendar_year:
        return False
    if str(row.get("field_months", "")).strip() != api_month:
        return False
    rel = str(row.get("field_related_file", "")).strip()
    title = str(row.get("field_pdf_name_statutory", "")).strip()
    blob = f"{rel} {title}".lower()
    if "weekly" in blob:
        return False
    if "monthly" not in rel.lower() and "monthly" not in title.lower():
        return False
    if not FILE_EXT_RE.search(rel):
        return False
    return True


def download(url: str) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
            "Referer": REFERER,
        },
    )
    with urlopen(req, timeout=90) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Axis monthly portfolio files")
    parser.add_argument(
        "--months",
        nargs="+",
        default=["2026-01", "2026-02"],
        help="Months as YYYY-MM (default: 2026-01 2026-02)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="mf-monthly-holdings root",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max files per month (0 = no limit)")
    parser.add_argument(
        "--all-schemes",
        action="store_true",
        help="Download every per-scheme file instead of only the consolidated Monthly Portfolio-DD MM YY workbook",
    )
    args = parser.parse_args()

    amc_dir = args.root / "amcs" / "axis-mutual-fund"
    rows = load_api_rows()

    for month_key in args.months:
        api_month = month_key_to_api_month(month_key)
        year = month_key.split("-")[0]
        month_mm = month_key.split("-")[1].zfill(2)
        if args.all_schemes:
            selected = [r for r in rows if is_monthly_holdings_row(r, year, api_month)]
        else:
            selected = [
                r
                for r in rows
                if is_consolidated_monthly_workbook(r, year, api_month, month_mm)
            ]
            if len(selected) > 1:
                print(
                    f"  Warning: multiple consolidated rows for {month_key}; using the first only."
                )
                selected = selected[:1]
        if args.limit > 0:
            selected = selected[: args.limit]

        out_dir = amc_dir / month_key
        out_dir.mkdir(parents=True, exist_ok=True)

        manifest: list[dict] = []
        mode = "all schemes" if args.all_schemes else "consolidated workbook only"
        print(f"\n{month_key} ({api_month} {year}) [{mode}]: {len(selected)} file(s)")
        if not selected and not args.all_schemes:
            print(
                "  No consolidated file matched (expected title like 'Monthly Portfolio-DD MM YY'). "
                "Try --all-schemes if the site changed naming."
            )

        for i, row in enumerate(selected, 1):
            rel = str(row.get("field_related_file", "")).strip().replace("\\/", "/")
            file_url = urljoin(BASE, rel)
            fname = safe_filename(rel)
            dest = out_dir / fname

            rec = {
                "month": month_key,
                "download_url": file_url,
                "saved_as": fname,
                "field_aboutus_scheme_code": row.get("field_aboutus_scheme_code"),
                "field_pdf_name_statutory": row.get("field_pdf_name_statutory"),
                "field_months": row.get("field_months"),
                "field_year": row.get("field_year"),
            }

            if args.dry_run:
                print(f"  [{i}] {fname}")
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue

            try:
                body = download(file_url)
                h = hashlib.sha256(body).hexdigest()
                dest.write_bytes(body)
                manifest.append({**rec, "sha256": h})
                print(f"  [{i}] OK {fname} ({len(body)} bytes)")
            except Exception as e:
                manifest.append({**rec, "sha256": "", "error": str(e)})
                print(f"  [{i}] ERR {fname}: {e}")

        man_path = out_dir / "manifest.json"
        man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Wrote {man_path}")


if __name__ == "__main__":
    main()
