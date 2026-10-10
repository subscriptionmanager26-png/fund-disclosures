#!/usr/bin/env python3
"""
Bank of India Mutual Fund (boimf.in) — monthly and fortnightly portfolio workbooks.

Monthly (Investor Corner):
  POST https://www.boimf.in/AjaxService.asmx/GetDocuments
  LibraryName: InvestorCorner, folderName: MONTHLY PORTFOLIO

Fortnightly (Regulatory reports):
  POST https://www.boimf.in/AjaxService.asmx/RGetDocuments
  LibraryName: Reports, category: FORTNIGHTLY PORTFOLIO OF DEBT SCHEMES
  DocName prefix DDMMYYYY, e.g. 15072026_BANK OF INDIA_FORTNIGHTLYDISCLOSURE
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

BASE = "https://www.boimf.in"
AJAX_URL = f"{BASE}/AjaxService.asmx/GetDocuments"
FORTNIGHTLY_AJAX_URL = f"{BASE}/AjaxService.asmx/RGetDocuments"
REFERER = f"{BASE}/investor-corner"
FORTNIGHTLY_REFERER = f"{BASE}/regulatory-reports/fortnightly-portfolio-of-debt-schemes"

MONTH_NAME_TO_NUM = {
    "january": "01",
    "jan": "01",
    "february": "02",
    "feb": "02",
    "march": "03",
    "mar": "03",
    "april": "04",
    "apr": "04",
    "may": "05",
    "june": "06",
    "jun": "06",
    "july": "07",
    "jul": "07",
    "august": "08",
    "aug": "08",
    "september": "09",
    "sep": "09",
    "sept": "09",
    "october": "10",
    "oct": "10",
    "november": "11",
    "nov": "11",
    "december": "12",
    "dec": "12",
}

# e.g. MONTHLY-PORTFOLIO - 28-FEBRUARY-2026
STANDARD_MONTHLY_RE = re.compile(
    r"MONTHLY-PORTFOLIO\s*-\s*\d{1,2}-([A-Za-z]+)-(\d{4})\s*$",
    re.I,
)

FORTNIGHTLY_DOC_RE = re.compile(r"^(\d{2})(\d{2})(\d{4})_", re.I)


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    if not base or base in (".", ".."):
        base = "download.bin"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.bin"


def docname_to_month_key(doc_name: str) -> str | None:
    m = STANDARD_MONTHLY_RE.search((doc_name or "").strip())
    if not m:
        return None
    mon_word, year = m.group(1), m.group(2)
    mm = MONTH_NAME_TO_NUM.get(mon_word.lower())
    if not mm:
        return None
    return f"{year}-{mm}"


def fortnightly_doc_to_as_of(doc_name: str) -> str | None:
    m = FORTNIGHTLY_DOC_RE.match((doc_name or "").strip())
    if not m:
        return None
    dd, mm, yyyy = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return None
    return f"{yyyy}-{mm:02d}-{dd:02d}"


def as_of_to_ddmmyyyy(as_of: str) -> str | None:
    parts = as_of.strip().split("-")
    if len(parts) != 3:
        return None
    try:
        y, m, d = parts[0], int(parts[1]), int(parts[2])
    except ValueError:
        return None
    return f"{d:02d}{m:02d}{y}"


def post_ajax(url: str, payload: dict, *, referer: str) -> list[dict]:
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=data,
        method="POST",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, */*",
            "Origin": BASE,
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    with urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    outer = json.loads(raw)
    inner = json.loads(outer.get("d") or "{}")
    docs = inner.get("Documents")
    if not isinstance(docs, list):
        return []
    return [d for d in docs if isinstance(d, dict)]


def fetch_document_index() -> list[dict]:
    payload = {
        "pagno": 0,
        "category": None,
        "fromDate": None,
        "toDate": None,
        "LibraryName": "InvestorCorner",
        "folderName": "MONTHLY PORTFOLIO",
        "CategoryValue": "no",
    }
    return post_ajax(AJAX_URL, payload, referer=REFERER)


def fetch_fortnightly_index() -> list[dict]:
    payload = {
        "pagno": 0,
        "category": "FORTNIGHTLY PORTFOLIO OF DEBT SCHEMES",
        "fromDate": None,
        "toDate": None,
        "LibraryName": "Reports",
    }
    return post_ajax(FORTNIGHTLY_AJAX_URL, payload, referer=FORTNIGHTLY_REFERER)


def pick_for_months(
    docs: list[dict],
    month_keys: list[str],
) -> dict[str, dict]:
    """
    For each requested YYYY-MM, pick one row: prefer standard MONTHLY-PORTFOLIO name;
    if multiple (shouldn't happen), keep the one with lexicographically greatest DocName.
    """
    want = set(month_keys)
    per_month: dict[str, list[dict]] = {k: [] for k in month_keys}

    for row in docs:
        name = row.get("DocName") or ""
        mk = docname_to_month_key(name)
        if mk is None:
            continue
        if mk in want:
            per_month[mk].append(row)

    chosen: dict[str, dict] = {}
    for mk in month_keys:
        rows = per_month.get(mk) or []
        if not rows:
            continue
        rows.sort(key=lambda r: (r.get("DocName") or ""), reverse=True)
        chosen[mk] = rows[0]
    return chosen


def pick_fortnightly_for_months(
    docs: list[dict],
    month_keys: list[str],
    as_of: str = "",
) -> dict[str, dict]:
    want = set(month_keys)
    token = as_of_to_ddmmyyyy(as_of) if as_of else ""
    chosen: dict[str, dict] = {}
    for row in docs:
        name = row.get("DocName") or ""
        row_as_of = fortnightly_doc_to_as_of(name)
        if not row_as_of:
            continue
        if token and not name.upper().startswith(token):
            continue
        if as_of and row_as_of != as_of:
            continue
        mk = row_as_of[:7]
        if mk not in want:
            continue
        prev = chosen.get(mk)
        if not prev or name > (prev.get("DocName") or ""):
            chosen[mk] = row
    return chosen


def download(url: str, *, referer: str = REFERER) -> bytes:
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
        description="Fetch Bank of India MF monthly portfolio (consolidated xlsx per month)"
    )
    parser.add_argument(
        "--months",
        nargs="+",
        default=["2026-01", "2026-02"],
        help="Months as YYYY-MM",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="mf-monthly-holdings root",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fortnightly", action="store_true", help="Fetch fortnightly debt portfolios when supported")
    parser.add_argument(
        "--as-of",
        default="",
        help="Calendar as-of YYYY-MM-DD for fortnightly consolidated workbook",
    )
    args = parser.parse_args()

    amc_dir = args.root / "amcs" / "bank-of-india-mutual-fund"
    as_of = args.as_of.strip()
    if args.fortnightly and not as_of and args.months:
        as_of = f"{args.months[0]}-15"

    if args.fortnightly:
        print(f"POST RGetDocuments (Reports / FORTNIGHTLY PORTFOLIO OF DEBT SCHEMES)…")
        docs = fetch_fortnightly_index()
        print(f"  … {len(docs)} row(s) in index", flush=True)
        selected = pick_fortnightly_for_months(docs, list(args.months), as_of)
        referer = FORTNIGHTLY_REFERER
        label = f"fortnightly as_of={as_of}" if as_of else "fortnightly"
    else:
        print("POST GetDocuments (InvestorCorner / MONTHLY PORTFOLIO)…")
        docs = fetch_document_index()
        print(f"  … {len(docs)} row(s) in index", flush=True)
        selected = pick_for_months(docs, list(args.months))
        referer = REFERER
        label = "monthly"

    for month_key in args.months:
        out_dir = amc_dir / month_key
        out_dir.mkdir(parents=True, exist_ok=True)

        row = selected.get(month_key)
        print(f"\n{month_key} [{label}]:", end=" ")
        if not row:
            print("no matching row.")
            manifest: list[dict] = []
            man_path = out_dir / "manifest.json"
            man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            continue

        file_url = (row.get("FolderUrl") or "").strip()
        title = row.get("DocName") or ""
        fname = safe_filename(file_url) if file_url else "fortnightly-portfolio.xlsx"

        rec = {
            "month": month_key,
            "as_of": fortnightly_doc_to_as_of(title) if args.fortnightly else None,
            "download_url": file_url,
            "saved_as": fname,
            "title": title,
            "folder_id": row.get("FolderID"),
        }

        if args.dry_run:
            print(f"would save {fname}")
            manifest = [{**rec, "sha256": "", "dry_run": True}]
        else:
            try:
                body = download(file_url, referer=referer)
                h = hashlib.sha256(body).hexdigest()
                dest = out_dir / fname
                dest.write_bytes(body)
                manifest = [{**rec, "sha256": h}]
                print(f"OK {fname} ({len(body)} bytes)")
            except Exception as e:
                manifest = [{**rec, "sha256": "", "error": str(e)}]
                print(f"ERR: {e}")

        man_path = out_dir / "manifest.json"
        man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Wrote {man_path}")


if __name__ == "__main__":
    main()
