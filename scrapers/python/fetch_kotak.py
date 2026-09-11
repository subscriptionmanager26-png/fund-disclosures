#!/usr/bin/env python3
"""
Kotak Mahindra Mutual Fund — download portfolio spreadsheets for given YYYY-MM.

AMC-direct only (www.kotakmf.com). Do not use Advisorkhoj or other aggregators.

Default hub:
  https://www.kotakmf.com/Information/statutory-disclosure
  (Portfolios → Consolidated & Fortnightly Portfolio, optionId=51)

**Preferred:** JSON API (no captcha / uzlc needed for listing):

  GET https://java17vlbapi.kotakmf.com/kotakapi/forms/user/v1/getsubheaderList/417?option=51&…

File bytes are public on:
  https://vatseelabs-s3.kotakmf.com/{content}
  where `content` is e.g. FAD/Portfolios/Fortnightly-Portfolio-as-on-July-31,-2026/….xlsx

  python3 reference/python-fetchers/fetch_kotak.py --use-api --fortnightly --months 2026-07

Fallbacks: **`fetch_kotak_playwright.py`**, `--from-html`, `--url-list`, cookies if Radware blocks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse, urlunparse
import http.cookiejar
import urllib.request
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from disclosure_date import year_month_key

PAGE_URL = "https://www.kotakmf.com/Information/statutory-disclosure"
API_BASE = "https://java17vlbapi.kotakmf.com/kotakapi/forms/user/v1"
# Public S3/CloudFront host used by the SPA for non-PDF Forms & Downloads uploads.
VATSEE_S3_BASE = "https://vatseelabs-s3.kotakmf.com/"
# Portfolios header / “Consolidated & Fortnightly Portfolio” option.
DEFAULT_API_PARENT_ID = 417
DEFAULT_API_OPTION = 51

MONTH_KEY_RE = re.compile(r"^\d{4}-\d{2}$")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

MONTH_LONG = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}

MONTHLY_HINT = re.compile(
    r"(?i)month(ly)?\s*[-_\s]*portfolio|portfolio\s*[-_\s]*month|month[-_\s]*end\s*portfolio|"
    r"monthend|month_end|scheme\s*portfolio|portfolio\s*of\s*schemes|"
    r"holding\s*as\s*on|portfolio\s*disclosure|monthly\s*portfolio\s*disclosure|"
    r"consolidated\s*(sebi\s*)?portfolio",
)

FORTNIGHTLY_HINT = re.compile(r"(?i)fortnight(ly)?")

CONSOLIDATED_SEBI_HINT = re.compile(
    r"(?i)consolidated[\s_-]*sebi[\s_-]*portfolio|consolidatedsebiportfolio"
)

A_TAG_RE = re.compile(
    r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
    re.I,
)

ISO_DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")


def looks_like_waf_or_challenge(html: str) -> bool:
    """Radware / bot interstitial — not the real app HTML.

    Kotak's real app shell can also reference bot scripts; rely on captcha **title** and tiny rdwr stubs.
    """
    if not html or len(html) < 200:
        return True
    h = html.lower()
    if "<title>radware captcha page</title>" in h or re.search(
        r"<title>\s*radware\s+captcha\s+page\s*</title>", h
    ):
        return True
    # Edge redirect body before JS runs
    if len(html) < 4000 and "<center>rdwr</center>" in h:
        return True
    return False


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    if not base or base in (".", ".."):
        base = "download.bin"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.bin"


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s)


def infer_month_key_from_text(combined: str) -> str | None:
    """Prefer explicit YYYY-MM / YYYY/MM in JSON strings; then natural-language dates."""
    t = " ".join(strip_tags(combined).split())
    m = re.search(r"\b(20\d{2})[-/](0[1-9]|1[0-2])\b", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(r"\b(0[1-9]|1[0-2])[-/](20\d{2})\b", t)
    if m:
        return f"{m.group(2)}-{m.group(1)}"
    return text_month_to_key(t)


def text_month_to_key(text: str) -> str | None:
    t = " ".join(strip_tags(text).split())
    shared = year_month_key(t)
    if shared:
        return shared
    m = ISO_DATE_RE.search(t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(
        r"(?i)\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+"
        r"(\d{1,2}),?\s+(\d{4})\b",
        t,
    )
    if m:
        try:
            dt = datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}",
                "%B %d %Y",
            )
            return f"{dt.year}-{dt.month:02d}"
        except ValueError:
            pass
    m = re.search(
        r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2}),?\s+(\d{4})\b",
        t,
    )
    if m:
        try:
            dt = datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}",
                "%b %d %Y",
            )
            return f"{dt.year}-{dt.month:02d}"
        except ValueError:
            pass
    m = re.search(
        r"(?i)\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})\b",
        t,
    )
    if m:
        mm = MONTH_LONG.get(m.group(1).lower())
        if mm:
            return f"{m.group(2)}-{mm}"
    m = re.search(
        r"(?i)\b(january|february|march|april|may|june|july|august|september|october|november|december)[_\-](\d{4})\b",
        t,
    )
    if m:
        mm = MONTH_LONG.get(m.group(1).lower())
        if mm:
            return f"{m.group(2)}-{mm}"
    return None


def blob_to_month_key(url: str, anchor_inner: str) -> str | None:
    combined = unquote(url) + " " + strip_tags(anchor_inner)
    return infer_month_key_from_text(combined)


def is_kotak_spreadsheet_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    host = (p.netloc or "").lower()
    if "kotakmf.com" not in host:
        return False
    u = url.lower()
    return bool(re.search(r"\.(xlsx|xls)(\?|$|#)", u))


def build_opener(cookies_path: Path | None) -> urllib.request.OpenerDirector:
    """Use Netscape cookies.txt from the browser after passing Radware (optional)."""
    if cookies_path:
        if not cookies_path.is_file():
            raise SystemExit(f"Cookies file not found: {cookies_path}")
        jar = http.cookiejar.MozillaCookieJar(str(cookies_path))
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except OSError as e:
            raise SystemExit(f"Could not read cookies {cookies_path}: {e}") from e
        except Exception as e:
            raise SystemExit(
                f"Could not parse Netscape cookies.txt {cookies_path}: {e}\n"
                "Export with a “cookies.txt” browser extension (Netscape format)."
            ) from e
    else:
        jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def parse_url_list(path: Path, default_month: str | None) -> list[tuple[str, str, str]]:
    """
    Lines (UTF-8):
      YYYY-MM<TAB>https://...
      YYYY-MM,https://...
      https://...   (only if --default-month YYYY-MM is set)
    Blank lines and # comments ignored.
    """
    rows: list[tuple[str, str, str]] = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        month: str
        url: str
        if "\t" in line:
            a, b = line.split("\t", 1)
            month, url = a.strip(), b.strip()
        elif "," in line and MONTH_KEY_RE.match(line.split(",", 1)[0].strip() or ""):
            a, b = line.split(",", 1)
            month, url = a.strip(), b.strip()
        else:
            if not default_month:
                raise SystemExit(
                    f"URL-list line has no month prefix; add YYYY-MM\\tURL or use --default-month:\n  {line[:120]}"
                )
            month, url = default_month, line
        if not MONTH_KEY_RE.match(month):
            raise SystemExit(f"Invalid month {month!r} in {path} (expected YYYY-MM)")
        if not url.lower().startswith("http"):
            raise SystemExit(f"Invalid URL in {path}: {url[:120]!r}")
        if not is_kotak_spreadsheet_url(url):
            raise SystemExit(
                f"In {path}: URL must be on *.kotakmf.com and end in .xls / .xlsx:\n  {url[:120]}"
            )
        label = f"url-list:{path.name}"
        rows.append((month, url, label))
    return rows


def dedupe_rows(rows: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, str]] = []
    for mk, url, lab in rows:
        key = (mk, url)
        if key in seen:
            continue
        seen.add(key)
        out.append((mk, url, lab))
    return out


def _row_blob(url: str, label: str) -> str:
    return f"{url} {label}"


def is_consolidated_sebi_row(url: str, label: str) -> bool:
    return bool(CONSOLIDATED_SEBI_HINT.search(_row_blob(url, label)))


def is_fortnightly_row(url: str, label: str) -> bool:
    return bool(FORTNIGHTLY_HINT.search(_row_blob(url, label)))


def prefer_monthly_consolidated(rows: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Monthly fetch: keep consolidated SEBI packs; drop fortnightly when SEBI exists."""
    by_month: dict[str, list[tuple[str, str, str]]] = {}
    for mk, url, label in rows:
        by_month.setdefault(mk, []).append((mk, url, label))
    out: list[tuple[str, str, str]] = []
    for batch in by_month.values():
        consolidated = [
            row for row in batch if is_consolidated_sebi_row(row[1], row[2])
        ]
        if consolidated:
            out.extend(consolidated)
            continue
        # No consolidated monthly yet — keep non-fortnightly rows and month-end fortnightly.
        for mk, url, label in batch:
            blob = _row_blob(url, label)
            if not is_fortnightly_row(url, label):
                out.append((mk, url, label))
                continue
            if re.search(
                r"(?i)(?:\b31(?:st)?\b|\b30(?:th)?\b|as[- ]on[- ].*(?:31|30))",
                blob,
            ):
                out.append((mk, url, label))
    return out


