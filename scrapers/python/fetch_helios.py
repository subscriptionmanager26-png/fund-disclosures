#!/usr/bin/env python3
"""
Helios Mutual Fund — download monthly portfolio files for given YYYY-MM.

Source page (WordPress static HTML with direct links):
  https://www.heliosmf.in/portfolio-disclosure/

Monthly portfolio links are direct `.xls/.xlsx` URLs under `wp-content/uploads/...`.
We parse month from filename patterns like:
  - ...-28th-February-2026.xlsx
  - ...-as-on-31st-January-2026.xlsx
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from disclosure_date import dates_match_as_of, year_month_key

PAGE_URL = "https://www.heliosmf.in/portfolio-disclosure/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

LINK_RE = re.compile(
    r'href="(https://www\.heliosmf\.in/wp-content/uploads/[^"]+\.(?:xlsx|xls)(?:\?[^"]*)?)"',
    re.I,
)

# Helios WordPress is slow; retry timeouts instead of failing the whole AMC.
HTTP_TIMEOUT_S = 180
HTTP_RETRIES = 3
HTTP_BACKOFF_S = 5


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit('/', 1)[-1]
    base = unquote(base.split('?')[0])
    if not base or base in ('.', '..'):
        base = 'download.xlsx'
    return re.sub(r'[^\w.\-() ]', '_', base).strip()[:200] or 'download.xlsx'


def _urlopen_bytes(url: str, *, headers: dict[str, str], timeout: int = HTTP_TIMEOUT_S) -> bytes:
    last_err: Exception | None = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (TimeoutError, URLError, HTTPError, OSError) as e:
            last_err = e
            if attempt >= HTTP_RETRIES:
                break
            wait = HTTP_BACKOFF_S * attempt
            print(f"  retry {attempt}/{HTTP_RETRIES} after {type(e).__name__}: {e} (sleep {wait}s)")
            time.sleep(wait)
    assert last_err is not None
    raise last_err


def fetch_html() -> str:
    body = _urlopen_bytes(
        PAGE_URL,
        headers=HEADERS,
        timeout=HTTP_TIMEOUT_S,
    )
    return body.decode('utf-8', 'ignore')


def url_to_month_key(url: str) -> str | None:
    name = unquote(urlparse(url).path.rsplit('/', 1)[-1])
    return year_month_key(name, url)


def extract_rows(html: str, *, fortnightly: bool = False) -> list[dict]:
    urls = sorted(set(LINK_RE.findall(html)))
    rows = []
    for u in urls:
        name = unquote(urlparse(u).path.rsplit('/', 1)[-1])
        blob = name.lower()
        mk = url_to_month_key(u)
        if not mk:
            continue
        is_fn = 'fortnightly' in blob
        if fortnightly:
            if not is_fn:
                continue
        elif is_fn:
            continue
        if 'portfolio' not in blob and 'monthly' not in blob and 'monthtly' not in blob:
            if not blob.startswith('helios-'):
                continue
        rows.append({'month_key': mk, 'download_url': u, 'name': name})
    return rows


def download(url: str) -> bytes:
    return _urlopen_bytes(
        url,
        headers={
            'User-Agent': HEADERS['User-Agent'],
            'Accept': '*/*',
            'Referer': PAGE_URL,
        },
        timeout=HTTP_TIMEOUT_S,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description='Fetch Helios monthly portfolio files')
    parser.add_argument('--months', nargs='+', default=['2026-01', '2026-02'], help='YYYY-MM')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent, help='mf-monthly-holdings root')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--fortnightly', action='store_true')
    parser.add_argument('--as-of', dest='as_of', default='', help='YYYY-MM-DD fortnightly slice')
    args = parser.parse_args()

    amc_dir = args.root / 'amcs' / 'helios-mutual-fund'
    as_of = (args.as_of or '').strip()
    if args.fortnightly and not as_of and args.months:
        as_of = f"{args.months[0]}-15"

    print(f'GET {PAGE_URL} …')
    html = fetch_html()
    rows = extract_rows(html, fortnightly=args.fortnightly)
    kind = 'fortnightly' if args.fortnightly else 'monthly'
    print(f'  … parsed {len(rows)} {kind} portfolio link(s)')

    by_month: dict[str, list[dict]] = {k: [] for k in args.months}
    for r in rows:
        mk = r.get('month_key')
        if mk in by_month:
            by_month[mk].append(r)

    for mk in args.months:
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)

        batch = by_month.get(mk) or []
        if as_of:
            batch = [
                r for r in batch
                if dates_match_as_of(r.get('name') or '', r.get('download_url') or '', as_of=as_of)
            ]
        print(f'\n{mk}: {len(batch)} file(s)')
        manifest: list[dict] = []

        if not batch:
            print('  No matching monthly rows for this month.')

        for i, row in enumerate(batch, 1):
            url = row['download_url']
            fname = safe_filename(url)
            dest = out_dir / fname
            rec = {
                'month': mk,
                'download_url': url,
                'saved_as': fname,
                'title': row.get('name'),
            }
            if args.dry_run:
                print(f'  [{i}] {fname}')
                manifest.append({**rec, 'sha256': '', 'dry_run': True})
                continue
            # Skip re-download when a non-empty file already exists (avoids timeouts).
            # Tiny stubs (<1 KiB) are treated as failed prior downloads.
            if dest.exists() and dest.stat().st_size > 1024:
                h = hashlib.sha256(dest.read_bytes()).hexdigest()
                manifest.append({**rec, 'sha256': h, 'skipped_existing': True})
                print(f'  [{i}] SKIP existing {fname} ({dest.stat().st_size} bytes)')
                continue
            if dest.exists() and dest.stat().st_size <= 1024:
                try:
                    dest.unlink()
                except OSError:
                    pass
            try:
                body = download(url)
                if not body:
                    raise RuntimeError('empty download body')
                h = hashlib.sha256(body).hexdigest()
                dest.write_bytes(body)
                manifest.append({**rec, 'sha256': h})
                print(f'  [{i}] OK {fname} ({len(body)} bytes)')
            except Exception as e:
                manifest.append({**rec, 'sha256': '', 'error': str(e)})
                print(f'  [{i}] ERR {fname}: {e}')

        (out_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        print(f'Wrote {out_dir / "manifest.json"}')


if __name__ == '__main__':
    main()
