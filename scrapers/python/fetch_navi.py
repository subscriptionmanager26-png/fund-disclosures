#!/usr/bin/env python3
"""
Navi Mutual Fund - download monthly portfolio files for YYYY-MM.

Source page:
  https://navi.com/mutual-fund/downloads/portfolio

Data source:
  POST https://navi.com/wp-json/nv/v1/documents
  with category=884, type=Monthly, order=DESC, financial year + month value.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE = "https://navi.com"
PAGE_URL = f"{BASE}/mutual-fund/downloads/portfolio"
API_URL = f"{BASE}/wp-json/nv/v1/documents"
MONTHLY_CATEGORY = "884"
FORTNIGHTLY_CATEGORY = "885"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
MONTH_NUM_TO_NAME = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}
# Last day of month used in Navi fortnight dropdown values (non-leap Feb = 28).
FORTNIGHT_MONTH_END = {
    1: 31,
    2: 28,
    3: 31,
    4: 30,
    5: 31,
    6: 30,
    7: 31,
    8: 31,
    9: 30,
    10: 31,
    11: 30,
    12: 31,
}
TITLE_YM_RE = re.compile(
    r"\b\d{1,2}(?:st|nd|rd|th)\s*[–-]\s*"
    r"\d{1,2}(?:st|nd|rd|th)\s+"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+(\d{4})\b",
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
    s = (name or "").strip() or "navi_monthly_portfolio.xlsx"
    s = re.sub(r"[^\w.\-() ]+", "_", s).strip("._ ")
    return s[:220] or "navi_monthly_portfolio.xlsx"


def _curl_session():
    try:
        from curl_cffi import requests as creq  # type: ignore

        return creq
    except ImportError as e:
        raise SystemExit(
            "curl_cffi required:  .venv/bin/pip install curl_cffi\n" + str(e)
        ) from e


def fetch_text(url: str, *, ctx: ssl.SSLContext) -> str:
    try:
        creq = _curl_session()
        r = creq.get(
            url,
            headers=HEADERS,
            impersonate="chrome131",
            timeout=180,
            verify=not ctx.check_hostname is False,
        )
        r.raise_for_status()
        return r.text
    except Exception:
        req = urllib.request.Request(url, headers=HEADERS, method="GET")
        with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
            return resp.read().decode("utf-8", errors="ignore")


def fetch_nonce(*, ctx: ssl.SSLContext) -> str:
    html_text = fetch_text(PAGE_URL, ctx=ctx)
    m = re.search(r'"nonce":"([a-f0-9]+)"', html_text)
    if not m:
        raise RuntimeError("Could not locate nonce on source page")
    return m.group(1)


def fiscal_year_for_month(year: int, month: int) -> str:
    # Indian financial year runs Apr-Mar.
    if month >= 4:
        return f"{year}-{year + 1}"
    return f"{year - 1}-{year}"


def fortnight_values_for_month(year: int, month: int) -> list[str]:
    """Dropdown values like 'July 1-15' / 'July 16-31' (Feb end 28/29)."""
    name = MONTH_NUM_TO_NAME[month]
    end = FORTNIGHT_MONTH_END[month]
    if month == 2 and year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        end = 29
    return [f"{name} 1-15", f"{name} 16-{end}"]


def fetch_rows_for_month(
    *,
    ctx: ssl.SSLContext,
    nonce: str,
    year: int,
    month: int,
    fortnightly: bool = False,
) -> list[dict]:
    if fortnightly:
        values = fortnight_values_for_month(year, month)
        category = FORTNIGHTLY_CATEGORY
        doc_type = "Fortnightly"
    else:
        values = [MONTH_NUM_TO_NAME[month]]
        category = MONTHLY_CATEGORY
        doc_type = "Monthly"

    rows: list[dict] = []
    for value in values:
        payload = {
            "financial_year": fiscal_year_for_month(year, month),
            "value": value,
            "category": category,
            "type": doc_type,
            "order": "DESC",
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            API_URL,
            data=data,
            method="POST",
            headers={
                **HEADERS,
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": BASE,
                "Referer": PAGE_URL,
                "WP-NONCE": nonce,
            },
        )
        try:
            creq = _curl_session()
            r = creq.post(
                API_URL,
                data=payload,
                headers={
                    **HEADERS,
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": BASE,
                    "Referer": PAGE_URL,
                    "WP-NONCE": nonce,
                },
                impersonate="chrome131",
                timeout=180,
                verify=not ctx.check_hostname is False,
            )
            r.raise_for_status()
            raw = r.text
        except Exception:
            with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
        obj = json.loads(raw)
        if not obj.get("success"):
            continue
        batch = obj.get("data") or []
        if isinstance(batch, list):
            rows.extend(batch)
    return rows


def parse_month_from_title(title: str) -> tuple[int, int] | None:
    clean = html.unescape(title or "")
    m = TITLE_YM_RE.search(clean)
    if m:
        month = MONTHS.get(m.group(1).lower())
        if month:
            return int(m.group(2)), month
    # Fortnightly titles: "Navi Liquid Fund 1st – 15th July 2026"
    m2 = re.search(
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+(\d{4})\b",
        clean,
        re.I,
    )
    if not m2:
        return None
    month = MONTHS.get(m2.group(1).lower())
    if not month:
        return None
    return int(m2.group(2)), month


def row_url(row: dict) -> list[str]:
    raw = row.get("url")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    return []


def download(url: str, *, ctx: ssl.SSLContext) -> bytes:
    try:
        creq = _curl_session()
        r = creq.get(
            url,
            headers={**HEADERS, "Accept": "*/*", "Referer": PAGE_URL},
            impersonate="chrome131",
            timeout=180,
            verify=not ctx.check_hostname is False,
        )
        r.raise_for_status()
        return r.content
    except Exception:
        req = urllib.request.Request(
            url,
            headers={**HEADERS, "Accept": "*/*", "Referer": PAGE_URL},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
            return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Navi monthly portfolio files")
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
    amc_dir = args.root / "amcs" / "navi-mutual-fund"
    targets = {month_key_to_ym(mk): mk for mk in args.months}

    print(f"GET {PAGE_URL} (nonce bootstrap)", flush=True)
    try:
        nonce = fetch_nonce(ctx=ctx)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_navi.py ... --insecure-ssl"
            ) from e
        raise
    print(f"  nonce={nonce}", flush=True)

    for ym, mk in targets.items():
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in out_dir.iterdir():
            if p.is_file():
                p.unlink()

        print(f"\n{mk}: querying {API_URL}", flush=True)
        rows = fetch_rows_for_month(
            ctx=ctx,
            nonce=nonce,
            year=ym[0],
            month=ym[1],
            fortnightly=args.fortnightly,
        )
        selected: list[dict] = []
        for row in rows:
            title = str(row.get("title") or "")
            parsed = parse_month_from_title(title)
            if parsed == ym or args.fortnightly:
                # Fortnightly API already scoped by month value; keep all rows.
                if args.fortnightly or parsed == ym:
                    selected.append(row)
        if args.fortnightly:
            # de-dupe by url
            seen = set()
            uniq = []
            for row in selected:
                urls = tuple(row_url(row))
                if urls in seen:
                    continue
                seen.add(urls)
                uniq.append(row)
            selected = uniq

        print(f"  {len(selected)} row(s) matched title month filter", flush=True)
        manifest: list[dict] = []
        if not selected:
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            kind = "fortnightly" if args.fortnightly else "monthly"
            print(f"  No {kind} files found for this month.", flush=True)
            continue

        seen_urls: set[str] = set()
        for row in selected:
            title = html.unescape(str(row.get("title") or "")).strip()
            for url in row_url(row):
                url = url.replace("\\/", "/").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                raw_name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
                default = (
                    f"navi_fortnightly_portfolio_{mk}.xlsx"
                    if args.fortnightly
                    else f"navi_monthly_portfolio_{mk}.xlsx"
                )
                fn = safe_filename(raw_name or default)
                rec = {
                    "month": mk,
                    "title": title,
                    "source_page": PAGE_URL,
                    "api_url": API_URL,
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
