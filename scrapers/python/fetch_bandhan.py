#!/usr/bin/env python3
"""
Bandhan Mutual Fund — download portfolio spreadsheets (one per scheme) for given YYYY-MM.

Monthly (default):
  GET …/wp-json/finance-api/v1/posts/monthly-portfolios?page=N&per_page=100
  Titles end with month-end as-on dates (e.g. \"31 July 2026\").

Fortnightly (--fortnightly):
  GET …/wp-json/finance-api/v1/posts/fortnightly?bypass_pagination=true
  Keep mid-month rows only (day == 15), matching
  https://bandhanmutual.com/statutory-disclosures/scheme-portfolios/fortnightly
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
from disclosure_date import dates_match_as_of, extract_dates, year_month_key

CMS = "https://cmsnew.bandhanmutual.com/wp-json/finance-api/v1/posts"
MONTHLY_API = f"{CMS}/monthly-portfolios"
FORTNIGHTLY_API = f"{CMS}/fortnightly"
REFERER_MONTHLY = (
    "https://bandhanmutual.com/statutory-disclosures/scheme-portfolios/monthly-and-half-yearly"
)
REFERER_FORTNIGHTLY = (
    "https://bandhanmutual.com/statutory-disclosures/scheme-portfolios/fortnightly"
)


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    if not base or base in (".", ".."):
        base = "download.bin"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.bin"


def parse_title_date(title: str, url: str = "") -> tuple[str, int] | None:
    """Return (YYYY-MM, day) from title or file URL."""
    dates = extract_dates(title or "", url or "")
    if not dates:
        mk = year_month_key(title or "", url or "")
        if not mk:
            return None
        return mk, 0
    d = dates[0]
    return f"{d.year:04d}-{d.month:02d}", d.day


def _get_json(url: str, referer: str) -> dict | list:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
        },
    )
    with urlopen(req, timeout=180) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    return json.loads(raw)


def fetch_monthly_page(page: int, per_page: int = 100) -> list[dict]:
    url = f"{MONTHLY_API}?page={page}&per_page={per_page}"
    data = _get_json(url, REFERER_MONTHLY)
    if not isinstance(data, dict):
        return []
    rows = data.get("data")
    return rows if isinstance(rows, list) else []


def fetch_all_fortnightly() -> list[dict]:
    url = f"{FORTNIGHTLY_API}?bypass_pagination=true"
    data = _get_json(url, REFERER_FORTNIGHTLY)
    if not isinstance(data, dict):
        return []
    rows = data.get("data")
    return rows if isinstance(rows, list) else []


def file_url_from_row(row: dict) -> str | None:
    acf = row.get("acf_fields") or row.get("acf") or {}
    if not isinstance(acf, dict):
        return None
    files = acf.get("disclosure_files") or acf.get("files") or []
    if not isinstance(files, list) or not files:
        return None
    first = files[0]
    if not isinstance(first, dict):
        return None
    link = first.get("document_link") or {}
    if isinstance(link, dict):
        u = link.get("url")
        if u:
            return str(u).strip()
    if isinstance(first.get("url"), str) and first["url"].strip():
        return first["url"].strip()
    return None


def load_monthly_rows(
    month_keys: list[str],
    *,
    max_pages: int = 0,
    expected_per_month: int = 78,
    no_growth_page_limit: int = 20,
) -> dict[str, list[dict]]:
    per_month: dict[str, list[dict]] = {mk: [] for mk in month_keys}
    seen_ids: set[int] = set()
    page = 1
    no_growth_pages = 0
    while True:
        if max_pages and page > max_pages:
            print(f"  … stopping at --max-pages {max_pages}", flush=True)
            break
        batch = fetch_monthly_page(page)
        if not batch:
            print(f"  … page {page}: empty (end of API)", flush=True)
            break

        before = {mk: len(per_month[mk]) for mk in month_keys}
        for row in batch:
            if not isinstance(row, dict):
                continue
            parsed = parse_title_date(row.get("title") or "", file_url_from_row(row) or "")
            if not parsed or not file_url_from_row(row):
                continue
            mk, _day = parsed
            if mk not in per_month:
                continue
            rid = row.get("id")
            if rid is not None and rid in seen_ids:
                continue
            if rid is not None:
                seen_ids.add(rid)
            per_month[mk].append(row)

        after = {mk: len(per_month[mk]) for mk in month_keys}
        no_growth_pages = no_growth_pages + 1 if after == before else 0
        print(
            f"  … page {page}: batch {len(batch)} | "
            + ", ".join(f"{mk}={len(per_month[mk])}" for mk in month_keys),
            flush=True,
        )

        if expected_per_month > 0 and all(
            len(per_month[mk]) >= expected_per_month for mk in month_keys
        ):
            print("  … reached --expected-per-month — stopping.", flush=True)
            break
        if no_growth_page_limit > 0 and no_growth_pages >= no_growth_page_limit:
            print(
                f"  … no growth for {no_growth_pages} consecutive pages — stopping.",
                flush=True,
            )
            break
        page += 1
        if page > 800:
            raise RuntimeError("Safety stop: exceeded 800 monthly API pages")

    for mk in month_keys:
        per_month[mk].sort(key=lambda r: (r.get("title") or ""))
    return per_month


def load_fortnightly_rows(month_keys: list[str]) -> dict[str, list[dict]]:
    """Mid-month (day 15) fortnightly portfolios for each YYYY-MM."""
    print("  … fetching fortnightly catalog (bypass_pagination)…", flush=True)
    rows = fetch_all_fortnightly()
    print(f"  … catalog rows: {len(rows)}", flush=True)
    per_month: dict[str, list[dict]] = {mk: [] for mk in month_keys}
    seen_ids: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = parse_title_date(row.get("title") or "", file_url_from_row(row) or "")
        if not parsed:
            continue
        mk, _day = parsed
        if mk not in per_month:
            continue
        if not dates_match_as_of(
            row.get("title") or "",
            file_url_from_row(row) or "",
            as_of=f"{mk}-15",
        ):
            continue
        if not file_url_from_row(row):
            continue
        rid = row.get("id")
        if rid is not None and rid in seen_ids:
            continue
        if rid is not None:
            seen_ids.add(rid)
        per_month[mk].append(row)
    for mk in month_keys:
        per_month[mk].sort(key=lambda r: (r.get("title") or ""))
        print(f"  … {mk}: {len(per_month[mk])} mid-month (15th) file(s)", flush=True)
    return per_month


def download(url: str, referer: str) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
            "Referer": referer,
        },
    )
    with urlopen(req, timeout=120) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Bandhan MF monthly or fortnightly portfolio files"
    )
    parser.add_argument("--months", nargs="+", default=["2026-01"], help="YYYY-MM")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Output root (writes amcs/bandhan-mutual-fund/<YYYY-MM>/)",
    )
    parser.add_argument(
        "--fortnightly",
        action="store_true",
        help="Fetch mid-month (15th) fortnightly portfolios instead of monthly",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max files per month (0 = all)")
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument(
        "--expected-per-month",
        type=int,
        default=78,
        help="Monthly pagination stop heuristic (ignored for --fortnightly).",
    )
    parser.add_argument(
        "--no-growth-page-limit",
        type=int,
        default=20,
        help="Monthly pagination: stop after N pages with no new matches.",
    )
    args = parser.parse_args()

    amc_dir = args.root / "amcs" / "bandhan-mutual-fund"
    referer = REFERER_FORTNIGHTLY if args.fortnightly else REFERER_MONTHLY
    label = "fortnightly" if args.fortnightly else "monthly"

    print(f"Scanning Bandhan {label} disclosures…")
    if args.fortnightly:
        per_month = load_fortnightly_rows(list(args.months))
    else:
        per_month = load_monthly_rows(
            list(args.months),
            max_pages=args.max_pages,
            expected_per_month=args.expected_per_month,
            no_growth_page_limit=max(0, args.no_growth_page_limit),
        )

    for month_key in args.months:
        selected = list(per_month.get(month_key) or [])
        if args.limit > 0:
            selected = selected[: args.limit]

        out_dir = amc_dir / month_key
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n{month_key}: {len(selected)} {label} portfolio file(s)")

        manifest: list[dict] = []
        if not selected:
            print("  No rows matched.")

        for i, row in enumerate(selected, 1):
            file_url = file_url_from_row(row)
            if not file_url:
                continue
            fname = safe_filename(file_url)
            dest = out_dir / fname
            title = row.get("title") or ""
            acf = row.get("acf_fields") or {}
            rec = {
                "month": month_key,
                "download_url": file_url,
                "saved_as": fname,
                "title": title,
                "cms_id": row.get("id"),
                "disclosures_type": acf.get("disclosures_type")
                if isinstance(acf, dict)
                else None,
                "published": row.get("date"),
            }

            if args.dry_run:
                print(f"  [{i}] {fname}")
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue

            try:
                body = download(file_url, referer)
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
