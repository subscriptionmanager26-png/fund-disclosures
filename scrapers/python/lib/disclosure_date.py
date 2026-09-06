"""Parse AMC disclosure dates from labels, titles, and filenames.

AMCs mix full month names, abbreviations (Aug/Sept), ordinals (15th),
hyphens, and 2-digit years (15-Aug-26). Fortnightly filings also land on
nearby days (14 Aug for the 15th slice). Match on a canonical mid-month /
month-end slice, not a single literal day.

Also handles glued Kotak-style names (August312026) and path quirks
like as-on-August-31,-2026.
"""
from __future__ import annotations

import calendar
import re
from datetime import date

MONTH_NUM: dict[str, int] = {
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
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

MONTH_RE = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?"
)
ORD = r"(?:st|nd|rd|th)?"
SEP = r"[-_\s./]+"
# Day→year: allow "31,-2026" (Kotak folder paths) as well as "31, 2026".
DAY_YEAR_SEP = r"(?:[-_\s./]+|,\s*-?\s*)"

# Mid-month SEBI slice is the 15th; AMCs often file 13–16.
MID_DAY_MIN = 13
MID_DAY_MAX = 16
# Month-end: last few calendar days, or any day from the 27th onward.
END_NEAR_LAST = 3
END_DAY_MIN = 27


def expand_year(raw: str | int) -> int:
    n = int(raw)
    if n < 100:
        return 2000 + n
    return n


def _valid(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def normalize_blob(*parts: str) -> str:
    s = " ".join(p or "" for p in parts)
    s = re.sub(r"[\u200b\u200c\u200d\ufeff\u00a0]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def canonical_fortnightly_slice(year: int, month: int, day: int) -> str | None:
    """Map a filing date onto YYYY-MM-15 or YYYY-MM-<month-end>."""
    last = last_day(year, month)
    if MID_DAY_MIN <= day <= MID_DAY_MAX:
        return f"{year:04d}-{month:02d}-15"
    if day >= last - END_NEAR_LAST or day >= END_DAY_MIN:
        return f"{year:04d}-{month:02d}-{last:02d}"
    return None


def target_slice(as_of: str) -> str | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", as_of.strip())
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    last = last_day(y, mo)
    if d <= 16:
        return f"{y:04d}-{mo:02d}-15"
    return f"{y:04d}-{mo:02d}-{last:02d}"


_DMY_NAME = re.compile(
    rf"(?<!\d)(\d{{1,2}}){ORD}{SEP}({MONTH_RE}){SEP}(\d{{2}}|\d{{4}})(?!\d)",
    re.I,
)
_MDY_NAME = re.compile(
    rf"(?<![A-Za-z])({MONTH_RE}){SEP}(\d{{1,2}}){ORD}{DAY_YEAR_SEP}(\d{{2}}|\d{{4}})(?!\d)",
    re.I,
)
# Kotak: FortnightlyPortfolioAugust312026.xlsx / July152026
# Allow CamelCase boundary (…PortfolioAugust31…) as well as a normal word break.
_MDY_GLUED = re.compile(
    rf"(?:(?<![A-Za-z])|(?<=[a-z]))({MONTH_RE})(\d{{1,2}})(20\d{{2}})(?!\d)",
    re.I,
)
# Edelweiss: EDEL_Fortnightly_Disclosure_31Aug2026_….xlsx / 15Jul2026
_DMY_GLUED = re.compile(
    rf"(?<!\d)(\d{{1,2}})({MONTH_RE})(20\d{{2}})(?!\d)",
    re.I,
)
_ISO = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_NUMERIC = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})(?!\d)")
_COMPACT = re.compile(r"(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)")
_MONTH_YEAR = re.compile(
    rf"(?<![A-Za-z])({MONTH_RE})({SEP})(20\d{{2}}|\d{{2}})(?!\d)",
    re.I,
)
_MONTH_OF = re.compile(
    rf"month\s+of\s+({MONTH_RE})\s+(\d{{4}})",
    re.I,
)
_DAY_THEN_YEAR = re.compile(r"^,\s*-?\s*20\d{2}|^[-/.]20\d{2}")


def _month_year_token_ok(sep: str, year_raw: str, blob: str, end: int) -> bool:
    """Reject Jul_31 / Aug_15 / August-31,-2026 day tokens misread as years."""
    if len(year_raw) == 4:
        return True
    yy = int(year_raw)
    if yy > 31:
        return True
    # Underscore + 01–31 is almost always a day (Abakkus Jul_31_<hash>).
    if "_" in sep:
        return False
    # Hyphen day before a real year: as-on-August-31,-2026
    if _DAY_THEN_YEAR.match(blob[end : end + 10] or ""):
        return False
    return True


