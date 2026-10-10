#!/usr/bin/env python3
"""
Bajaj Finserv Mutual Fund — download monthly portfolio .xls/.xlsx for YYYY-MM.

Uses WordPress REST media search. Files are per-scheme, e.g.:
  bajaj-finserv-small-cap-fund_monthly-portfolio-as-on-30-jun-2026-xls
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

API_SEARCH = "https://www.bajajamc.com/wp-json/wp/v2/media?search={q}&per_page=100"
REFERER = "https://www.bajajamc.com/downloads"

MON_ABBREV = {
    "01": "jan",
    "02": "feb",
    "03": "mar",
    "04": "apr",
    "05": "may",
    "06": "jun",
    "07": "jul",
    "08": "aug",
    "09": "sep",
    "10": "oct",
    "11": "nov",
    "12": "dec",
}


def month_search_key(month_key: str) -> tuple[str, str]:
    """2026-06 -> ('as-on-30-jun-2026', slug fragment)."""
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    y = int(parts[0])
    m = int(parts[1])
    last = calendar.monthrange(y, m)[1]
    mon = MON_ABBREV[f"{m:02d}"]
    frag = f"as-on-{last}-{mon}-{y}"
    return frag, frag


def month_search_fragments(month_key: str) -> list[str]:
    """
    WP media slugs for Bajaj AMC sometimes omit the hyphen before the year
    (e.g. as-on-30-sep2026 vs as-on-30-sep-2026). Search all variants.
    """
    frag, _ = month_search_key(month_key)
    frags = [frag]
    m = re.match(r"^as-on-(\d+)-([a-z]+)-(\d{4})$", frag)
    if m:
        day, mon, year = m.group(1), m.group(2), m.group(3)
        frags.append(f"as-on-{day}-{mon}{year}")
        if mon == "sep":
            frags.append(f"as-on-{day}-september-{year}")
    seen: set[str] = set()
    out: list[str] = []
    for f in frags:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def fortnightly_search_key(month_key: str, as_of: str = "") -> str:
    """Mid-month fortnightly slug fragment, e.g. as-on-15-jul-2026."""
    if as_of:
        parts = as_of.strip().split("-")
        if len(parts) == 3:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            mon = MON_ABBREV[f"{m:02d}"]
            return f"as-on-{d:02d}-{mon}-{y}"
    parts = month_key.strip().split("-")
    y, m = int(parts[0]), int(parts[1])
    mon = MON_ABBREV[f"{m:02d}"]
    return f"as-on-15-{mon}-{y}"


def safe_filename(url: str, title: str = "") -> str:
    path = urlparse(url).path
    base = unquote(path.rsplit("/", 1)[-1].split("?")[0])
    if not base or base in (".", ".."):
        base = re.sub(r"[^\w.\-() ]", "_", title)[:180] or "download.bin"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.bin"


def fetch_media_search(q: str) -> list[dict]:
    url = API_SEARCH.format(q=quote(q))
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": REFERER,
        },
    )
    with urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    return data if isinstance(data, list) else []


def pick_download_url(item: dict) -> str | None:
    src = item.get("source_url")
    if isinstance(src, str) and src.startswith("http"):
        return src
    guid = (item.get("guid") or {}).get("rendered") or ""
    if guid.startswith("http"):
        return guid
    desc = (item.get("description") or {}).get("rendered") or ""
    m = re.search(r'href="(https?://[^"]+)"', desc)
    return m.group(1) if m else None


def is_monthly_for(item: dict, frag: str) -> bool:
    slug = str(item.get("slug") or "")
    title = (item.get("title") or {}).get("rendered") or ""
    url = pick_download_url(item) or ""
    blob = f"{slug} {title} {url}".lower()
    norm_blob = blob.replace("_", "-")
    if frag not in norm_blob:
        # Bajaj slugs sometimes use as-on-30-sep2026 (no hyphen before year)
        alt = re.sub(r"-(\d{4})$", r"\1", frag)
        alt2 = frag.replace("-", "_")
        if alt not in norm_blob and alt2 not in norm_blob:
            return False
    if "monthly" not in blob and "portfolio" not in blob:
        return False
    if "fortnightly" in blob:
        return False
    if not re.search(r"\.(xlsx?|xls)(\?|$)", url, re.I) and item.get("mime_type", "") not in (
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel.sheet.macroEnabled.12",
    ):
        # still accept if mime says spreadsheet
        mt = (item.get("mime_type") or "").lower()
        if "excel" not in mt and "spreadsheet" not in mt and "sheet" not in mt:
            return False
    return True


def is_fortnightly_for(item: dict, frag: str) -> bool:
    slug = str(item.get("slug") or "")
    title = (item.get("title") or {}).get("rendered") or ""
    url = pick_download_url(item) or ""
    blob = f"{slug} {title} {url}".lower()
    if frag not in blob.replace("_", "-"):
        alt = frag.replace("-", "_")
        if alt not in blob:
            return False
    if "fortnightly" not in blob:
        return False
    if not re.search(r"\.(xlsx?|xls)(\?|$)", url, re.I):
        mt = (item.get("mime_type") or "").lower()
        if "excel" not in mt and "spreadsheet" not in mt and "sheet" not in mt:
            return False
    return True


def download(url: str) -> bytes:
    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Referer": REFERER},
    )
    with urlopen(req, timeout=120) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Bajaj Finserv monthly/fortnightly portfolios")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--fortnightly",
        action="store_true",
        help="Fetch mid-month fortnightly portfolios (day 15 by default, or --as-of)",
    )
    parser.add_argument(
        "--as-of",
        default="",
        help="Calendar as-of YYYY-MM-DD for fortnightly filename matching",
    )
    args = parser.parse_args()

    amc_dir = args.root / "amcs" / "bajaj-finserv-mutual-fund"
    label = "fortnightly" if args.fortnightly else "monthly"

    for month_key in args.months:
        if args.fortnightly:
            search_frags = [fortnightly_search_key(month_key, args.as_of)]
            matcher = is_fortnightly_for
        else:
            search_frags = month_search_fragments(month_key)
            matcher = is_monthly_for

        out_dir = amc_dir / month_key
        out_dir.mkdir(parents=True, exist_ok=True)

        # de-dupe by url across all search fragments
        seen: set[str] = set()
        uniq = []
        for frag in search_frags:
            for it in fetch_media_search(frag):
                if not matcher(it, frag):
                    # also accept sibling slug variant (sep vs sep2026)
                    if not any(matcher(it, alt) for alt in search_frags):
                        continue
                u = pick_download_url(it)
                if not u or u in seen:
                    continue
                seen.add(u)
                uniq.append(it)

        print(
            f"\n{month_key} {label} search={search_frags!r}: {len(uniq)} media object(s)",
        )
        manifest: list[dict] = []
        if not uniq:
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            print("  No attachment found")
            continue

        for item in uniq:
            file_url = pick_download_url(item)
            assert file_url
            title = (item.get("title") or {}).get("rendered") or ""
            fname = safe_filename(file_url, title)
            rec = {
                "month": month_key,
                "search": frag,
                "media_id": item.get("id"),
                "download_url": file_url,
                "saved_as": fname,
                "title": title,
                "mime_type": item.get("mime_type"),
            }
            if args.dry_run:
                print(f"  dry-run {fname}")
                manifest.append({**rec, "dry_run": True})
                continue
            try:
                body = download(file_url)
                (out_dir / fname).write_bytes(body)
                print(f"  OK {fname} ({len(body)} bytes)")
                manifest.append({**rec, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)})
            except Exception as e:
                print(f"  ERR {fname}: {e}")
                manifest.append({**rec, "error": str(e)})

        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out_dir / 'manifest.json'}")


if __name__ == "__main__":
    main()
