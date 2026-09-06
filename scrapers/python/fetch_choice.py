#!/usr/bin/env python3
"""
Choice Mutual Fund — download **monthly** or **fortnightly portfolio** files for given YYYY-MM.

Monthly: Next.js API
  POST https://choicemf.com/api/monthly-portfolio-report/portfolio-website-list

Fortnightly (disclosures/fortnight-portfolio):
  GET https://choicemf.com/api/document-master-list
  → regulatory-compliance → fortnight-portfolio → financial_years[].files

Files are served from:

  https://doc.choicemf.com/<file_path>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import http.cookiejar
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlunparse, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from disclosure_date import dates_match_as_of, first_iso

API_URL = "https://choicemf.com/api/monthly-portfolio-report/portfolio-website-list"
DOCUMENT_LIST_URL = "https://choicemf.com/api/document-master-list"
DOC_BASE = "https://doc.choicemf.com/"
PAGE_REF_MONTHLY = "https://choicemf.com/disclosures/monthly-portfolio"
PAGE_REF_FORTNIGHTLY = "https://choicemf.com/disclosures/fortnight-portfolio"

MONTH_NUM_TO_NAME = {
    1: "january",
    2: "february",
    3: "march",
    4: "april",
    5: "may",
    6: "june",
    7: "july",
    8: "august",
    9: "september",
    10: "october",
    11: "november",
    12: "december",
}

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://choicemf.com",
}


def headers_for(page_ref: str, *, post: bool = False) -> dict[str, str]:
    h = {**BASE_HEADERS, "Referer": page_ref}
    if post:
        h["Content-Type"] = "application/json"
    return h


def encode_url_for_http(url: str) -> str:
    p = urlparse(url.strip())
    path = quote(p.path, safe="/%")
    return urlunparse((p.scheme, p.netloc, path, p.params, p.query, p.fragment))


def safe_filename(url: str, scheme_name: str, month_key: str) -> str:
    path = urlparse(url).path
    base = path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    base = base.replace("\u2013", "-").replace("\u2014", "-")
    if not base or base in (".", ".."):
        safe_scheme = re.sub(r"[^\w.\-]", "_", scheme_name)[:60]
        base = f"{safe_scheme}_{month_key}.xlsx"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.bin"


def report_date_to_month_key(report_date: str) -> str | None:
    """YYYY-MM-DD -> YYYY-MM"""
    m = re.match(r"^(\d{4})-(\d{2})-\d{2}$", (report_date or "").strip())
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}"


def month_key_to_fy(month_key: str) -> str:
    """Indian FY label used by document-master-list, e.g. 2026-07 -> 2026-27."""
    y, m = (int(x) for x in month_key.split("-"))
    if m >= 4:
        return f"{y}-{str(y + 1)[-2:]}"
    return f"{y - 1}-{str(y)[-2:]}"


def month_key_to_month_slug(month_key: str) -> str:
    _y, m = (int(x) for x in month_key.split("-"))
    return MONTH_NUM_TO_NAME[m]


def doc_name_to_as_of(*parts: str) -> str | None:
    return first_iso(*parts)


def file_download_url(file_row: dict) -> str:
    open_url = str(file_row.get("open_file_url") or "").strip()
    if open_url.startswith("http"):
        return open_url
    fp = str(file_row.get("file_path") or "").strip().lstrip("/")
    return urljoin(DOC_BASE, fp)


def fetch_monthly_listing(opener: urllib.request.OpenerDirector) -> list[dict]:
    data = json.dumps({}).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers=headers_for(PAGE_REF_MONTHLY, post=True),
        method="POST",
    )
    with opener.open(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    payload = json.loads(raw)
    body = payload.get("body") or {}
    rows = body.get("data")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def fetch_fortnight_files(opener: urllib.request.OpenerDirector) -> list[dict]:
    req = urllib.request.Request(
        DOCUMENT_LIST_URL,
        headers=headers_for(PAGE_REF_FORTNIGHTLY),
        method="GET",
    )
    with opener.open(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8", "ignore"))
    body = payload.get("body") or []
    for top in body:
        if str(top.get("slug") or "").strip() != "regulatory-compliance":
            continue
        for child in top.get("children") or []:
            if str(child.get("slug") or "").strip() != "fortnight-portfolio":
                continue
            out: list[dict] = []
            for fy in child.get("financial_years") or []:
                fy_label = str(fy.get("financial_year") or "").strip()
                for f in fy.get("files") or []:
                    if isinstance(f, dict):
                        out.append({**f, "_financial_year": fy_label})
            return out
    return []


def flatten_monthly_reports(
    listing: list[dict],
    month_keys: set[str],
) -> list[tuple[str, str, str, str]]:
    out: list[tuple[str, str, str, str]] = []
    for scheme in listing:
        name = str(scheme.get("scheme_name") or "").strip() or "scheme"
        reports = scheme.get("reports")
        if not isinstance(reports, list):
            continue
        for rep in reports:
            if not isinstance(rep, dict):
                continue
            rd = str(rep.get("report_date") or "").strip()
            fp = str(rep.get("file_path") or "").strip().lstrip("/")
            if not fp:
                continue
            mk = report_date_to_month_key(rd)
            if not mk or mk not in month_keys:
                continue
            url = urljoin(DOC_BASE, fp)
            out.append((mk, name, fp, url))
    return out


def flatten_fortnight_reports(
    files: list[dict],
    month_keys: set[str],
    as_of: str,
) -> list[tuple[str, str, str, str]]:
    out: list[tuple[str, str, str, str]] = []
    for f in files:
        doc_name = str(f.get("doc_name") or "").strip()
        month_slug = str(f.get("month") or "").strip().lower()
        fy = str(f.get("_financial_year") or "").strip()
        path = str(f.get("file_path") or "")
        open_url = str(f.get("open_file_url") or "")
        if as_of and not dates_match_as_of(doc_name, path, open_url, as_of=as_of):
            continue
        for mk in month_keys:
            if month_key_to_fy(mk) != fy:
                continue
            if month_slug and month_slug != month_key_to_month_slug(mk):
                continue
            url = file_download_url(f)
            fp = str(f.get("file_path") or "").strip()
            out.append((mk, doc_name, fp, url))
            break
    return out


def download(opener: urllib.request.OpenerDirector, url: str, *, page_ref: str) -> bytes:
    safe = encode_url_for_http(url)
    req = urllib.request.Request(
        safe,
        headers={
            "User-Agent": BASE_HEADERS["User-Agent"],
            "Accept": "*/*",
            "Referer": page_ref,
        },
        method="GET",
    )
    with opener.open(req, timeout=120) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Choice MF monthly/fortnightly portfolio files")
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
        help="Fetch fortnight-portfolio via document-master-list",
    )
    parser.add_argument(
        "--as-of",
        default="",
        help="Calendar as-of YYYY-MM-DD (filters fortnightly doc_name dates)",
    )
    args = parser.parse_args()

    want = set(args.months)
    amc_dir = args.root / "amcs" / "choice-mutual-fund"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    label = "fortnightly" if args.fortnightly else "monthly"
    page_ref = PAGE_REF_FORTNIGHTLY if args.fortnightly else PAGE_REF_MONTHLY
    as_of = args.as_of.strip()
    if args.fortnightly and not as_of and args.months:
        as_of = f"{args.months[0]}-15"

    if args.fortnightly:
        print(f"GET {DOCUMENT_LIST_URL} …", flush=True)
        listing = fetch_fortnight_files(opener)
        print(f"  … {len(listing)} fortnight file row(s)", flush=True)
        rows = flatten_fortnight_reports(listing, want, as_of)
    else:
        print(f"POST {API_URL} …", flush=True)
        listing = fetch_monthly_listing(opener)
        print(f"  … {len(listing)} scheme row(s) in API", flush=True)
        rows = flatten_monthly_reports(listing, want)

    by_month: dict[str, list[tuple[str, str, str, str]]] = {k: [] for k in args.months}
    for item in rows:
        mk = item[0]
        if mk in by_month:
            by_month[mk].append(item)

    for month_key in args.months:
        out_dir = amc_dir / month_key
        out_dir.mkdir(parents=True, exist_ok=True)
        batch = by_month.get(month_key) or []
        suffix = f" as_of={as_of}" if args.fortnightly and as_of else ""
        print(f"\n{month_key} [{label}{suffix}]: {len(batch)} file(s)", flush=True)
        manifest: list[dict] = []

        if not batch:
            print(f"  No {label} reports for this month.", flush=True)

        for i, (_mk, scheme_name, file_path, file_url) in enumerate(batch, 1):
            fname = safe_filename(file_url, scheme_name, month_key)
            rec = {
                "month": month_key,
                "as_of": as_of or None,
                "scheme_name": scheme_name,
                "report_path": file_path,
                "download_url": file_url,
                "saved_as": fname,
            }
            if args.dry_run:
                print(f"  [{i}] {fname}", flush=True)
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue
            try:
                body = download(opener, file_url, page_ref=page_ref)
                h = hashlib.sha256(body).hexdigest()
                (out_dir / fname).write_bytes(body)
                manifest.append({**rec, "sha256": h})
                print(f"  [{i}] OK {fname} ({len(body)} bytes)", flush=True)
            except Exception as e:
                manifest.append({**rec, "sha256": "", "error": str(e)})
                print(f"  [{i}] ERR {fname}: {e}", flush=True)

        man_path = out_dir / "manifest.json"
        man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Wrote {man_path}", flush=True)


if __name__ == "__main__":
    main()