def doc_tuple_from_blob(
    blob: str,
    url: str,
    *,
    require_monthly_hint: bool,
    fortnightly_only: bool = False,
) -> tuple[str, str, str] | None:
    mk = infer_month_key_from_text(blob + " " + url)
    if not mk:
        return None
    text = blob + " " + url
    if fortnightly_only:
        if not FORTNIGHTLY_HINT.search(text):
            return None
    elif require_monthly_hint and not (
        MONTHLY_HINT.search(text) or FORTNIGHTLY_HINT.search(text)
    ):
        return None
    return (mk, url, blob[:300].strip())


def collect_spreadsheet_rows_from_json(
    obj: object,
    *,
    require_monthly_hint: bool,
    fortnightly_only: bool = False,
) -> list[tuple[str, str, str]]:
    """Walk JSON and emit (YYYY-MM, absolute_url, label) for Kotak spreadsheet links."""

    def normalize_url(s: str) -> str | None:
        s = s.strip()
        if not s:
            return None
        if s.startswith("//"):
            s = "https:" + s
        if re.search(r"\.(xlsx|xls)(\?|$|#)", s, re.I):
            if re.match(r"^https?://", s, re.I):
                pass
            elif s.startswith("FAD/") or s.startswith("FormsDownloads/"):
                # SPA downloads non-PDF uploads from vatseelabs-s3, not www.
                s = urljoin(VATSEE_S3_BASE, s.lstrip("/"))
            elif s.startswith("/"):
                s = urljoin("https://www.kotakmf.com/", s.lstrip("/"))
            else:
                if "FAD/" in s or s.lower().endswith((".xlsx", ".xls")):
                    s = urljoin(VATSEE_S3_BASE, s.lstrip("/"))
                else:
                    s = urljoin("https://www.kotakmf.com/", s.lstrip("/"))
        if is_kotak_spreadsheet_url(s):
            return s
        return None

    found: list[tuple[str, str, str]] = []

    def item_blob(o: dict) -> str:
        # Prefer explicit title fields so sibling rows in a list cannot pollute month/title matching.
        parts = [
            o.get("subHeaderTitle"),
            o.get("headerTitle"),
            o.get("description"),
            o.get("fileName"),
            o.get("content"),
            o.get("title"),
            o.get("name"),
        ]
        return " ".join(str(p) for p in parts if p)

    def walk(o: object) -> None:
        if isinstance(o, dict):
            blob = item_blob(o)
            content = o.get("content")
            if isinstance(content, str) and re.search(r"\.(xlsx|xls)(\?|$|#)", content, re.I):
                nu = normalize_url(content)
                if nu:
                    tup = doc_tuple_from_blob(
                        blob or content,
                        nu,
                        require_monthly_hint=require_monthly_hint,
                        fortnightly_only=fortnightly_only,
                    )
                    if tup:
                        found.append(tup)
            for _k, v in o.items():
                if isinstance(v, str):
                    if _k in ("content", "fileName", "file_name"):
                        # Bare filenames are not downloadable; `content` holds the FAD path.
                        continue
                    nu = normalize_url(v)
                    if nu:
                        tup = doc_tuple_from_blob(
                            f"{blob} {v}",
                            nu,
                            require_monthly_hint=require_monthly_hint,
                            fortnightly_only=fortnightly_only,
                        )
                        if tup:
                            found.append(tup)
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(o, list):
            for el in o:
                walk(el)

    walk(obj)
    return found


