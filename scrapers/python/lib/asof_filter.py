"""Filter disclosure filenames/URLs to a calendar as-of date (YYYY-MM-DD)."""
from __future__ import annotations

import calendar
import re
from typing import Literal

try:
    from .disclosure_date import dates_match_as_of, extract_dates, year_month_key
except ImportError:
    from disclosure_date import dates_match_as_of, extract_dates, year_month_key

AS_OF_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

MONTH_NAMES = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
MONTH_SHORT = [n[:3] for n in MONTH_NAMES]

DATE_IN_NAME_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+(\d{4})\b",
    re.I,
)

MONTH_WORD_TO_NUM = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def parse_as_of(as_of: str) -> tuple[int, int, int] | None:
    m = AS_OF_RE.match(as_of.strip())
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if mo < 1 or mo > 12 or d < 1 or d > 31:
        return None
    return y, mo, d


def month_end_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _month_meta(storage_key: str) -> dict | None:
    parsed = parse_as_of(storage_key)
    if not parsed:
        return None
    y, mo, d = parsed
    last = month_end_day(y, mo)
    return {
        "year": y,
        "month": mo,
        "day": d,
        "last_day": last,
        "mm": f"{mo:02d}",
        "month_short": MONTH_SHORT[mo - 1],
        "month_long": MONTH_NAMES[mo - 1],
    }


def _month_end_patterns(meta: dict) -> list[re.Pattern[str]]:
    d = str(meta["last_day"])
    mm = meta["mm"]
    year = meta["year"]
    ms = meta["month_short"]
    ml = meta["month_long"]
    ord_ = r"(?:st|th|nd|rd)?"
    patterns = [
        re.compile(rf"\b{d}{ord_}\s*{ml}\b", re.I),
        re.compile(rf"\b{d}{ord_}\s*{ms}\b", re.I),
        re.compile(rf"\b{ml}\s+{d}{ord_}\b", re.I),
        re.compile(rf"\b{ms}\s+{d}{ord_}\b", re.I),
        re.compile(rf"{d}{ord_}[-_./]{mm}[-_./]{year}", re.I),
        re.compile(rf"{d}{ord_}[-_./]{ms}", re.I),
        re.compile(rf"{d}{ord_}[-_./]{ml}", re.I),
        re.compile(rf"{d:0>2}{mm}{year}", re.I),
        re.compile(rf"as on {d}\b", re.I),
    ]
    if meta["last_day"] == 31:
        patterns.extend(
            [
                re.compile(r"\b31(?:st)?\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I),
                re.compile(r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+31(?:st)?(?:\b|[,\s_])", re.I),
            ]
        )
    return patterns


def _mid_month_patterns(meta: dict) -> list[re.Pattern[str]]:
    mm = meta["mm"]
    year = meta["year"]
    ms = meta["month_short"]
    ml = meta["month_long"]
    ord_ = r"(?:st|th|nd|rd)?"
    return [
        re.compile(rf"\b15{ord_}\s*{ml}\b", re.I),
        re.compile(rf"\b15{ord_}\s*{ms}\b", re.I),
        re.compile(rf"\b{ml}\s+15{ord_}\b", re.I),
        re.compile(rf"\b{ms}\s+15{ord_}\b", re.I),
        re.compile(rf"15{ord_}[-_./]{mm}[-_./]{year}", re.I),
        re.compile(rf"15{ord_}[-_./]{mm}[-_./]{str(year)[-2:]}", re.I),
        re.compile(rf"\b{ms}\s+15{ord_},?\s+{year}\b", re.I),
        re.compile(rf"\b{ml}\s+15{ord_},?\s+{year}\b", re.I),
        re.compile(rf"1-15\s+{ms}\b", re.I),
        re.compile(r"\bmid[-\s]?month\b", re.I),
    ]


def file_matches_storage_key(
    filename: str,
    url: str,
    storage_key: str,
    cadence: Literal["monthly", "fortnightly"] = "fortnightly",
) -> bool:
    """Mirror scrapers/node/lib/asofFileFilter.js."""
    if not AS_OF_RE.match(storage_key):
        return True
    meta = _month_meta(storage_key)
    if not meta:
        return True
    blob = f"{filename} {url}".lower()
    is_mid = meta["day"] <= 15
    end_hints = _month_end_patterns(meta)
    mid_hints = _mid_month_patterns(meta)

    if re.search(r"monthly portfolio", blob) and cadence == "fortnightly":
        return False

    if cadence == "fortnightly":
        if is_mid:
            if any(p.search(blob) for p in end_hints):
                return False
            return True
        if any(p.search(blob) for p in mid_hints) and not any(
            p.search(blob) for p in end_hints
        ):
            return False
    elif cadence == "monthly" and not is_mid:
        if any(p.search(blob) for p in mid_hints) and not any(
            p.search(blob) for p in end_hints
        ):
            return False
    return True


def filter_paths_for_storage_key(
    paths: list,
    storage_key: str,
    cadence: str,
) -> list:
    out = []
    for p in paths:
        name = p.name if hasattr(p, "name") else str(p)
        url = str(p)
        if file_matches_storage_key(name, url, storage_key, cadence):  # type: ignore[arg-type]
            out.append(p)
    return out


def canonical_as_of_for_folder(
    extracted: str | None,
    folder_period: str,
    disclosure_type: str,
) -> str | None:
    """When a file lives in a date-keyed folder, clamp as_of to that slice if same month."""
    if not AS_OF_RE.match(folder_period):
        return extracted
    if disclosure_type != "fortnightly":
        return extracted
    fy, fm, fd = (int(x) for x in folder_period.split("-"))
    if not extracted or not AS_OF_RE.match(extracted):
        return folder_period
    ey, em, ed = (int(x) for x in extracted.split("-"))
    if (ey, em) != (fy, fm):
        return extracted
    folder_mid = fd <= 15
    extract_mid = ed <= 15
    if folder_mid != extract_mid:
        return folder_period
    # Same half of the month: keep the canonical slice (14 Aug → 15).
    return folder_period


def doc_name_to_as_of(doc_name: str) -> str | None:
    try:
        from .disclosure_date import first_iso
    except ImportError:
        from disclosure_date import first_iso
    return first_iso(doc_name or "")




def file_matches_asof_strict(
    filename: str,
    url: str,
    as_of: str,
) -> bool:
    """Keep disclosure-page spreadsheets; drop only a clear wrong as-of/month."""
    if not AS_OF_RE.match(as_of):
        return True
    if dates_match_as_of(filename, url, as_of=as_of):
        return True
    dates = extract_dates(filename, url)
    if dates:
        return False
    mk = year_month_key(filename, url)
    if mk == as_of[:7]:
        return True
    if mk:
        return False
    return True

def filename_matches_asof(url_or_name: str, as_of: str) -> bool:
    """Strict positive match used when scraping AMC pages with full history."""
    return file_matches_asof_strict(url_or_name, url_or_name, as_of)


def default_as_of_for_month(month_key: str, *, fortnightly: bool) -> str | None:
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        return None
    y, m = parts[0], parts[1].zfill(2)
    if fortnightly:
        return f"{y}-{m}-15"
    return None
