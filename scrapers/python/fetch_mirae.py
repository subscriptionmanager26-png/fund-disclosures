#!/usr/bin/env python3
"""
Mirae Asset Mutual Fund — download monthly portfolio files for given YYYY-MM.

Source page:
  https://www.miraeassetmf.co.in/downloads/portfolio

The page script calls:
  POST https://www.miraeassetmf.co.in/AjaxService/GetDownloadsData
with RSA-encrypted JSON:
  {"modulename":"portfolio_tab1","pgno":<n>,"pgsize":<m>}

Response shape:
  {
    "Data": [{"Title","URL","PublishDate", ...}, ...],
    "DataCount": <int>,
    "ReturnCode": 0
  }
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

BASE = "https://www.miraeassetmf.co.in"
PAGE_URL = f"{BASE}/downloads/portfolio"
API_URL = f"{BASE}/AjaxService/GetDownloadsData"

PUBLIC_KEY_B64 = "MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBAOvAjm2G3Izal1C+7/KmbtYPjmpoddfexaNNl4Yc1KZCF72oczL8J6yq534Thl7/ViLr0a14e3x8+w7YzI/8cfkCAwEAAQ=="

HEADERS_JSON = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json;charset=utf-8",
    "Origin": BASE,
    "Referer": PAGE_URL,
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
    base = name.strip() or "mirae_portfolio.xlsx"
    base = re.sub(r"[^\w.\-() ]+", "_", base).strip("._ ")
    return (base[:200] or "mirae_portfolio.xlsx")


def parse_publish_month(value: str) -> tuple[int, int] | None:
    m = re.search(r"/Date\((\d+)\)/", value or "")
    if not m:
        return None
    ts_ms = int(m.group(1))
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.year, dt.month


MONTH_TOKEN_TO_NUM = {
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


def parse_portfolio_month_from_text(text: str) -> tuple[int, int] | None:
    s = (text or "").lower()
    # Common patterns: "-jan2026", "-jan-2026", "January 2026", "as on 31st january 2026".
    m = re.search(
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"[\s\-_]*([12]\d{3})",
        s,
    )
    if not m:
        return None
    token = m.group(1)
    year = int(m.group(2))
    month = MONTH_TOKEN_TO_NUM.get(token)
    if not month:
        return None
    return year, month


def build_download_url(raw_url: str) -> str:
    if not raw_url:
        return ""
    if raw_url.startswith("http://") or raw_url.startswith("https://"):
        return raw_url
    return urljoin(BASE, raw_url)


def _load_encryptor():
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except Exception as e:
        raise SystemExit(
            "Missing dependency: cryptography\n"
            "Install with:\n"
            "  /Users/kushagraagarwal/mf-monthly-holdings/.venv/bin/pip install cryptography\n"
            "or:\n"
            "  python3 -m pip install cryptography"
        ) from e

    der = base64.b64decode(PUBLIC_KEY_B64)
    pub = serialization.load_der_public_key(der, backend=default_backend())
    return pub, padding


def encrypt_request_payload(payload: dict) -> str:
    pub, padding = _load_encryptor()
    s = json.dumps(payload, separators=(",", ":"))
    out = ""
    while s:
        chunk = s[:22]
        s = s[22:]
        block = pub.encrypt(chunk.encode("utf-8"), padding.PKCS1v15())
        out += base64.b64encode(block).decode("ascii")
    return out


def call_get_downloads(
    pgno: int,
    pgsize: int,
    *,
    ctx: ssl.SSLContext,
    modulename: str = "portfolio_tab1",
) -> dict:
    """POST GetDownloadsData.

    As of 2026-09 the site accepts the plain request object (RSA EncryptFunction
    responses come back as ``Request is null or empty``). Keep
    ``encrypt_request_payload`` for debugging older builds only.

    ``modulename`` tabs on /downloads/portfolio:
      portfolio_tab1 = Monthly Portfolio (default)
      portfolio_tab3 = Fortnightly Portfolio  ← do not use for monthly syncs
    """
    payload = {"modulename": modulename, "pgno": pgno, "pgsize": pgsize}
    body = json.dumps({"request": payload}, separators=(",", ":")).encode("utf-8")
    headers = {
        **HEADERS_JSON,
        "Content-Type": "application/json;charset=utf-8",
    }
    req = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def fetch_all_rows(
    *,
    page_size: int,
    ctx: ssl.SSLContext,
    modulename: str = "portfolio_tab1",
) -> list[dict]:
    out: list[dict] = []
    page = 1
    total = None
    while True:
        data = call_get_downloads(page, page_size, ctx=ctx, modulename=modulename)
        rc = str(data.get("ReturnCode", ""))
        if rc != "0":
            raise RuntimeError(
                f"GetDownloadsData ReturnCode={data.get('ReturnCode')} "
                f"msg={data.get('ReturnMsg')}"
            )
        rows = data.get("Data") or []
        if not isinstance(rows, list) or not rows:
            break
        out.extend([r for r in rows if isinstance(r, dict)])
        if total is None:
            try:
                total = int(data.get("DataCount", 0))
            except Exception:
                total = 0
        if total and len(out) >= total:
            break
        page += 1
    return out


def download(url: str, *, ctx: ssl.SSLContext) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": HEADERS_JSON["User-Agent"],
            "Accept": "*/*",
            "Referer": PAGE_URL,
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Mirae Asset MF monthly portfolio files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument("--page-size", type=int, default=500, help="API page size (default: 500)")
    parser.add_argument(
        "--modulename",
        default="portfolio_tab1",
        help="Downloads tab id: portfolio_tab1=monthly (default), portfolio_tab3=fortnightly",
    )
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
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    targets = {month_key_to_ym(mk): mk for mk in args.months}
    amc_dir = args.root / "amcs" / "mirae-asset-mutual-fund"
    modulename = (args.modulename or "portfolio_tab1").strip()
    if modulename == "portfolio_tab3":
        print(
            "NOTE: portfolio_tab3 is Fortnightly Portfolio — "
            "do not mix with monthly as-of syncs.",
            flush=True,
        )

    print(f"POST {API_URL} (modulename={modulename}) …", flush=True)
    try:
        rows = fetch_all_rows(page_size=args.page_size, ctx=ctx, modulename=modulename)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_mirae.py ... --insecure-ssl"
            ) from e
        raise
    print(f"  Indexed {len(rows)} rows from portfolio_tab1", flush=True)

    rows_by_month: dict[tuple[int, int], list[dict]] = {}
    seen_url: set[str] = set()
    for row in rows:
        title = str(row.get("Title", "")).strip()
        url = build_download_url(str(row.get("URL", "")).strip())
        ym = (
            parse_portfolio_month_from_text(urlparse(url).path)
            or parse_portfolio_month_from_text(title)
            or parse_publish_month(str(row.get("PublishDate", "")))
        )
        if ym is None or ym not in targets:
            continue
        if not url or url in seen_url:
            continue
        seen_url.add(url)
        rows_by_month.setdefault(ym, []).append(row)

    for ym, mk in targets.items():
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in out_dir.iterdir():
            if p.is_file():
                p.unlink()
        manifest: list[dict] = []
        selected = rows_by_month.get(ym, [])
        print(f"\n{mk}: {len(selected)} file(s)", flush=True)
        if not selected:
            print("  No rows found for this month.", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue
        for row in selected:
            title = str(row.get("Title", "")).strip()
            url = build_download_url(str(row.get("URL", "")).strip())
            parsed_name = Path(urlparse(url).path).name
            fn = safe_filename(parsed_name or f"{title}.xlsx")
            rec = {
                "month": mk,
                "title": title,
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
