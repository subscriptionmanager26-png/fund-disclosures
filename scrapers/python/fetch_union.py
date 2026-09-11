#!/usr/bin/env python3
"""
Union Mutual Fund — download monthly portfolio spreadsheets for YYYY-MM.

Primary source: HTML of
  https://www.unionmf.com/about-us/downloads/monthly-portfolio
which embeds absolute CDN links under
  /docs/default-source/funddetail-downloads/fund-portfolio/<month>-<year>/...

Fallback: OData folder API (often only latest month) + optional next-month URL derivation.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urljoin
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import asof_filter

FOLDER_ID = "b6cafa81-47fb-4935-bc54-b752b9e7d797"
API_URL = (
    "https://www.unionmf.com/api/downloads/documents"
    f"?$filter=FolderId%20eq%20{FOLDER_ID}&$orderby=Yearfilter%20desc"
)
BASE = "https://www.unionmf.com/"
PAGE_URL = "https://www.unionmf.com/about-us/downloads/monthly-portfolio"
FORTNIGHTLY_PAGE_URL = "https://www.unionmf.com/about-us/downloads/fortnightly-portfolio"
REFERER = PAGE_URL

MONTH_SLUG = {
    "01": "january",
    "02": "february",
    "03": "march",
    "04": "april",
    "05": "may",
    "06": "june",
    "07": "july",
    "08": "august",
    "09": "september",
    "10": "october",
    "11": "november",
    "12": "december",
}

URL_RE = re.compile(
    r"https?://www\.unionmf\.com/docs/default-source/funddetail-downloads/"
    r"fund-portfolio/([a-z]+-\d{4})/[^\"'\s<>]+\.(?:xlsx|xls|xlsb)(?:\?[^\"'\s<>]*)?",
    re.I,
)

FORTNIGHTLY_URL_RE = re.compile(
    r"https?://www\.unionmf\.com/docs/default-source/downloads/"
    r"scheme-disclosures/portfolios-disclosure/fortnightly-portfolio/"
    r"[^\"'\s<>]+\.(?:xlsx|xls|xlsb)(?:\?[^\"'\s<>]*)?",
    re.I,
)

DATE_TAIL_RE = re.compile(
    r"-(\d{2})-(\d{2})-(\d{4})(\.(?:xlsx|xls|xlsb|pdf))$",
    re.I,
)

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _http_get(url: str, *, referer: str, accept: str = "*/*") -> bytes:
    try:
        from curl_cffi import requests as creq  # type: ignore

        r = creq.get(
            url,
            headers={**HTTP_HEADERS, "Accept": accept, "Referer": referer},
            impersonate="chrome131",
            timeout=120,
        )
        r.raise_for_status()
        return r.content
    except Exception:
        req = Request(
            url,
            headers={**HTTP_HEADERS, "Accept": accept, "Referer": referer},
        )
        with urlopen(req, timeout=120) as resp:
            return resp.read()


def month_key_to_path_segment(month_key: str) -> str:
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    year, mm = parts[0], parts[1].zfill(2)
    slug = MONTH_SLUG.get(mm)
    if not slug or not year.isdigit():
        raise ValueError(f"Bad month key {month_key!r}")
    return f"{slug}-{year}"


def next_month_key(month_key: str) -> str:
    y_s, m_s = month_key.strip().split("-")
    y, m = int(y_s, 10), int(m_s, 10)
    if m == 12:
        return f"{y + 1}-01"
    return f"{y}-{m + 1:02d}"


def split_path_query(url: str) -> tuple[str, str]:
    if "?" in url:
        p, q = url.split("?", 1)
        return p, "?" + q
    return url, ""


def safe_filename(url_path: str) -> str:
    base = url_path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    if not base or base in (".", ".."):
        base = "download.bin"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.bin"


def load_page_docs(page_url: str, url_re: re.Pattern[str]) -> list[dict]:
    html = _http_get(
        page_url,
        referer=BASE,
        accept="text/html,application/xhtml+xml,*/*",
    ).decode("utf-8", "ignore")
    docs: list[dict] = []
    seen: set[str] = set()
    for m in url_re.finditer(html):
        url = m.group(0)
        seg = m.group(1).lower() if m.lastindex else ""
        path = url.split("?")[0]
        if path in seen:
            continue
        seen.add(path)
        fname = unquote(path.rsplit("/", 1)[-1])
        docs.append(
            {
                "Url": url,
                "Title": fname,
                "Extension": "." + fname.rsplit(".", 1)[-1].lower(),
                "Id": None,
                "PublicationDate": None,
                "_segment": seg,
                "_source": "html",
            }
        )
    return docs


def load_fortnightly_docs(as_of: str | None) -> list[dict]:
    docs = load_page_docs(FORTNIGHTLY_PAGE_URL, FORTNIGHTLY_URL_RE)
    if not as_of:
        return docs
    return [d for d in docs if asof_filter.filename_matches_asof(str(d.get("Url") or ""), as_of)]


def load_api_docs() -> list[dict]:
    raw = _http_get(
        API_URL,
        referer=REFERER,
        accept="application/json, text/plain, */*",
    ).decode("utf-8", "ignore")
    data = json.loads(raw)
    val = data.get("value") if isinstance(data, dict) else None
    if not isinstance(val, list):
        raise RuntimeError("Unexpected API shape")
    return val


def is_monthly_portfolio_for_segment(doc: dict, path_segment: str) -> bool:
    url = str(doc.get("Url") or "")
    title = str(doc.get("Title") or "")
    blob = f"{url} {title}".lower()
    if "fortnight" in blob or "weekly" in blob:
        return False
    seg = path_segment.lower()
    if doc.get("_segment"):
        return doc["_segment"] == seg and re.search(r"\.(xlsx|xls|xlsb)(\?|$)", url, re.I)
    if f"/fund-portfolio/{seg}/" not in url.lower():
        return False
    if title and not title.lower().startswith("monthly portfolio"):
        # API titles start with Monthly Portfolio Report; HTML titles are filenames
        if "monthly-portfolio" not in url.lower() and "monthly portfolio" not in title.lower():
            return False
    ext = str(doc.get("Extension") or "").lower()
    if ext and ext not in (".xlsx", ".xls", ".xlsb", ".pdf"):
        return False
    return True


def derive_docs_from_reference_month(
    all_docs: list[dict],
    target_month_key: str,
    ref_month_key: str,
) -> list[dict]:
    ref_seg = month_key_to_path_segment(ref_month_key)
    tgt_seg = month_key_to_path_segment(target_month_key)
    ty, tm = (int(x) for x in target_month_key.split("-"))
    last_day = calendar.monthrange(ty, tm)[1]
    ref_rows = [d for d in all_docs if is_monthly_portfolio_for_segment(d, ref_seg)]
    derived: list[dict] = []
    seg_pat = re.compile(r"/fund-portfolio/" + re.escape(ref_seg) + "/", re.I)
    for d in ref_rows:
        rel = str(d.get("Url") or "").strip()
        if not rel:
            continue
        path, qs = split_path_query(rel)
        path_new = seg_pat.sub(f"/fund-portfolio/{tgt_seg}/", path, count=1)
        if path_new == path:
            continue
        m = DATE_TAIL_RE.search(path_new)
        if not m:
            continue
        ext = m.group(4)
        new_suffix = f"-{last_day:02d}-{tm:02d}-{ty}{ext.lower()}"
        new_rel = path_new[: m.start()] + new_suffix + qs
        derived.append(
            {
                "Url": new_rel,
                "Title": d.get("Title"),
                "Extension": ext.lower(),
                "Id": None,
                "PublicationDate": None,
                "_derived_from_month": ref_month_key,
                "_derived_from_url": rel,
            }
        )
    return derived


def download(url: str, referer: str = REFERER) -> bytes:
    return _http_get(url, referer=referer, accept="*/*")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Union MF monthly portfolio files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-derive-from-next-month", action="store_true")
    parser.add_argument(
        "--fortnightly",
        action="store_true",
        help="Fetch fortnightly portfolio files (mid-month / month-end by --as-of)",
    )
    parser.add_argument(
        "--as-of",
        default="",
        help="Calendar as-of YYYY-MM-DD (filters fortnightly filenames)",
    )
    args = parser.parse_args()

    amc_dir = args.root / "amcs" / "union-mutual-fund"

    if args.fortnightly:
        as_of = args.as_of.strip() or None
        print(f"GET {FORTNIGHTLY_PAGE_URL}", flush=True)
        try:
            page_docs = load_fortnightly_docs(as_of)
            if not as_of and args.months:
                as_of = asof_filter.default_as_of_for_month(args.months[0], fortnightly=True)
                page_docs = load_fortnightly_docs(as_of)
            print(
                f"  HTML indexed {len(page_docs)} fortnightly link(s)"
                + (f" for as_of={as_of}" if as_of else ""),
                flush=True,
            )
        except Exception as e:
            print(f"  HTML scrape failed: {e}", flush=True)
            page_docs = []
        api_docs: list[dict] = []

        for month_key in args.months:
            selected = list(page_docs)
            if args.limit > 0:
                selected = selected[: args.limit]

            out_dir = amc_dir / month_key
            out_dir.mkdir(parents=True, exist_ok=True)
            manifest: list[dict] = []
            print(
                f"\n{month_key} [fortnightly as_of={as_of}]: {len(selected)} file(s)",
                flush=True,
            )

            for i, doc in enumerate(selected, 1):
                rel = str(doc.get("Url") or "").strip()
                file_url = rel if rel.startswith("http") else urljoin(BASE, rel)
                fname = safe_filename(rel)
                rec = {
                    "month": month_key,
                    "as_of": as_of,
                    "download_url": file_url,
                    "saved_as": fname,
                    "Title": doc.get("Title"),
                    "source": "html_fortnightly",
                }
                if args.dry_run:
                    print(f"  [{i}] {fname}", flush=True)
                    manifest.append({**rec, "dry_run": True})
                    continue
                try:
                    body = download(file_url, FORTNIGHTLY_PAGE_URL)
                    (out_dir / fname).write_bytes(body)
                    print(f"  [{i}] OK {fname} ({len(body)} bytes)", flush=True)
                    manifest.append(
                        {**rec, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
                    )
                except Exception as e:
                    print(f"  [{i}] ERR {fname}: {e}", flush=True)
                    manifest.append({**rec, "error": str(e)})

            (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            print(f"Wrote {out_dir / 'manifest.json'}", flush=True)
        return

    print(f"GET {PAGE_URL}", flush=True)
    try:
        page_docs = load_page_docs(PAGE_URL, URL_RE)
        print(f"  HTML indexed {len(page_docs)} portfolio link(s)", flush=True)
    except Exception as e:
        print(f"  HTML scrape failed: {e}", flush=True)
        page_docs = []

    api_docs: list[dict] = []
    try:
        api_docs = load_api_docs()
        print(f"  API indexed {len(api_docs)} document(s)", flush=True)
    except Exception as e:
        print(f"  API load failed: {e}", flush=True)

    for month_key in args.months:
        segment = month_key_to_path_segment(month_key)
        selected = [d for d in page_docs if is_monthly_portfolio_for_segment(d, segment)]
        source_note = "html"

        if not selected:
            selected = [d for d in api_docs if is_monthly_portfolio_for_segment(d, segment)]
            source_note = "api"

        if not selected and not args.no_derive_from_next_month:
            ref_key = next_month_key(month_key)
            pool = page_docs or api_docs
            try:
                derived = derive_docs_from_reference_month(pool, month_key, ref_key)
            except ValueError:
                derived = []
            if derived:
                selected = derived
                source_note = f"derived from {ref_key}"

        selected.sort(key=lambda d: (d.get("Title") or d.get("Url") or ""))
        if args.limit > 0:
            selected = selected[: args.limit]

        out_dir = amc_dir / month_key
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest: list[dict] = []
        print(f"\n{month_key} ({segment}) [{source_note}]: {len(selected)} file(s)", flush=True)

        for i, doc in enumerate(selected, 1):
            rel = str(doc.get("Url") or "").strip()
            file_url = rel if rel.startswith("http") else urljoin(BASE, rel)
            fname = safe_filename(rel)
            rec = {
                "month": month_key,
                "path_segment": segment,
                "download_url": file_url,
                "saved_as": fname,
                "Title": doc.get("Title"),
                "source": source_note,
            }
            if args.dry_run:
                print(f"  [{i}] {fname}", flush=True)
                manifest.append({**rec, "dry_run": True})
                continue
            try:
                body = download(file_url)
                (out_dir / fname).write_bytes(body)
                print(f"  [{i}] OK {fname} ({len(body)} bytes)", flush=True)
                manifest.append({**rec, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)})
            except Exception as e:
                print(f"  [{i}] ERR {fname}: {e}", flush=True)
                manifest.append({**rec, "error": str(e)})

        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
