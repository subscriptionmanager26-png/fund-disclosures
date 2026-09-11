"""Disclosure folder keys: date-keyed trees under data/disclosures/{cadence}/{key}/."""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date

AS_OF_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


def month_end_iso(year: int, month: int) -> str:
    last = monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{last:02d}"


def disclosure_storage_keys(period: str, disclosure_type: str) -> list[str]:
    """Folder keys for a fetch/parse period argument.

    Fortnightly YYYY-MM expands to both mid-month (15) and month-end so parsers
    and daily jobs never miss packs that only appear on the 31st.
    """
    if AS_OF_RE.match(period):
        return [period]
    if PERIOD_RE.match(period):
        y, m = map(int, period.split("-"))
        end = month_end_iso(y, m)
        if disclosure_type == "fortnightly":
            return [f"{period}-15", end]
        return [end]
    return [period]


def disclosure_storage_key(period: str, disclosure_type: str) -> str:
    """Primary folder key (first of disclosure_storage_keys)."""
    return disclosure_storage_keys(period, disclosure_type)[0]


def disclosure_period_candidates(as_of: str, disclosure_type: str) -> list[str]:
    """Disclosure/parsed dirs to scan for a calendar as-of date."""
    keys: list[str] = []
    if AS_OF_RE.match(as_of):
        keys.append(as_of)
    ym = as_of[:7]
    if PERIOD_RE.match(ym) and ym not in keys:
        keys.append(ym)
    return keys


def disclosure_search_keys(period: str, disclosure_type: str) -> list[str]:
    """Dirs to read when --period is YYYY-MM or YYYY-MM-DD."""
    if AS_OF_RE.match(period):
        keys = [period]
        ym = period[:7]
        if ym != period and ym not in keys:
            keys.append(ym)
        return keys
    if PERIOD_RE.match(period):
        out = list(disclosure_storage_keys(period, disclosure_type))
        if period not in out:
            out.append(period)
        return out
    return [period]


def discover_period_dirs(root, disclosure_type: str) -> list[str]:
    """All period folder names under a disclosure or parsed cadence root."""
    base = root / disclosure_type
    if not base.is_dir():
        return []
    out = []
    for p in sorted(base.iterdir()):
        if p.is_dir() and p.name != "latest":
            out.append(p.name)
    return out
