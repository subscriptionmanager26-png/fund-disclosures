#!/usr/bin/env python3
"""
Resolve quarantined disclosure files by the as-of date printed INSIDE the workbook.

Why: AMCs keep renaming files (hash names, "October" with no year, "oct15",
upload timestamps). Filename rules reject those, and the AMC then looks
"empty" until someone patches a regex. The workbook itself always carries a
"Portfolio as on 31-Oct-2026" banner and a holdings table, so content is the
reliable signal.

fetch-period.js downloads undated files to:
    data/quarantine/{type}/{storageKey}/{amc}/{file}

For each file this script:
  1. reads every sheet (zip members too),
  2. keeps sheets that look like a portfolio (ISIN column + %NAV / market value)
     and have an "as on / as of / month ended" banner,
  3. promotes the file to data/disclosures/{type}/{storageKey}/{amc}/ when the
     banner date matches the slice (monthly: same month; fortnightly: same
     15th / month-end slice, and month-end fortnightly must say "fortnight"),
  4. leaves everything else in quarantine with a reason.

Verdicts: data/probes/quarantine-{type}-{storageKey}.json

Usage:
  python3 scrapers/python/resolve_quarantine.py --type=monthly --period=2026-10-31
  python3 scrapers/python/resolve_quarantine.py --type=fortnightly --period=2026-10-15 --dry-run
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "parsers"))

from amc_parsers.common import (  # noqa: E402
    _AS_OF_LABEL_RE,
    _AS_OF_NOISE_RE,
    _row_as_of,
    load_sheets,
)

SPREADSHEET_EXT = (".xlsx", ".xls", ".xlsm", ".xlsb")
MAX_SHEETS = 60
BANNER_ROWS = 40
TABLE_ROWS = 80

_ISIN_HEADER_RE = re.compile(r"(?i)\bisin\b")
_VALUE_HEADER_RE = re.compile(
    r"(?i)%\s*(?:to|of)\s*(?:net\s*assets?|nav|aum)|market\s*(?:value|/\s*fair)|"
    r"fair\s*value|rs\.?\s*in\s*lakhs?|value\s*\(rs"
)
_FORTNIGHT_RE = re.compile(r"(?i)fort\s*n?ight")


def last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def slice_for(as_of: str) -> str | None:
    """Canonical fortnightly slice (…-15 or month-end) for a calendar date."""
    try:
        y, m, d = (int(x) for x in as_of.split("-"))
    except ValueError:
        return None
    ld = last_day(y, m)
    if 13 <= d <= 16:
        return f"{y:04d}-{m:02d}-15"
    if d >= ld - 3:
        return f"{y:04d}-{m:02d}-{ld:02d}"
    return None


def sheet_evidence(rows: list[list[str]]) -> dict:
    """Portfolio-ness + banner as-of for one sheet."""
    head = rows[:TABLE_ROWS]
    has_isin = any(_ISIN_HEADER_RE.search(" ".join(c for c in r if c)) for r in head)
    has_value = any(_VALUE_HEADER_RE.search(" ".join(c for c in r if c)) for r in head)
    as_of = None
    for row in rows[:BANNER_ROWS]:
        joined = " | ".join(c for c in row if c)
        if not joined or not _AS_OF_LABEL_RE.search(joined) or _AS_OF_NOISE_RE.search(joined):
            continue
        as_of = _row_as_of(row)
        if as_of:
            break
    text = " ".join(" ".join(c for c in r if c) for r in rows[:BANNER_ROWS])
    return {
        "portfolio": bool(has_isin and has_value),
        "as_of": as_of,
        "fortnight": bool(_FORTNIGHT_RE.search(text)),
    }


def workbook_evidence(path: Path) -> list[dict]:
    """Evidence for each sheet in a spreadsheet, or each spreadsheet in a zip."""
    out: list[dict] = []
    if path.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory() as td, zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                name = Path(info.filename).name
                if info.is_dir() or not name.lower().endswith(SPREADSHEET_EXT):
                    continue
                target = Path(td) / f"{len(out)}_{name}"
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                for ev in workbook_evidence(target):
                    out.append({**ev, "member": name})
        return out
    for sheet_name, rows in load_sheets(path)[:MAX_SHEETS]:
        out.append({"sheet": sheet_name, **sheet_evidence(rows)})
    return out


def decide(evidence: list[dict], *, disclosure_type: str, storage_key: str, filename: str) -> dict:
    """Return {verdict: promote|keep_quarantined, reason, ...}."""
    portfolio = [e for e in evidence if e["portfolio"]]
    dated = [e for e in portfolio if e["as_of"]]
    found = sorted({e["as_of"] for e in dated})
    base = {"as_of_found": found, "sheets": len(evidence), "portfolio_sheets": len(portfolio)}
    if not portfolio:
        return {**base, "verdict": "keep_quarantined", "reason": "not_a_portfolio_workbook"}
    if not dated:
        return {**base, "verdict": "keep_quarantined", "reason": "no_as_of_banner"}

    want_ym = storage_key[:7]
    if disclosure_type == "monthly":
        match = [e for e in dated if e["as_of"][:7] == want_ym]
    else:
        match = [e for e in dated if slice_for(e["as_of"]) == storage_key]
    other = len(dated) - len(match)
    if not match:
        return {**base, "verdict": "keep_quarantined", "reason": "as_of_other_period"}
    if other > len(match):
        return {**base, "verdict": "keep_quarantined", "reason": "mostly_other_period"}

    is_month_end = storage_key[8:] == f"{last_day(int(storage_key[:4]), int(storage_key[5:7])):02d}"
    if disclosure_type == "fortnightly" and is_month_end:
        # A month-end monthly equity pack has the same as-of as fortnightly-31;
        # only promote into the fortnightly tree with explicit fortnight evidence.
        if not (_FORTNIGHT_RE.search(filename) or any(e["fortnight"] for e in match)):
            return {**base, "verdict": "keep_quarantined", "reason": "month_end_without_fortnight_marker"}
    if disclosure_type == "monthly" and (
        _FORTNIGHT_RE.search(filename) or all(e["fortnight"] for e in match)
    ):
        return {**base, "verdict": "keep_quarantined", "reason": "fortnightly_pack_in_monthly"}
    return {**base, "verdict": "promote", "reason": "content_as_of_matches"}


def resolve(root: Path, disclosure_type: str, storage_key: str, *, dry_run: bool = False) -> dict:
    qdir = root / "data" / "quarantine" / disclosure_type / storage_key
    report: dict = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "type": disclosure_type,
        "storageKey": storage_key,
        "dry_run": dry_run,
        "amcs": {},
        "promoted": 0,
        "kept_quarantined": 0,
    }
    if not qdir.is_dir():
        return report
    for amc_dir in sorted(p for p in qdir.iterdir() if p.is_dir()):
        rows = []
        for f in sorted(p for p in amc_dir.iterdir() if p.is_file()):
            entry: dict = {"file": f.name}
            if not f.name.lower().endswith(SPREADSHEET_EXT + (".zip",)):
                entry.update(verdict="keep_quarantined", reason="unsupported_extension")
            else:
                try:
                    ev = workbook_evidence(f)
                    entry.update(
                        decide(ev, disclosure_type=disclosure_type, storage_key=storage_key, filename=f.name)
                    )
                except Exception as e:  # unreadable / truncated workbook
                    entry.update(verdict="keep_quarantined", reason="unreadable", error=str(e)[:300])
            if entry["verdict"] == "promote":
                dest_dir = root / "data" / "disclosures" / disclosure_type / storage_key / amc_dir.name
                dest = dest_dir / f.name
                entry["promoted_to"] = str(dest.relative_to(root))
                if not dry_run:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    if dest.exists():
                        entry["note"] = "already_present"
                        f.unlink()
                    else:
                        shutil.move(str(f), dest)
                report["promoted"] += 1
            else:
                report["kept_quarantined"] += 1
            rows.append(entry)
        if rows:
            report["amcs"][amc_dir.name] = rows
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--type", required=True, choices=["monthly", "fortnightly"])
    ap.add_argument("--period", required=True, help="storage key YYYY-MM-DD")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.period):
        raise SystemExit("--period must be YYYY-MM-DD (the fetch storage key)")
    root = Path(args.root)
    report = resolve(root, args.type, args.period, dry_run=args.dry_run)
    probes = root / "data" / "probes"
    probes.mkdir(parents=True, exist_ok=True)
    out = probes / f"quarantine-{args.type}-{args.period}.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"quarantine {args.type} {args.period}: promoted={report['promoted']} "
        f"kept={report['kept_quarantined']} → {out}"
    )
    for amc, rows in report["amcs"].items():
        for r in rows:
            print(f"  {amc}: {r['verdict']:<17} {r['reason']:<34} {r['file']}")


if __name__ == "__main__":
    main()