def extract_dates(*parts: str) -> list[date]:
    """Unique calendar dates found in labels / URLs / filenames."""
    blob = normalize_blob(*parts)
    if not blob:
        return []
    found: dict[str, date] = {}

    def add(dt: date | None) -> None:
        if dt is None or dt.year < 1990 or dt.year > 2100:
            return
        found[dt.isoformat()] = dt

    for m in _ISO.finditer(blob):
        add(_valid(int(m.group(1)), int(m.group(2)), int(m.group(3))))

    for m in _DMY_NAME.finditer(blob):
        mon = MONTH_NUM.get(m.group(2).lower())
        if mon:
            add(_valid(expand_year(m.group(3)), mon, int(m.group(1))))

    for m in _MDY_NAME.finditer(blob):
        mon = MONTH_NUM.get(m.group(1).lower())
        if mon:
            add(_valid(expand_year(m.group(3)), mon, int(m.group(2))))

    for m in _MDY_GLUED.finditer(blob):
        mon = MONTH_NUM.get(m.group(1).lower())
        if mon:
            add(_valid(int(m.group(3)), mon, int(m.group(2))))

    for m in _DMY_GLUED.finditer(blob):
        mon = MONTH_NUM.get(m.group(2).lower())
        if mon:
            add(_valid(int(m.group(3)), mon, int(m.group(1))))

    for m in _NUMERIC.finditer(blob):
        a, b, y = int(m.group(1)), int(m.group(2)), expand_year(m.group(3))
        if a > 12 and 1 <= b <= 12:
            add(_valid(y, b, a))
        elif b > 12 and 1 <= a <= 12:
            add(_valid(y, a, b))
        elif 1 <= a <= 12 and 1 <= b <= 12:
            add(_valid(y, b, a))  # India DMY

    for m in _COMPACT.finditer(blob):
        add(_valid(int(m.group(3)), int(m.group(2)), int(m.group(1))))

    return sorted(found.values())


def extract_all_year_months(*parts: str) -> list[tuple[int, int]]:
    """All calendar months found (full dates + month-year tokens), in order."""
    blob = normalize_blob(*parts)
    if not blob:
        return []
    found: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()

    def add(year: int, month: int) -> None:
        if year < 1990 or year > 2100 or month < 1 or month > 12:
            return
        key = (year, month)
        if key in seen:
            return
        seen.add(key)
        found.append(key)

    for dt in extract_dates(blob):
        add(dt.year, dt.month)

    for m in _MONTH_OF.finditer(blob):
        mon = MONTH_NUM.get(m.group(1).lower())
        if mon:
            add(expand_year(m.group(2)), mon)

    for m in _MONTH_YEAR.finditer(blob):
        mon = MONTH_NUM.get(m.group(1).lower())
        if not mon:
            continue
        if not _month_year_token_ok(m.group(2), m.group(3), blob, m.end()):
            continue
        add(expand_year(m.group(3)), mon)

    return found


def extract_year_month(*parts: str) -> tuple[int, int] | None:
    """Best calendar month from a label or filename.

    When several month-years appear (e.g. scheme maturity APR 2028 + as-of
    Jul 2026), prefer the last one — disclosure as-of usually trails the name.
    """
    yms = extract_all_year_months(*parts)
    if not yms:
        return None
    return yms[-1]


def dates_match_as_of(*parts: str, as_of: str) -> bool:
    """True when any extracted date belongs to the same fortnightly slice as as_of."""
    want = target_slice(as_of)
    if not want:
        return True
    y, mo, _ = (int(x) for x in as_of.split("-"))
    for dt in extract_dates(*parts):
        if (dt.year, dt.month) != (y, mo):
            continue
        got = canonical_fortnightly_slice(dt.year, dt.month, dt.day)
        if got == want:
            return True
    return False


def first_iso(*parts: str) -> str | None:
    dates = extract_dates(*parts)
    return dates[0].isoformat() if dates else None


def year_month_key(*parts: str) -> str | None:
    ym = extract_year_month(*parts)
    if not ym:
        return None
    return f"{ym[0]:04d}-{ym[1]:02d}"


def blob_matches_year_month(*parts: str, year: int, month: int) -> bool | None:
    """True/False if a calendar date or month was parsed; None if nothing dated."""
    yms = extract_all_year_months(*parts)
    if not yms:
        return None
    return any(y == year and m == month for y, m in yms)
