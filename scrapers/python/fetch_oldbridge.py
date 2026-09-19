#!/usr/bin/env python3
"""
Old Bridge Mutual Fund - download monthly portfolio files for YYYY-MM.

Source page:
  https://www.oldbridgemf.com/statutory-disclosures.html

Data source:
  Statutory disclosures tab "Monthly Portfolio" (v-pills-tabContent2).
  Rows contain title + direct upload link, e.g.:
  - Old Bridge Arbitrage Fund - February 2026
  - /uploads/Old_Bridge_Arbitrage_Fund_Monthly_Portfolio_Feb_26_....xlsx
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

BASE = "https://www.oldbridgemf.com"
PAGE_URL = f"{BASE}/statutory-disclosures.html"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TAB_RE = re.compile(
    r'<div class="tab-pane[^"]*" id="v-pills-tabContent2"[^>]*>(.*?)</div>\s*'
    r'<div class="tab-pane[^"]*" id="v-pills-tabContent3"',
    re.I | re.S,
)
ROW_RE = re.compile(
    r"<h([26])[^>]*>(.*?)</h\1>\s*"
    r'.*?<a[^>]+href="([^"]+\.xlsx?)"[^>]*>',
    re.I | re.S,
)
MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
TITLE_YM_RE = re.compile(
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"[\s_-]*(20\d{2}|\d{2})\b",
    re.I,
)
FILE_YM_RE = re.compile(
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[_-]?(20\d{2}|\d{2})",
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


def month_key_to_ym(month_key: str) -> tuple[int, int]:
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    y, m = int(parts[0]), int(parts[1].zfill(2))
    if not (1 <= m <= 12):
        raise ValueError(f"Bad month in {month_key!r}")
    return y, m


def safe_filename(name: str) -> str:
    s = (name or "").strip() or "oldbridge_monthly_portfolio.xlsx"
    s = re.sub(r"[^\w.\-() ]+", "_", s).strip("._ ")
    return s[:220] or "oldbridge_monthly_portfolio.xlsx"


def fetch_html(*, ctx: ssl.SSLContext) -> str:
    req = urllib.request.Request(PAGE_URL, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _to_year(yy: str) -> int:
    y = int(yy)
    return y + 2000 if y < 100 else y


def parse_ym(title: str, url: str) -> tuple[int, int] | None:
    s = " ".join(html.unescape(title or "").split())
    m = TITLE_YM_RE.search(s)
    if m:
        mon = MONTHS.get(m.group(1).lower())
        if mon:
            return _to_year(m.group(2)), mon
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    m = FILE_YM_RE.search(name)
    if m:
        mon = MONTHS.get(m.group(1).lower())
        if mon:
            return _to_year(m.group(2)), mon
    return None


def extract_rows(page_html: str) -> list[dict]:
    tm = TAB_RE.search(page_html)
    if not tm:
        return []
    block = tm.group(1)
    rows: list[dict] = []
    seen: set[str] = set()
    for _lvl, title_raw, href_raw in ROW_RE.findall(block):
        url = urljoin(BASE + "/", href_raw.strip())
        ym = parse_ym(title_raw, url)
        if ym is None:
            continue
        key = f"{ym[0]}-{ym[1]}|{url}"
        if key in seen:
            continue
        seen.add(key)
        title = " ".join(html.unescape(title_raw).split())
        rows.append({"year": ym[0], "month": ym[1], "title": title, "url": url})
    return rows


def download(url: str, *, ctx: ssl.SSLContext) -> bytes:
    req = urllib.request.Request(
        url,
        headers={**HEADERS, "Accept": "*/*", "Referer": PAGE_URL},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Old Bridge monthly portfolio files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument(
        "--insecure-ssl",
        action="store_true",
        help="Disable TLS verification if your Python lacks CA certs",
    )
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "old-bridge-mutual-fund"
    targets = {month_key_to_ym(mk): mk for mk in args.months}

    print(f"GET {PAGE_URL}", flush=True)
    try:
        page_html = fetch_html(ctx=ctx)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_oldbridge.py ... --insecure-ssl"
            ) from e
        raise

    rows = extract_rows(page_html)
    print(f"  Indexed {len(rows)} monthly row(s) from tabContent2", flush=True)

    by_month: dict[tuple[int, int], list[dict]] = {}
    for row in rows:
        ym = (row["year"], row["month"])
        if ym in targets:
            by_month.setdefault(ym, []).append(row)

    for ym, mk in targets.items():
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in out_dir.iterdir():
            if p.is_file():
                p.unlink()

        selected = by_month.get(ym, [])
        manifest: list[dict] = []
        print(f"\n{mk}: {len(selected)} file(s)", flush=True)
        if not selected:
            print("  No monthly portfolio file found for this month.", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue

        for row in selected:
            title = row["title"]
            url = row["url"]
            raw_name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
            # Overlap reports are not scheme portfolios — skip for monthly holdings.
            blob = f"{title} {raw_name}".lower()
            if "overlap" in blob:
                print(f"  SKIP overlap report: {raw_name or title}", flush=True)
                continue
            fn = safe_filename(raw_name or f"oldbridge-monthly-portfolio-{mk}.xlsx")
            rec = {
                "month": mk,
                "title": title,
                "source_page": PAGE_URL,
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

        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"  Wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
