#!/usr/bin/env python3
"""
Abakkus Mutual Fund - download portfolio disclosure files for YYYY-MM.

Source page:
  https://www.abakkusmf.com/statutory-disclosures.html

Data source:
  Embedded JS variable on the disclosures page:
  const verticals = [...]
  Verticals:
    - Monthly Portfolio Disclosures  (default)
    - Fortnightly Portfolio            (--fortnightly)
  Items expose downloadMedia.url / downloadUrl.
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

BASE = "https://www.abakkusmf.com"
PAGE_URL = f"{BASE}/statutory-disclosures.html"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
VERTICALS_RE = re.compile(r"(?:const|var|let)\s+verticals\s*=\s*(\[.*?\]);", re.S)
VERTICALS_JSON_RE = re.compile(
    r'<script[^>]+id=["\']verticals-data["\'][^>]*>(\[.*?\])</script>',
    re.S | re.I,
)
TITLE_YM_RE = re.compile(
    r"\b"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+(\d{4})\b",
    re.I,
)
TITLE_MDY_RE = re.compile(
    r"\b"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{1,2},\s*(\d{4})\b",
    re.I,
)
TITLE_DMY_RE = re.compile(
    r"\b\d{1,2}[/-]\d{1,2}[/-](20\d{2})\b",
    re.I,
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
    s = (name or "").strip() or "abakkus_monthly_portfolio.xlsx"
    s = re.sub(r"[^\w.\-() ]+", "_", s).strip("._ ")
    return s[:220] or "abakkus_monthly_portfolio.xlsx"


def fetch_html(*, ctx: ssl.SSLContext) -> str:
    req = urllib.request.Request(PAGE_URL, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def parse_ym_from_title(title: str) -> tuple[int, int] | None:
    clean = " ".join(html.unescape(title or "").split())
    # "15th July 2026" / "31 July 2026"
    m_dmy = re.search(
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+(20\d{2})\b",
        clean,
        re.I,
    )
    if m_dmy:
        month = MONTHS.get(m_dmy.group(1).lower())
        if month:
            return int(m_dmy.group(2)), month
    m = TITLE_YM_RE.search(clean)
    if not m:
        m = TITLE_MDY_RE.search(clean)
    if m:
        month = MONTHS.get(m.group(1).lower())
        if month:
            return int(m.group(2)), month
    m = re.search(
        r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b",
        clean,
        re.I,
    )
    if m:
        mm = int(m.group(2))
        yy = int(m.group(3))
        if 1 <= mm <= 12:
            return yy, mm
    return None


def load_verticals(page_html: str) -> list[dict]:
    m = VERTICALS_RE.search(page_html)
    if m:
        return json.loads(m.group(1))
    m = VERTICALS_JSON_RE.search(page_html)
    if m:
        return json.loads(m.group(1))
    return []


def extract_rows_from_verticals(page_html: str, *, fortnightly: bool = False) -> list[dict]:
    data = load_verticals(page_html)
    if not data:
        return []
    want = "fortnightly portfolio" if fortnightly else "monthly portfolio disclosures"
    target = None
    for vertical in data:
        title = str(vertical.get("title") or "").strip().lower()
        if title == want or (fortnightly and "fortnightly" in title and "portfolio" in title):
            target = vertical
            break
    if not target:
        return []

    rows: list[dict] = []
    seen: set[str] = set()
    for section in target.get("sections", []):
        for sub in section.get("subSections", []):
            for item in sub.get("items", []):
                title = " ".join(html.unescape(str(item.get("title") or "")).split())
                media = item.get("downloadMedia") or {}
                rel_url = str(media.get("url") or item.get("downloadUrl") or "").strip()
                if not rel_url:
                    continue
                url = rel_url if rel_url.startswith("http") else urljoin(BASE + "/", rel_url)
                ym = parse_ym_from_title(title)
                if ym is None:
                    # Fallback: infer from filename token if title is sparse.
                    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
                    tm = re.search(
                        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[_-]?(\d{4})",
                        name,
                        re.I,
                    )
                    if tm:
                        month = MONTHS.get(tm.group(1).lower())
                        if month:
                            ym = (int(tm.group(2)), month)
                if ym is None:
                    continue
                key = f"{ym[0]}-{ym[1]}|{url}"
                if key in seen:
                    continue
                seen.add(key)
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
    parser = argparse.ArgumentParser(description="Fetch Abakkus monthly portfolio disclosures")
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
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "abakkus-mutual-fund"
    targets = {month_key_to_ym(mk): mk for mk in args.months}

    print(f"GET {PAGE_URL}", flush=True)
    try:
        page_html = fetch_html(ctx=ctx)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_abakkus.py ... --insecure-ssl"
            ) from e
        raise

    rows = extract_rows_from_verticals(page_html, fortnightly=args.fortnightly)
    kind = "fortnightly" if args.fortnightly else "monthly"
    print(f"  Indexed {len(rows)} {kind} row(s) from JS verticals", flush=True)

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
            print(f"  No {kind} portfolio file found for this month.", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue

        for row in selected:
            title = row["title"]
            url = row["url"]
            raw_name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
            fn = safe_filename(raw_name or f"abakkus-monthly-portfolio-{mk}.xlsx")
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
