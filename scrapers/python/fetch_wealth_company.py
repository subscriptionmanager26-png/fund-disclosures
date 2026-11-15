#!/usr/bin/env python3
"""The Wealth Company — fortnightly and monthly portfolio downloads.

Pages:
  https://www.wealthcompanyamc.in/literature-forms/portfolio-documents/fortnightly/
  https://www.wealthcompanyamc.in/literature-forms/portfolio-documents/monthly/

Labels and /uploads/*Portfolio* paths are embedded in the HTML (MUI/Next).
Pair first N labels with upload paths in document order.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

BASE = "https://www.wealthcompanyamc.in"
FN_URL = f"{BASE}/literature-forms/portfolio-documents/fortnightly/"
MO_URL = f"{BASE}/literature-forms/portfolio-documents/monthly/"
HEADERS = {"User-Agent": "Mozilla/5.0"}
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    # Nov 2026: CMS titles switched to abbreviations ("Oct 31, 2026").
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_ALT = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)
FN_LABEL_RE = re.compile(
    r"Fortnightly - The Wealth Company [^\"<>]+? - "
    rf"(({_MONTH_ALT})\.? \d{{1,2}}, 20\d{{2}})",
    re.I,
)
MO_LABEL_RE = re.compile(
    r"Monthly - The Wealth Company [^\"<>]+? - "
    rf"(({_MONTH_ALT})\.? \d{{1,2}}, 20\d{{2}})",
    re.I,
)
FN_UPLOAD_RE = re.compile(r"/uploads/Fortnightly_[Pp]ortfolio[\w.-]+\.(?:xlsx?|xlsb)", re.I)
MO_UPLOAD_RE = re.compile(r"/uploads/Monthly_[Pp]ortfolio[\w.-]+\.(?:xlsx?|xlsb)", re.I)
CMS_UPLOAD_RE = re.compile(
    r'\{"uploadDate":"(\d{4}-\d{2}-\d{2})"[^}]*"attachment":\{"id":\d+,"documentId":"[^"]+","url":"(/uploads/(?:Fortnightly|Monthly)_[^"]+\.(?:xlsx?|xlsb))"\}',
    re.I,
)


def ssl_ctx(insecure: bool) -> ssl.SSLContext:
    if insecure:
        return ssl._create_unverified_context()
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def parse_ymd(label_date: str) -> tuple[int, int, int] | None:
    m = re.match(r"([A-Za-z]+)\.?\s+(\d{1,2}),\s*(20\d{2})", label_date.strip())
    if not m:
        return None
    mon = MONTHS.get(m.group(1).lower())
    if not mon:
        return None
    return int(m.group(3)), mon, int(m.group(2))


def parse_ym(label_date: str) -> tuple[int, int] | None:
    ymd = parse_ymd(label_date)
    return (ymd[0], ymd[1]) if ymd else None


_NAME_DATE_RE = re.compile(rf"\b(({_MONTH_ALT})\.?\s+\d{{1,2}},\s*20\d{{2}})", re.I)
_ROW_START_RE = re.compile(r'\{"uploadDate":"')


def collect_pairs_from_cms(text: str) -> list[dict]:
    """Parse Strapi rows embedded in the page (``downloads: [{uploadDate, name, attachment}]``).

    The disclosure date comes from the row ``name`` ("Monthly - The Wealth Company
    Multi Cap Fund - Oct 31, 2026"). ``uploadDate`` is when the file was uploaded:
    for October 2026 it was 2026-11-09, so using it filed every October portfolio
    under November and the monthly fetch for October came back empty. It is
    only used when the name carries no date.
    """
    text = text.replace("&amp;", "&")
    decoder = json.JSONDecoder()
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for m in _ROW_START_RE.finditer(text):
        try:
            row, _ = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        path = ((row.get("attachment") or {}) if isinstance(row, dict) else {}).get("url") or ""
        if not re.match(r"/uploads/(?:Fortnightly|Monthly)_[^\"]+\.(?:xlsx?|xlsb)$", path, re.I):
            continue
        upload_date = str(row.get("uploadDate") or "")
        name = str(row.get("name") or "").replace("\\n", " ").strip()
        ymd = None
        nm = _NAME_DATE_RE.search(name)
        if nm:
            ymd = parse_ymd(nm.group(1))
        source = "name"
        if not ymd and re.fullmatch(r"\d{4}-\d{2}-\d{2}", upload_date):
            ymd = tuple(int(x) for x in upload_date.split("-"))
            source = "uploadDate"
        if not ymd:
            continue
        key = (f"{ymd[0]}-{ymd[1]}-{ymd[2]}", path)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "year": ymd[0],
                "month": ymd[1],
                "day": ymd[2],
                "label": name or upload_date,
                "path": path,
                "upload_date": upload_date,
                "date_source": source,
            }
        )
    return out


def collect_pairs(text: str, *, label_re: re.Pattern[str], upload_re: re.Pattern[str]) -> list[dict]:
    """Pair document labels with /uploads paths.

    The monthly/fortnightly pages embed each label twice (SSR + JSON). Pairing
    the raw lists by index then maps June labels onto May files. Dedup labels
    (order-preserving, after unescaping &amp;) so the first N unique titles
    line up with the N unique upload paths.
    """
    text = text.replace("&amp;", "&")
    labels: list[str] = []
    seen_l: set[str] = set()
    for m in label_re.finditer(text):
        key = m.group(0)
        if key in seen_l:
            continue
        seen_l.add(key)
        labels.append(m.group(1))
    uploads: list[str] = []
    seen_u: set[str] = set()
    for u in upload_re.findall(text):
        if u not in seen_u:
            seen_u.add(u)
            uploads.append(u)
    pairs = []
    for lab, path in zip(labels, uploads):
        ym = parse_ymd(lab)
        if not ym:
            continue
        pairs.append({"year": ym[0], "month": ym[1], "day": ym[2], "label": lab, "path": path})
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--fortnightly", action="store_true")
    ap.add_argument("--as-of", dest="as_of", default="", help="Calendar as-of YYYY-MM-DD")
    ap.add_argument("--insecure-ssl", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    page = FN_URL if args.fortnightly else MO_URL
    label_re = FN_LABEL_RE if args.fortnightly else MO_LABEL_RE
    upload_re = FN_UPLOAD_RE if args.fortnightly else MO_UPLOAD_RE

    ctx = ssl_ctx(args.insecure_ssl)
    req = urllib.request.Request(page, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
        html = resp.read().decode("utf-8", "ignore")
    text = html.replace('\\"', '"').replace("\\/", "/")
    pairs = collect_pairs_from_cms(text)
    if not pairs:
        pairs = collect_pairs(text, label_re=label_re, upload_re=upload_re)
        print("  (fallback label/upload pairing)", flush=True)
    else:
        print(f"  CMS rows: {len(pairs)} (dated from name: {sum(1 for p in pairs if p.get('date_source') == 'name')})", flush=True)

    as_of_parts = None
    if args.as_of:
        bits = args.as_of.strip().split("-")
        if len(bits) == 3:
            as_of_parts = (int(bits[0]), int(bits[1]), int(bits[2]))

    targets = set()
    for mk in args.months:
        y, m = mk.split("-")
        if as_of_parts:
            targets.add(as_of_parts)
        else:
            targets.add((int(y), int(m)))

    amc = args.root / "amcs" / "the-wealth-company-mutual-fund"
    for target in sorted(targets):
        if len(target) == 3:
            y, m, d = target
            mk = f"{y}-{m:02d}"
        else:
            y, m = target
            d = None
            mk = f"{y}-{m:02d}"
        out = amc / mk
        out.mkdir(parents=True, exist_ok=True)
        for p in out.iterdir():
            if p.is_file():
                p.unlink()
        if d is not None:
            selected = [
                r
                for r in pairs
                if (r["year"], r["month"], r["day"]) == (y, m, d)
                or r.get("upload_date") == f"{y:04d}-{m:02d}-{d:02d}"
            ]
        else:
            selected = [r for r in pairs if (r["year"], r["month"]) == (y, m)]
        uniq = []
        seenp: set[str] = set()
        for r in selected:
            if r["path"] in seenp:
                continue
            seenp.add(r["path"])
            uniq.append(r)
        kind = "fortnightly" if args.fortnightly else "monthly"
        print(f"{mk} ({kind}): {len(uniq)} file(s)")
        manifest = []
        for r in uniq:
            url = urljoin(BASE + "/", r["path"])
            fn = re.sub(r"[^\w.\-]+", "_", r["path"].rsplit("/", 1)[-1])[:180]
            rec = {"month": mk, "title": r["label"], "download_url": url, "saved_as": fn}
            if args.dry_run:
                manifest.append(rec)
                print(f"  DRY {fn}")
                continue
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={**HEADERS, "Referer": page}),
                timeout=120,
                context=ctx,
            ) as resp:
                body = resp.read()
            (out / fn).write_bytes(body)
            manifest.append({**rec, "sha256": hashlib.sha256(body).hexdigest()})
            print(f"  OK {fn} ({len(body)} bytes)")
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
