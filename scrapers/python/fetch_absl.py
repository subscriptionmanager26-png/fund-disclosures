#!/usr/bin/env python3
"""
Aditya Birla Sun Life Mutual Fund - download monthly portfolio ZIPs for YYYY-MM.

Public page:
  https://mutualfund.adityabirlacapital.com/forms-and-downloads/portfolio

API used by page accordion:
  GET /postlogin/CustomApi/Resources/FactsheetAccordionById
    ?id=3ccab227-9de5-4494-b78d-2b4f7c0c054a
    &ctype=/sitecore/content/Root/BSL/Library/Lists/FAQ/Customer Types/Individual
    &month=March&year=<YYYY>

Response contains:
  {"ReturnCode":"1","AccordionList":[{"ResourceLink","pdfUrl",...}, ...]}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlparse, urlunparse

BASE = "https://mutualfund.adityabirlacapital.com"
PAGE_URL = f"{BASE}/forms-and-downloads/portfolio"
API_URL = f"{BASE}/postlogin/CustomApi/Resources/FactsheetAccordionById"
ACCORDION_ID = "3ccab227-9de5-4494-b78d-2b4f7c0c054a"
ACCORDION_ID_FORTNIGHTLY = "12341969-e855-4a80-b20a-dfb63e2268d4"
CTYPE = "/sitecore/content/Root/BSL/Library/Lists/FAQ/Customer Types/Individual"
ANCHOR_MONTH = "March"  # stable month token observed to return full list

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

TITLE_YM_RE = re.compile(
    r"as on\s+\w+\s+\d{1,2},\s*([12]\d{3})",
    re.I,
)
MONTH_TOKEN_RE = re.compile(
    r"as on\s+"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{1,2},\s*[12]\d{3}",
    re.I,
)
MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
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


def parse_month_from_title(title: str) -> tuple[int, int] | None:
    y = TITLE_YM_RE.search(title or "")
    m = MONTH_TOKEN_RE.search(title or "")
    if not y or not m:
        return None
    year = int(y.group(1))
    month = MONTHS.get(m.group(1).lower())
    if not month:
        return None
    return year, month


def safe_filename(name: str) -> str:
    s = (name or "").strip() or "absl_monthly_portfolio.zip"
    s = re.sub(r"[^\w.\-() ]+", "_", s).strip("._ ")
    return s[:220] or "absl_monthly_portfolio.zip"


def path_to_download_url(url: str) -> str:
    p = urlparse(url)
    safe_path = "/".join(quote(seg, safe="%") for seg in p.path.split("/"))
    host = p.netloc
    if host.lower() == "abcscprod.azureedge.net":
        # AzureEdge hostname is intermittently unresolved in some environments.
        host = "mutualfund.adityabirlacapital.com"
    return urlunparse((p.scheme, host, safe_path, p.params, p.query, p.fragment))


def fetch_rows_for_year(year: int, *, ctx: ssl.SSLContext, accordion_id: str) -> list[dict]:
    query = urlencode(
        {
            "id": accordion_id,
            "ctype": CTYPE,
            "month": ANCHOR_MONTH,
            "year": str(year),
        }
    )
    url = f"{API_URL}?{query}"
    req = urllib.request.Request(url, headers={**HEADERS, "Referer": PAGE_URL}, method="GET")
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        obj = json.loads(resp.read().decode("utf-8", errors="ignore"))
    rows = obj.get("AccordionList") or []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _assert_complete_zip(body: bytes) -> None:
    import io
    import zipfile

    if not body or body[:2] != b"PK":
        raise zipfile.BadZipFile("not a zip (bad magic)")
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise zipfile.BadZipFile(f"corrupt member {bad}")
        if not zf.namelist():
            raise zipfile.BadZipFile("empty zip")


def download(url: str, *, ctx: ssl.SSLContext, expect_zip: bool = False) -> bytes:
    """Download with retries; ABSL packs often truncate mid-transfer."""
    import time
    import zipfile

    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(
                url,
                headers={**HEADERS, "Accept": "*/*", "Referer": PAGE_URL},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=300, context=ctx) as resp:
                body = resp.read()
                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit() and len(body) < int(cl):
                    raise RuntimeError(
                        f"truncated download ({len(body)} < Content-Length {cl})"
                    )
            if expect_zip:
                _assert_complete_zip(body)
            return body
        except (urllib.error.URLError, TimeoutError, OSError, zipfile.BadZipFile, RuntimeError) as e:
            last_err = e
            if attempt >= 3:
                break
            wait = 5 * attempt
            print(f"  retry {attempt}/3 after {type(e).__name__}: {e} (sleep {wait}s)", flush=True)
            time.sleep(wait)
    assert last_err is not None
    raise last_err


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch ABSL monthly portfolio ZIP files")
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
    parser.add_argument(
        "--fortnightly",
        action="store_true",
        help="Use fortnightly debt accordion id instead of monthly",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "aditya-birla-sun-life-mutual-fund"
    targets = {month_key_to_ym(mk): mk for mk in args.months}
    target_years = sorted({y for y, _ in targets})
    accordion_id = ACCORDION_ID_FORTNIGHTLY if args.fortnightly else ACCORDION_ID

    all_rows: list[dict] = []
    for year in target_years:
        print(f"GET {API_URL} (id={accordion_id}, year={year}, month={ANCHOR_MONTH})", flush=True)
        try:
            rows = fetch_rows_for_year(year, ctx=ctx, accordion_id=accordion_id)
        except urllib.error.URLError as e:
            if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
                raise SystemExit(
                    f"{e}\n\nRetry with:  python3 scripts/fetch_absl.py ... --insecure-ssl"
                ) from e
            raise
        print(f"  Indexed {len(rows)} row(s) for year {year}", flush=True)
        all_rows.extend(rows)

    by_month: dict[tuple[int, int], list[dict]] = {}
    seen: set[str] = set()
    for row in all_rows:
        title = str(row.get("ResourceLink") or "").strip()
        ym = parse_month_from_title(title)
        if ym is None or ym not in targets:
            continue
        url = str(row.get("pdfUrl") or "").strip()
        if not url:
            continue
        key = f"{ym}:{url}"
        if key in seen:
            continue
        seen.add(key)
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
            print("  No portfolio row found for this month.", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue

        for row in selected:
            title = str(row.get("ResourceLink") or "").strip()
            raw_url = str(row.get("pdfUrl") or "").strip()
            url = path_to_download_url(raw_url)
            raw_name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
            fn = safe_filename(raw_name or f"absl_monthly_portfolio_{mk}.zip")
            rec = {
                "month": mk,
                "title": title,
                "download_url": url,
                "saved_as": fn,
            }
            if args.dry_run:
                print(f"  dry-run {fn}", flush=True)
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue
            try:
                body = download(url, ctx=ctx, expect_zip=fn.lower().endswith(".zip"))
                if not body:
                    raise RuntimeError("empty download body")
                h = hashlib.sha256(body).hexdigest()
                dest = out_dir / fn
                dest.write_bytes(body)
                manifest.append({**rec, "sha256": h})
                print(f"  OK {fn} ({len(body)} bytes)", flush=True)
            except Exception as e:
                # Never leave a truncated zip for the parser to trip on.
                dest = out_dir / fn
                if dest.exists() and fn.lower().endswith(".zip"):
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                manifest.append({**rec, "sha256": "", "error": str(e)})
                print(f"  ERR {fn}: {e}", flush=True)

        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"  Wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
