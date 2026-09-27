#!/usr/bin/env python3
"""
HSBC Mutual Fund — download monthly fund portfolio files for given YYYY-MM.

Source page (accordion with section-specific tables):
  https://www.assetmanagement.hsbc.co.in/en/mutual-funds/investor-resources/information-library
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

PAGE_URL = "https://www.assetmanagement.hsbc.co.in/en/mutual-funds/investor-resources/information-library"
BASE_URL = "https://www.assetmanagement.hsbc.co.in"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
FUND_PORTFOLIO_SECTION_RE = re.compile(
    r'<section[^>]+id="tabpanel-1797700734"[^>]*>(.*?)</section>',
    re.I | re.S,
)
# Accordion #9 — Fortnightly portfolio of debt schemes (registry page_url hash …=9)
FORTNIGHTLY_SECTION_RE = re.compile(
    r'id="tabpanel-1884516352"[^>]*>(.*?)(?=id="tabpanel-\d+"|\Z)',
    re.I | re.S,
)
MONTH_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b[\s,\-]+(\d{4})",
    re.I,
)


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    if not base or base in (".", ".."):
        base = "download.xlsx"
    base = re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.xlsx"
    if not re.search(r"\.(xlsx|xls|xlsm|zip)$", base, re.I):
        if "fortnightly-debt-portfolio" in url.lower():
            base = f"{base}.xlsx"
    return base


def _http_get(url: str, *, referer: str = PAGE_URL, accept: str = "*/*") -> bytes:
    try:
        from curl_cffi import requests as creq  # type: ignore

        r = creq.get(
            url,
            headers={**HEADERS, "Accept": accept, "Referer": referer},
            impersonate="chrome131",
            timeout=120,
        )
        r.raise_for_status()
        return r.content
    except Exception:
        req = Request(url, headers={**HEADERS, "Accept": accept, "Referer": referer})
        with urlopen(req, timeout=300) as resp:
            return resp.read()


def fetch_html() -> str:
    try:
        return _http_get(PAGE_URL, referer=PAGE_URL, accept=HEADERS["Accept"]).decode(
            "utf-8", "ignore"
        )
    except Exception:
        # HSBC occasionally stalls with urllib TLS reads; curl is more reliable here.
        body = curl_fetch(PAGE_URL, referer=PAGE_URL)
        return body.decode("utf-8", "ignore")


def text_to_month_key(text: str) -> str | None:
    m = MONTH_RE.search(text)
    if not m:
        return None
    mon, year = m.group(1), m.group(2)
    try:
        dt = datetime.strptime(f"{mon} {year}", "%B %Y")
        return f"{dt.year:04d}-{dt.month:02d}"
    except ValueError:
        return None


FORTNIGHTLY_LINK_RE = re.compile(
    r'href="([^"]*fortnightly-debt-portfolio/document-\d{8}/[^"#?]+)"',
    re.I,
)


def as_of_to_document_token(as_of: str) -> str | None:
    """YYYY-MM-DD -> DDMMYYYY for HSBC document-15072026 paths."""
    parts = as_of.strip().split("-")
    if len(parts) != 3:
        return None
    y, m, d = parts[0], parts[1], parts[2]
    return f"{int(d):02d}{m}{y}"


def extract_fortnightly_rows(html: str, as_of: str = "") -> list[dict]:
    sec = FORTNIGHTLY_SECTION_RE.search(html)
    block = sec.group(1) if sec else html
    if not sec:
        print("  ⚠ Fortnightly accordion (tabpanel-1884516352) not found; scanning full page", flush=True)
    doc_token = as_of_to_document_token(as_of) if as_of else ""
    rows: list[dict] = []
    for m in FORTNIGHTLY_LINK_RE.finditer(block):
        href = m.group(1)
        href_l = href.lower()
        if doc_token and f"document-{doc_token}/" not in href_l:
            continue
        url = urljoin(BASE_URL, href)
        fname = safe_filename(url)
        rows.append(
            {
                "download_url": url,
                "title": fname,
                "as_of": as_of or None,
                "document_token": doc_token or None,
            }
        )
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        u = r["download_url"]
        if u in seen:
            continue
        seen.add(u)
        out.append(r)
    return out


def extract_rows(html: str) -> list[dict]:
    sec = FUND_PORTFOLIO_SECTION_RE.search(html)
    block = sec.group(1) if sec else html
    if not sec:
        print("  ⚠ Fund portfolios accordion not found; scanning full page", flush=True)
    rows: list[dict] = []
    for href, label_html in LINK_RE.findall(block):
        href_l = href.lower()
        if "/portfolios/" not in href_l or not re.search(r"\.(xlsx|xls)(?:\?|$)", href_l):
            continue
        label = re.sub(r"<[^>]+>", " ", label_html)
        label = re.sub(r"\s+", " ", label).strip()
        blob = f"{label} {href}"
        mk = text_to_month_key(blob)
        if not mk:
            continue
        url = urljoin(BASE_URL, href)
        rows.append(
            {
                "month_key": mk,
                "download_url": url,
                "title": label or safe_filename(url),
            }
        )
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for r in rows:
        key = (r["month_key"], r["download_url"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def download(url: str) -> bytes:
    try:
        return _http_get(url, referer=PAGE_URL, accept="*/*")
    except Exception:
        return curl_fetch(url, referer=PAGE_URL)


def curl_fetch(url: str, referer: str) -> bytes:
    proc = subprocess.run(
        [
            "curl",
            "-sS",
            "-L",
            "--max-time",
            "300",
            "-A",
            HEADERS["User-Agent"],
            "-H",
            "Accept: */*",
            "-H",
            f"Referer: {referer}",
            url,
        ],
        check=True,
        capture_output=True,
    )
    return proc.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch HSBC monthly fund portfolio files")
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
        help="Fetch per-scheme fortnightly debt portfolios from information-library",
    )
    parser.add_argument(
        "--as-of",
        default="",
        help="Calendar as-of YYYY-MM-DD (maps to document-DDMMYYYY folder)",
    )
    args = parser.parse_args()

    amc_dir = args.root / "amcs" / "hsbc-mutual-fund"
    print(f"GET {PAGE_URL} ...")
    html = fetch_html()

    if args.fortnightly:
        as_of = args.as_of.strip()
        if not as_of and args.months:
            as_of = f"{args.months[0]}-15"
        rows = extract_fortnightly_rows(html, as_of)
        print(f"  ... parsed {len(rows)} fortnightly link(s) for as_of={as_of}")
        for mk in args.months:
            out_dir = amc_dir / mk
            out_dir.mkdir(parents=True, exist_ok=True)
            batch = rows
            print(f"\n{mk} [fortnightly as_of={as_of}]: {len(batch)} file(s)")
            manifest: list[dict] = []
            if not batch:
                print("  No matching fortnightly rows.")
            for i, row in enumerate(batch, 1):
                url = row["download_url"]
                fname = safe_filename(url)
                rec = {
                    "month": mk,
                    "as_of": as_of,
                    "download_url": url,
                    "saved_as": fname,
                    "title": row.get("title"),
                }
                if args.dry_run:
                    print(f"  [{i}] {fname}")
                    manifest.append({**rec, "sha256": "", "dry_run": True})
                    continue
                try:
                    body = download(url)
                    h = hashlib.sha256(body).hexdigest()
                    (out_dir / fname).write_bytes(body)
                    manifest.append({**rec, "sha256": h})
                    print(f"  [{i}] OK {fname} ({len(body)} bytes)")
                except Exception as e:
                    manifest.append({**rec, "sha256": "", "error": str(e)})
                    print(f"  [{i}] ERR {fname}: {e}")
            (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            print(f"Wrote {out_dir / 'manifest.json'}")
        return

    rows = extract_rows(html)
    print(f"  ... parsed {len(rows)} monthly fund portfolio link(s)")

    by_month: dict[str, list[dict]] = {k: [] for k in args.months}
    for r in rows:
        mk = r.get("month_key")
        if mk in by_month:
            by_month[mk].append(r)

    for mk in args.months:
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        batch = by_month.get(mk) or []
        print(f"\n{mk}: {len(batch)} file(s)")
        manifest: list[dict] = []
        if not batch:
            print("  No matching rows for this month.")
        for i, row in enumerate(batch, 1):
            url = row["download_url"]
            fname = safe_filename(url)
            rec = {
                "month": mk,
                "download_url": url,
                "saved_as": fname,
                "title": row.get("title"),
            }
            if args.dry_run:
                print(f"  [{i}] {fname}")
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue
            try:
                body = download(url)
                h = hashlib.sha256(body).hexdigest()
                (out_dir / fname).write_bytes(body)
                manifest.append({**rec, "sha256": h})
                print(f"  [{i}] OK {fname} ({len(body)} bytes)")
            except Exception as e:
                manifest.append({**rec, "sha256": "", "error": str(e)})
                print(f"  [{i}] ERR {fname}: {e}")
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Wrote {out_dir / 'manifest.json'}")


if __name__ == "__main__":
    main()
