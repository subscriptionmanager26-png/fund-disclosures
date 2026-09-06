#!/usr/bin/env python3
"""
Groww Mutual Fund — download consolidated monthly portfolio workbook(s) for given YYYY-MM.

Source page embeds JSON in `__NEXT_DATA__` with tree:
  props.pageProps.filesData

Monthly portfolio files live under folder path containing "Portfolio" and have names like:
  Monthly Portfolio- Jan 31, 2026.xlsx

We intentionally skip fortnightly files and keep only records with "Monthly Portfolio" in file name.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from disclosure_date import year_month_key

PAGE_URL = "https://growwmf.in/statutory-disclosure/portfolio"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>',
    re.S,
)


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    if not base or base in (".", ".."):
        base = "download.bin"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.bin"


def fetch_next_data() -> dict:
    req = Request(PAGE_URL, headers=HEADERS)
    with urlopen(req, timeout=120) as resp:
        html = resp.read().decode("utf-8", "ignore")
    m = NEXT_DATA_RE.search(html)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:
        return {}


def infer_month_key(name: str) -> str | None:
    return year_month_key(name or "")


def collect_monthly_files(next_data: dict) -> list[dict]:
    files_data = (((next_data.get("props") or {}).get("pageProps") or {}).get("filesData") or {})
    out: list[dict] = []

    def walk(node: dict, path: list[str]) -> None:
        if not isinstance(node, dict):
            return
        name = str(node.get("name") or "")
        files = node.get("files") or []
        for f in files:
            if not isinstance(f, dict):
                continue
            fname = str(f.get("name") or "")
            url = str(f.get("publicUrl") or "").strip()
            if not url:
                continue
            # keep only monthly portfolio files, skip fortnightly and unrelated docs
            if "monthly portfolio" not in fname.lower():
                continue
            if "fortnightly" in fname.lower():
                continue
            if not re.search(r"\.(xlsx|xls)(\?|$)", url, re.I):
                continue

            mk = infer_month_key(fname)
            out.append(
                {
                    "folder_path": " / ".join([p for p in path + [name] if p]),
                    "name": fname,
                    "download_url": url,
                    "month_key": mk,
                }
            )

        for sub in node.get("folders") or []:
            walk(sub, path + [name])

    walk(files_data, [])

    # dedupe by URL
    dedup: dict[str, dict] = {}
    for rec in out:
        dedup[rec["download_url"]] = rec
    return list(dedup.values())


def download(url: str) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": HEADERS["User-Agent"],
            "Accept": "*/*",
            "Referer": PAGE_URL,
        },
    )
    with urlopen(req, timeout=120) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Groww MF monthly portfolio files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="mf-monthly-holdings root",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    amc_dir = args.root / "amcs" / "groww-mutual-fund"

    print(f"GET {PAGE_URL} …")
    nd = fetch_next_data()
    rows = collect_monthly_files(nd)
    print(f"  … found {len(rows)} monthly portfolio file row(s)")

    by_month: dict[str, list[dict]] = {m: [] for m in args.months}
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
            print("  No matching monthly rows found")

        for i, row in enumerate(batch, 1):
            url = row["download_url"]
            fname = safe_filename(url)
            rec = {
                "month": mk,
                "download_url": url,
                "saved_as": fname,
                "title": row.get("name"),
                "folder_path": row.get("folder_path"),
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
