#!/usr/bin/env python3
"""Compare Groww consolidated monthly workbook tabs vs parsed scheme folders.

The statutory monthly pack is one multi-sheet xlsx (IB01… tabs). Fortnightly
files reuse letter aliases (LF → IB02) — not extra schemes.

Usage:
  .venv/bin/python3 scripts/audit-groww-scheme-count.py --period 2026-09-30
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "parsers"))
sys.path.insert(0, str(ROOT / "scrapers" / "python" / "lib"))

from amc_parsers.family import parse_file  # noqa: E402

try:
    import openpyxl
except ImportError:
    print("openpyxl required", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--period", default="2026-09-30", help="YYYY-MM-DD storage key")
    ap.add_argument("--amc", default="groww-mutual-fund")
    args = ap.parse_args()

    disc_dir = ROOT / "data" / "disclosures" / "monthly" / args.period / args.amc
    parsed_dir = ROOT / "data" / "parsed" / "monthly" / args.period / args.amc
    files = sorted(
        p
        for p in disc_dir.glob("*.xlsx")
        if "monthly portfolio" in p.name.lower()
    )
    if not files:
        print(json.dumps({"error": "no monthly portfolio xlsx", "dir": str(disc_dir)}, indent=2))
        return 1

    report = {"period": args.period, "workbooks": []}
    for path in files:
        wb = openpyxl.load_workbook(path, read_only=True)
        sheets = [s for s in wb.sheetnames if s not in {"XDO_METADATA"}]
        wb.close()
        schemes = parse_file(
            path,
            amc_id=args.amc,
            family="sebi_title",
            prefer_leading_code=True,
            multi_sheet=True,
        )
        parsed_n = len([d for d in parsed_dir.iterdir() if d.is_dir()]) if parsed_dir.is_dir() else 0
        report["workbooks"].append(
            {
                "file": path.name,
                "scheme_tabs": len(sheets),
                "parsed_schemes": len(schemes),
                "parsed_folders_on_disk": parsed_n,
                "tab_sample": sheets[:3] + (["…"] if len(sheets) > 6 else []) + sheets[-3:],
            }
        )

    print(json.dumps(report, indent=2))
    ok = all(
        w["scheme_tabs"] == w["parsed_schemes"] for w in report["workbooks"]
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
