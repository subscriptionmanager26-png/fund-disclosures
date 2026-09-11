#!/usr/bin/env python3
"""
Nippon India Mutual Fund — download **monthly portfolio** `.xls` / `.xlsx` for given YYYY-MM.

Source (SharePoint-style HTML):
  https://mf.nipponindiaim.com/investor-service/downloads/factsheet-portfolio-and-other-disclosures

Rows look like:
  <label class="lhsLbl">Monthly portfolio for the month of February 2026</label>
  … <a class="xls" href="/InvestorServices/FactsheetsDocuments/NIMF-MONTHLY-PORTFOLIO-28-Feb-26.xls">

Only rows whose label contains **monthly portfolio** (case-insensitive) are considered
by default. With `--fortnightly`, keep debt/fortnightly disclosure rows
(labels containing ``fortnightly`` or ``debt schemes portfolio``).
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
from urllib.parse import unquote, urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from disclosure_date import extract_year_month

BASE = "https://mf.nipponindiaim.com"
LISTING_URL = (
    f"{BASE}/investor-service/downloads/factsheet-portfolio-and-other-disclosures"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
}

LI_BLOCK_RE = re.compile(r"<li[^>]*>([\s\S]*?)</li>", re.I)
LHS_LBL_RE = re.compile(r'<label class="lhsLbl">([^<]+)</label>', re.I)
RHS_XLS_RE = re.compile(
    r'<label class="rhsLbl"><a class="xls" href="([^"]+\.(?:xls|xlsx))"',
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


def normalize_label(s: str) -> str:
    s = re.sub(r"[\u200b\u200c\u200d\ufeff\u00a0]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def label_to_year_month(*parts: str) -> tuple[int, int] | None:
    """Return (year, month) from a listing label and/or file href."""
    return extract_year_month(*(normalize_label(p) if p else "" for p in parts))


def fetch_listing_html(*, ctx: ssl.SSLContext) -> str:
    req = urllib.request.Request(LISTING_URL, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def iter_li_label_xls_rows(html: str) -> list[tuple[str, str]]:
    """Pairs (label, relative_or_absolute href) from each `<li>` that has an `.xls` rhs link."""
    rows: list[tuple[str, str]] = []
    for m in LI_BLOCK_RE.finditer(html):
        block = m.group(1)
        rm = RHS_XLS_RE.search(block)
        if not rm:
            continue
        pos = rm.start()
        label = ""
        for lm in LHS_LBL_RE.finditer(block):
            if lm.start() < pos:
                label = lm.group(1)
        if not label:
            continue
        rows.append((label.strip(), rm.group(1).strip()))
    return rows


def parse_portfolio_index(
    html: str, *, fortnightly: bool
) -> dict[tuple[int, int], list[tuple[str, str]]]:
    """(year, month) -> list of (absolute_url, normalized_label)."""
    out: dict[tuple[int, int], list[tuple[str, str]]] = {}
    seen: set[str] = set()
    for raw_label, href in iter_li_label_xls_rows(html):
        lab = normalize_label(raw_label)
        low = lab.lower()
        if fortnightly:
            if "fortnightly" not in low and "debt schemes portfolio" not in low:
                continue
        elif "monthly portfolio" not in low:
            continue
        ym = label_to_year_month(lab, href)
        if ym is None:
            continue
        url = href.strip()
        if not url.lower().startswith("http"):
            url = urljoin(BASE, url)
        if url in seen:
            continue
        seen.add(url)
        # Monthly: first occurrence wins (newest-first page). Fortnightly: keep all.
        if not fortnightly and ym in out:
            continue
        out.setdefault(ym, []).append((url, lab))
    return out


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = unquote(path.rsplit("/", 1)[-1].split("?")[0])
    base = re.sub(r"[^\w.\-]+", "_", base).strip("._")[:200]
    return base or "nippon_portfolio.xls"


def download(url: str, *, ctx: ssl.SSLContext) -> bytes:
    req = urllib.request.Request(
        url,
        headers={**HEADERS, "Accept": "*/*", "Referer": LISTING_URL},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read()


def month_key_to_ym(month_key: str) -> tuple[int, int]:
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    y, m = int(parts[0]), int(parts[1].zfill(2))
    if not (1 <= m <= 12):
        raise ValueError(f"Bad month in {month_key!r}")
    return y, m


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Nippon India MF monthly portfolio xls/xlsx",
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
    parser.add_argument(
        "--fortnightly",
        action="store_true",
        help="Keep rows whose label contains 'fortnightly' instead of monthly portfolio",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "nippon-india-mutual-fund"

    print(f"GET {LISTING_URL} …", flush=True)
    try:
        html = fetch_listing_html(ctx=ctx)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_nippon.py ... --insecure-ssl"
            ) from e
        raise
    index = parse_portfolio_index(html, fortnightly=args.fortnightly)
    kind = "fortnightly" if args.fortnightly else "monthly portfolio"
    print(f"  Indexed {len(index)} distinct calendar month(s) ({kind} rows)", flush=True)

    for mk in args.months:
        y, mon = month_key_to_ym(mk)
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        rows = index.get((y, mon)) or []
        manifest: list[dict] = []
        print(f"\n{mk}: {len(rows)} file(s)", flush=True)
        if not rows:
            print(f"  No {kind} row for this month (check listing / wording).", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue
        for url, label in rows:
            fn = safe_filename(url)
            rec = {
                "month": mk,
                "label": label,
                "kind": "fortnightly_portfolio" if args.fortnightly else "monthly_portfolio_combined",
                "download_url": url,
                "saved_as": fn,
            }
            if args.dry_run:
                print(f"  dry-run {fn}", flush=True)
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue
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