def collect_spreadsheet_rows_from_dom_anchors(
    anchors: list[tuple[str, str]],
    *,
    page_url: str,
    require_monthly_hint: bool,
) -> list[tuple[str, str, str]]:
    """From Playwright/eval: list of (href, link_text)."""
    rows: list[tuple[str, str, str]] = []
    for href, text in anchors:
        href = (href or "").strip()
        if not href:
            continue
        abs_url = urljoin(page_url, href)
        if not is_kotak_spreadsheet_url(abs_url):
            continue
        blob = f"{abs_url} {text}"
        tup = doc_tuple_from_blob(blob, abs_url, require_monthly_hint=require_monthly_hint)
        if tup:
            rows.append(tup)
    return rows


def extract_api_list(payload: object) -> list[object]:
    """Unwrap common API envelopes to a list of row objects."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "Data", "result", "Result", "response", "Response", "body", "Body"):
        if key in payload:
            inner = payload[key]
            if inner is payload:
                continue
            got = extract_api_list(inner)
            if got:
                return got
    for key in (
        "subHeaderList",
        "SubHeaderList",
        "list",
        "List",
        "items",
        "Items",
        "rows",
        "Rows",
        "records",
        "Records",
        "content",
        "Content",
    ):
        v = payload.get(key)
        if isinstance(v, list):
            return v
    return []


def child_subheader_ids(item: object) -> list[int]:
    if not isinstance(item, dict):
        return []
    keys = (
        "subHeaderId",
        "SubHeaderId",
        "subheaderId",
        "childSubHeaderId",
        "ChildSubHeaderId",
        "navigationId",
        "NavigationId",
    )
    out: list[int] = []
    for k in keys:
        v = item.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, int) and v > 0:
            out.append(v)
        elif isinstance(v, str) and v.strip().isdigit():
            n = int(v.strip())
            if n > 0:
                out.append(n)
    return list(dict.fromkeys(out))


def kotak_mobile_api_headers(uzlc: str | None, authorization: str | None) -> dict[str, str]:
    """Match browser XHR on kotakmf.com (works with getsubheaderList; uzlc optional)."""
    auth = (authorization or "decrypted").strip()
    hdrs = {
        "sec-ch-ua-platform": '"Android"',
        "Authorization": auth,
        "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "sec-ch-ua-mobile": "?1",
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://www.kotakmf.com",
        "Referer": PAGE_URL,
    }
    if (uzlc or "").strip():
        hdrs["uzlc"] = uzlc.strip()
    return hdrs


def _query_params_typed(query: dict[str, str]) -> dict[str, str | int]:
    """Use ints for numeric query keys (same as browser `params` object)."""
    out: dict[str, str | int] = {}
    for k, v in query.items():
        if v.isdigit():
            out[k] = int(v)
        else:
            out[k] = v
    return out


def _parse_kotak_api_body(text: str, status_label: str = "") -> object:
    text = text.strip()
    if not text:
        raise RuntimeError("Empty API response body" + status_label)
    if text.startswith("<"):
        raise RuntimeError(
            "API returned HTML, not JSON (Radware / expired uzlc / missing cookies?). "
            f"{status_label}\nFirst 280 chars:\n{text[:280]}"
        )
    return json.loads(text)


def kotak_api_json_requests(
    path_under_v1: str,
    query: dict[str, str],
    uzlc: str | None,
    authorization: str | None,
) -> object:
    import requests

    url = f"{API_BASE}/{path_under_v1}"
    params = _query_params_typed(query)
    hdrs = kotak_mobile_api_headers(uzlc, authorization)
    r = requests.get(url, params=params, headers=hdrs, timeout=120)
    label = f" HTTP {r.status_code}" if r.status_code != 200 else ""
    if r.status_code >= 400:
        raise RuntimeError(
            f"Kotak API HTTP {r.status_code}: {(r.text or '')[:400]}"
        )
    return _parse_kotak_api_body(r.text or "", label)


def kotak_api_json_urllib(
    opener: urllib.request.OpenerDirector,
    path_under_v1: str,
    query: dict[str, str],
    uzlc: str | None,
    authorization: str | None,
) -> object:
    url = f"{API_BASE}/{path_under_v1}?{urlencode(query)}"
    headers = {
        **kotak_mobile_api_headers(uzlc, authorization),
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    with opener.open(req, timeout=120) as resp:
        raw = resp.read()
    text = raw.decode("utf-8", errors="ignore")
    return _parse_kotak_api_body(text)


def kotak_api_json(
    opener: urllib.request.OpenerDirector,
    path_under_v1: str,
    query: dict[str, str],
    uzlc: str | None,
    authorization: str | None,
    *,
    api_http: str = "auto",
) -> object:
    """
    api_http:
      - requests — `pip install requests`; uses same headers as browser snippet
      - urllib — opener (e.g. cookies) + mobile headers
      - auto — try requests first, then urllib
    """
    last_err: Exception | None = None
    if api_http in ("auto", "requests"):
        try:
            return kotak_api_json_requests(path_under_v1, query, uzlc, authorization)
        except ImportError as e:
            last_err = e
            if api_http == "requests":
                raise RuntimeError(
                    "Install requests: pip install -r scripts/requirements-kotak-requests.txt"
                ) from e
        except Exception as e:
            last_err = e
            if api_http == "requests":
                raise
    if api_http == "urllib" or api_http == "auto":
        try:
            return kotak_api_json_urllib(opener, path_under_v1, query, uzlc, authorization)
        except Exception as e:
            if api_http == "urllib":
                raise
            last_err = last_err or e
    raise RuntimeError(
        f"Kotak API failed (requests then urllib). Last error: {last_err}"
    )


def fetch_rows_via_forms_api(
    opener: urllib.request.OpenerDirector,
    *,
    uzlc: str | None,
    authorization: str | None,
    parent_id: int,
    option: int,
    page_size: int,
    max_pages: int,
    recurse_subheaders: bool,
    max_depth: int,
    require_monthly_hint: bool,
    fortnightly_only: bool = False,
    api_http: str = "auto",
) -> list[tuple[str, str, str]]:
    visited: set[int] = set()
    collected: list[tuple[str, str, str]] = []

    def load_parent(pid: int, depth: int) -> None:
        if pid in visited or depth > max_depth:
            return
        visited.add(pid)
        page_num = 1
        while page_num <= max_pages:
            payload = kotak_api_json(
                opener,
                f"getsubheaderList/{pid}",
                {
                    "option": str(option),
                    "pagination": "1",
                    "pageSize": str(page_size),
                    "pageNumber": str(page_num),
                },
                uzlc,
                authorization,
                api_http=api_http,
            )
            collected.extend(
                collect_spreadsheet_rows_from_json(
                    payload,
                    require_monthly_hint=require_monthly_hint,
                    fortnightly_only=fortnightly_only,
                )
            )
            items = extract_api_list(payload)
            if not items:
                break
            if recurse_subheaders:
                for item in items:
                    for cid in child_subheader_ids(item):
                        if cid != pid:
                            load_parent(cid, depth + 1)
            if len(items) < page_size:
                break
            page_num += 1

    load_parent(parent_id, 0)
    return collected


def extract_link_rows(html: str, base_url: str = PAGE_URL) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for href, inner in A_TAG_RE.findall(html):
        href = href.strip()
        if href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        abs_url = urljoin(base_url, href)
        if not is_kotak_spreadsheet_url(abs_url):
            continue
        blob = abs_url + strip_tags(inner)
        if not MONTHLY_HINT.search(blob):
            continue
        mk = blob_to_month_key(abs_url, inner)
        if not mk:
            continue
        label = " ".join(strip_tags(inner).split())
        rows.append((mk, abs_url, label or abs_url))
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for mk, u, lab in rows:
        if u in seen:
            continue
        seen.add(u)
        out.append((mk, u, lab))
    return out


def fetch_url(opener: urllib.request.OpenerDirector, url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={**HEADERS, "Referer": PAGE_URL},
        method="GET",
    )
    with opener.open(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "ignore")


def encode_url_for_http(url: str) -> str:
    p = urlparse(url.strip())
    path = quote(p.path, safe="/%")
    return urlunparse((p.scheme, p.netloc, path, p.params, p.query, p.fragment))


def download(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    h = {
        **HEADERS,
        "Accept": "*/*",
        "Origin": "https://www.kotakmf.com",
        "Referer": PAGE_URL,
    }
    if extra_headers:
        h.update(extra_headers)
    req = urllib.request.Request(encode_url_for_http(url), headers=h, method="GET")
    with opener.open(req, timeout=120) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Kotak MF monthly portfolio files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="mf-monthly-holdings root",
    )
    parser.add_argument(
        "--cookies",
        type=Path,
        help="Netscape cookies.txt (export after passing captcha in browser — enables live page + downloads).",
    )
    parser.add_argument(
        "--from-html",
        type=Path,
        help="Saved HTML from a browser (often works together with --cookies for downloads).",
    )
    parser.add_argument(
        "--url-list",
        type=Path,
        help="Text file: YYYY-MM<TAB>download_url per line (see amcs/kotak-mahindra-mutual-fund/URL_LIST.example.txt).",
    )
    parser.add_argument(
        "--default-month",
        help="With --url-list: use this YYYY-MM for bare URL lines (no month prefix).",
    )
    parser.add_argument("--page-url", default=PAGE_URL, help="Base URL for resolving relative links")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--use-api",
        action="store_true",
        help="Use forms JSON API (needs fresh uzlc from DevTools, or cookies some sessions).",
    )
    parser.add_argument(
        "--uzlc",
        help="Value of the uzlc request header (or set env KOTAK_UZLC). Short-lived; copy from Network tab.",
    )
    parser.add_argument(
        "--api-auth",
        help="Authorization header for API (default: decrypted, or env KOTAK_API_AUTH).",
    )
    parser.add_argument("--api-parent-id", type=int, default=DEFAULT_API_PARENT_ID, help="getsubheaderList/{id}")
    parser.add_argument("--api-option", type=int, default=DEFAULT_API_OPTION, help="?option=")
    parser.add_argument("--api-page-size", type=int, default=50, help="pageSize query param")
    parser.add_argument("--api-max-pages", type=int, default=200, help="Safety cap per subheader")
    parser.add_argument(
        "--no-api-recurse",
        action="store_true",
        help="Do not follow subHeaderId (etc.) children; only paginate the parent id.",
    )
    parser.add_argument("--api-max-depth", type=int, default=12, help="Max recursion depth for subheaders")
    parser.add_argument(
        "--api-strict-hint",
        action="store_true",
        help="Require MONTHLY_HINT text near API URLs (may drop rows if titles omit keywords).",
    )
    parser.add_argument(
        "--also-fetch-html",
        action="store_true",
        help="With --use-api: still GET the forms HTML page and merge <a> links.",
    )
    parser.add_argument(
        "--api-http",
        choices=("auto", "requests", "urllib"),
        default="auto",
        help="HTTP client for getsubheaderList: requests (browser-like headers), urllib, or auto (try requests first).",
    )
    parser.add_argument(
        "--fortnightly",
        action="store_true",
        help="Only keep Fortnightly Portfolio rows (titles/paths containing fortnightly).",
    )
    args = parser.parse_args()

    want = set(args.months)
    amc_dir = args.root / "amcs" / "kotak-mahindra-mutual-fund"
    base_for_links = (args.page_url or PAGE_URL).strip()

    api_uzlc = (args.uzlc or os.environ.get("KOTAK_UZLC") or "").strip() or None
    api_auth = (args.api_auth or os.environ.get("KOTAK_API_AUTH") or "decrypted").strip() or None
    # Listing works without uzlc against java17; default on unless caller forces HTML-only.
    use_api = args.use_api or bool(api_uzlc) or True

    opener = build_opener(args.cookies)
    dl_extra: dict[str, str] = {}
    if api_uzlc:
        dl_extra["uzlc"] = api_uzlc
    if api_auth:
        dl_extra["Authorization"] = api_auth

    all_rows: list[tuple[str, str, str]] = []

    if use_api:
        print(
            f"API getsubheaderList/{args.api_parent_id} option={args.api_option} "
            f"(recurse={'off' if args.no_api_recurse else 'on'}) …",
            flush=True,
        )
        try:
            api_rows = fetch_rows_via_forms_api(
                opener,
                uzlc=api_uzlc,
                authorization=api_auth,
                parent_id=args.api_parent_id,
                option=args.api_option,
                page_size=max(1, args.api_page_size),
                max_pages=max(1, args.api_max_pages),
                recurse_subheaders=not args.no_api_recurse,
                max_depth=max(0, args.api_max_depth),
                require_monthly_hint=args.api_strict_hint,
                fortnightly_only=args.fortnightly,
                api_http=args.api_http,
            )
        except Exception as e:
            raise SystemExit(
                f"Forms API failed: {e}\n\n"
                "Copy a fresh `uzlc` header from DevTools → Network → getsubheaderList request, e.g.\n"
                "  export KOTAK_UZLC='…'\n"
                f"  python3 scripts/fetch_kotak.py --use-api --months {' '.join(args.months)}\n"
                "Add --cookies cookies.txt if the API still returns HTML.\n"
            ) from e
        print(f"  … API extracted {len(api_rows)} spreadsheet row(s) (before dedupe)", flush=True)
        all_rows.extend(api_rows)

    if args.url_list:
        ul_rows = parse_url_list(args.url_list, args.default_month)
        all_rows.extend(ul_rows)
        print(f"Loaded {len(ul_rows)} row(s) from {args.url_list}", flush=True)

    if args.from_html:
        html = args.from_html.read_text(encoding="utf-8", errors="ignore")
        print(f"Read HTML from {args.from_html}", flush=True)
        all_rows.extend(extract_link_rows(html, base_url=base_for_links))

    fetch_live_html = not args.url_list and (not use_api or args.also_fetch_html)
    if fetch_live_html:
        print(f"GET {PAGE_URL} …", flush=True)
        try:
            html = fetch_url(opener, PAGE_URL)
        except Exception as e:
            if use_api and all_rows:
                print(f"  … HTML fetch skipped/failed ({e}); continuing with API rows only.", flush=True)
                html = ""
            else:
                raise SystemExit(
                    f"Could not load Kotak page: {e}\n"
                    "Try --use-api with KOTAK_UZLC, or --cookies, or --from-html / --url-list.\n"
                    "See amcs/kotak-mahindra-mutual-fund/README.md"
                ) from e
        if html:
            print(f"  … got {len(html)} bytes", flush=True)
            if looks_like_waf_or_challenge(html):
                if use_api and all_rows:
                    print("  … HTML looks like WAF; ignoring and using API rows only.", flush=True)
                else:
                    msg = (
                        "Response looks like Radware / bot protection (not the real downloads page).\n\n"
                        "Try:\n"
                        "  • Forms API: export KOTAK_UZLC from DevTools and run with --use-api\n"
                        "  • Or --cookies / --url-list\n\n"
                        f"Page: {PAGE_URL}\n"
                    )
                    raise SystemExit(msg)
            all_rows.extend(extract_link_rows(html, base_url=base_for_links))

    rows = dedupe_rows(all_rows)
    if not args.fortnightly:
        before = len(rows)
        rows = prefer_monthly_consolidated(rows)
        if before != len(rows):
            print(
                f"  … monthly preference kept {len(rows)}/{before} row(s) "
                "(consolidated SEBI over fortnightly)",
                flush=True,
            )
    print(f"Total {len(rows)} monthly-portfolio file link(s) after merge/dedupe", flush=True)

    by_month: dict[str, list[tuple[str, str]]] = {k: [] for k in args.months}
    for mk, url, label in rows:
        if mk not in want:
            continue
        by_month[mk].append((url, label))

    for month_key in args.months:
        out_dir = amc_dir / month_key
        out_dir.mkdir(parents=True, exist_ok=True)
        batch = by_month.get(month_key) or []
        print(f"\n{month_key}: {len(batch)} file(s)", flush=True)
        manifest: list[dict] = []

        if not batch:
            print(
                "  No files for this month — use --use-api + KOTAK_UZLC, --cookies, --url-list, "
                "or HTML (see amcs/kotak-mahindra-mutual-fund/README.md).",
                flush=True,
            )

        for i, (file_url, label) in enumerate(batch, 1):
            fname = safe_filename(file_url)
            rec = {
                "month": month_key,
                "download_url": file_url,
                "saved_as": fname,
                "label": label,
            }
            if args.dry_run:
                print(f"  [{i}] {fname}", flush=True)
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue
            try:
                body = download(opener, file_url, extra_headers=dl_extra or None)
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
