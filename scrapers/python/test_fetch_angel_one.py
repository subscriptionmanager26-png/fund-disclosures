"""Angel One: props extraction from the Next.js RSC payload (Nov 2026 layout change)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_angel_one as a  # noqa: E402


def _compact(obj) -> str:
    # The site emits compact JSON ("null,{"), not json.dumps' default "null, {".
    return json.dumps(obj, separators=(",", ":"))


def _page(chunks: list[str]) -> str:
    body = "".join(
        f"<script>self.__next_f.push([1,{json.dumps(c)}])</script>" for c in chunks
    )
    return f"<html><body>{body}</body></html>"


DISCLOSURES = {
    "1": {
        "fields": {
            "Sub_Section": "Portfolio Disclosures",
            "Dropdown": "Monthly",
            "Dropdown2": "October 2026",
            "post_guid": [
                "https://cms.angelonemf.com/x/Monthly-Portfolio-October-2026-Angel-One-Nifty-50-ETF.xlsx"
            ],
        }
    },
    "2": {
        "fields": {
            "Sub_Section": "Portfolio Disclosures",
            "Dropdown": "Monthly",
            "Dropdown2": "",
            "post_guid": [
                "https://cms.angelonemf.com/x/Monthly-Portfolio-September-2026-Angel-One-Gold-ETF-1-1.xlsx"
            ],
        }
    },
}


def test_single_chunk_with_layout_object_first():
    """Nov 2026: one chunk; the first null,{...} is the root layout, not the props."""
    props = {"investorsData": {}, "disclosuresData": DISCLOSURES, "note": "brace { inside } string"}
    chunk = (
        '0:["$","html",null,{"lang":"en","children":["$","body",null,{"children":"x"}]}]\n'
        "f:" + _compact(["$", "$L1a", None, props]) + "\n"
    )
    rows = a.extract_rows(_page([chunk]))
    got = sorted((r["year"], r["month"]) for r in rows)
    assert got == [(2026, 9), (2026, 10)]


def test_legacy_separate_chunk_still_works():
    props = {"investorsData": {}, "disclosuresData": DISCLOSURES}
    rows = a.extract_rows(_page(["0:[]\n", "f:" + _compact(["$", "$L1a", None, props])]))
    assert len(rows) == 2
