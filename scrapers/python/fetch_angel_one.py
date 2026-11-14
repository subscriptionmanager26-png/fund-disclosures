#!/usr/bin/env python3
"""
Angel One Mutual Fund - download monthly portfolio files for YYYY-MM.

Source page:
  https://www.angelonemf.com/downloads

Data source:
  Next.js SSR payload in `self.__next_f.push([1, "..."])` chunks. One chunk
  contains:
    f:["$","$L1a",null,{"investorsData":{...},"disclosuresData":{...},...}]
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
from urllib.parse import unquote, urljoin, urlparse

BASE = "https://www.angelonemf.com"
PAGE_URL = f"{BASE}/downloads"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
PUSH_RE = re.compile(
    r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)',
    re.DOTALL,
)
MONTH_ORDER = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
EXT_FILTER = {"xls", "xlsx", "pdf"}


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
    s = (name or "").strip() or "angel_one_monthly_portfolio.xlsx"
    s = re.sub(r"[^\w.\-() ]+", "_", s).strip("._ ")
    return s[:220] or "angel_one_monthly_portfolio.xlsx"


def fetch_html(*, ctx: ssl.SSLContext) -> str:
    req = urllib.request.Request(PAGE_URL, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def extract_props(html_text: str) -> dict:
    """Return the React props object that carries ``disclosuresData``.

    Next.js RSC payload: ``self.__next_f.push([1, "<escaped>"])`` chunks. Older
    builds put the downloads props in their own chunk; since Nov 2026 the whole
    tree is one chunk whose FIRST ``null,{...}`` object is the root layout
    (``{"lang", "children"}``), so taking the first object silently returned no
    rows. Decode every ``null,{`` candidate with a real JSON decoder (string-
    aware, unlike brace counting) and keep the one that has ``disclosuresData``.
    """
    decoder = json.JSONDecoder()
    for m in PUSH_RE.finditer(html_text):
        raw = m.group(1)
        if "disclosuresData" not in raw:
            continue

        try:
            unescaped = json.loads('"' + raw + '"')
        except json.JSONDecodeError:
            unescaped = raw.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")

        for cand in re.finditer(r"null,(\{)", unescaped):
            try:
                obj, _end = decoder.raw_decode(unescaped, cand.start(1))
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "disclosuresData" in obj:
                return obj

        # Fallback: the key may sit deeper than a props object we can anchor on.
        for key in re.finditer(r'"disclosuresData"\s*:', unescaped):
            try:
                val, _end = decoder.raw_decode(unescaped, key.end() + (len(unescaped[key.end():]) - len(unescaped[key.end():].lstrip())))
            except json.JSONDecodeError:
                continue
            if isinstance(val, (dict, list)):
                return {"disclosuresData": val}

    raise ValueError("Could not find disclosuresData in __next_f payload")


def parse_month_label(label: str) -> tuple[int, int] | None:
    text = (label or "").strip()
    ym = re.search(r"(20\d{2})", text)
    if not ym:
        return None
    year = int(ym.group(1))
    low = text.lower()
    for name, num in MONTH_ORDER.items():
        if name in low:
            return year, num
    return None


def parse_ym_from_url(url: str) -> tuple[int, int] | None:
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1]).lower()
    m = re.search(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[-_ ]*(20\d{2})",
        name,
        re.I,
    )
    if not m:
        return None
    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    mon = month_map.get(m.group(1).lower())
    if not mon:
        return None
    return int(m.group(2)), mon


def extract_rows(html_text: str) -> list[dict]:
    props = extract_props(html_text)
    disclosures = props.get("disclosuresData", {})
    rows: list[dict] = []
    seen: set[str] = set()

    for record in disclosures.values():
        fields = (record or {}).get("fields", {})
        if fields.get("Sub_Section") != "Portfolio Disclosures":
            continue
        portfolio_type = str(fields.get("Dropdown", "")).strip().lower()
        # Dropdown may be "Monthly" or null for older combined dumps; skip half-yearly.
        if portfolio_type and "monthly" not in portfolio_type:
            continue
        if "half" in portfolio_type:
            continue

        month_label = str(fields.get("Dropdown2") or "").strip()
        ym = parse_month_label(month_label) if month_label else None

        urls = fields.get("post_guid", []) or []
        for u in urls:
            raw_url = str(u).strip()
            if not raw_url:
                continue
            ext = raw_url.rsplit(".", 1)[-1].lower().split("?")[0]
            if ext not in EXT_FILTER:
                continue
            # Only scheme monthly portfolios (not AAUM / other monthly dumps).
            path_name = unquote(urlparse(raw_url).path.rsplit("/", 1)[-1])
            if not re.search(r"monthly[-_\s]*portfolio", path_name, re.I):
                continue
            if re.search(r"\baaum\b|half[-_\s]*year", path_name, re.I):
                continue

            url = raw_url if raw_url.startswith("http") else urljoin(BASE + "/", raw_url)
            url_ym = parse_ym_from_url(url)
            # Newer CMS rows often leave Dropdown2 empty — take month from filename.
            row_ym = ym or url_ym
            if row_ym is None:
                continue
            if ym is not None and url_ym is not None and ym != url_ym:
                continue
            key = f"{row_ym[0]}-{row_ym[1]}|{url}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "year": row_ym[0],
                    "month": row_ym[1],
                    "title": month_label
                    or f"{row_ym[0]}-{row_ym[1]:02d}",
                    "url": url,
                }
            )
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
    parser = argparse.ArgumentParser(description="Fetch Angel One monthly portfolio files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--insecure-ssl",
        action="store_true",
        help="Disable TLS verification if your Python lacks CA certs",
    )
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "angel-one-mutual-fund"
    targets = {month_key_to_ym(mk): mk for mk in args.months}

    print(f"GET {PAGE_URL}", flush=True)
    try:
        html_text = fetch_html(ctx=ctx)
        rows = extract_rows(html_text)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_angel_one.py ... --insecure-ssl"
            ) from e
        raise
    print(f"  Indexed {len(rows)} monthly row(s) from __next_f payload", flush=True)

    by_month: dict[tuple[int, int], list[dict]] = {}
    for row in rows:
        ym = (row["year"], row["month"])
        if ym in targets:
            by_month.setdefault(ym, []).append(row)

    for ym, mk in targets.items():
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        if not args.dry_run:
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
            fn = safe_filename(raw_name or f"angel-one-monthly-portfolio-{mk}.xlsx")
            rec = {
                "month": mk,
                "title": title,
                "source_page": PAGE_URL,
                "download_url": url,
                "saved_as": fn,
            }
            if args.dry_run:
                manifest.append({**rec, "sha256": "", "dry_run": True})
                print(f"  would save {fn}", flush=True)
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

        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"  Wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
