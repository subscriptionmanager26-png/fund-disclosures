#!/usr/bin/env python3
"""Tests for portfolio as-of extraction (Helios Excel serial / NAV history trap)."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from amc_parsers.common import (  # noqa: E402
    assert_zip_readable,
    excel_serial_to_iso,
    extract_as_of,
    parse_as_of,
    sheet_rows_xlrd,
)


class AsOfExtractionTests(unittest.TestCase):
    def test_excel_serial_july_15_2026(self):
        # 1899-12-30 + 46218 days ≈ 2026-07-15
        self.assertEqual(excel_serial_to_iso(46218), "2026-07-15")

    def test_portfolio_statement_serial_beats_nav_history(self):
        rows = [
            ["Helios Mutual Fund"],
            ["SCHEME NAME :", "Helios Overnight Fund"],
            ["PORTFOLIO STATEMENT AS ON :", "46218"],
            ["Name of the Instrument / Issuer", "ISIN"],
            ["TREPS", ""],
            ["NAV Histrory:"],
            [
                "Option / Plan",
                "NAV Rs. per unit as on June 30, 2026",
                "NAV Rs. per unit as on July 15, 2026",
            ],
        ]
        self.assertEqual(
            extract_as_of(
                rows,
                filename="Helios-Overnight-Fund-Fortnightly-Portfolio-as-on-15th-July-2026.xls",
            ),
            "2026-07-15",
        )

    def test_nav_history_alone_ignored_when_filename_has_date(self):
        rows = [
            ["NAV Histrory:"],
            [
                "Option / Plan",
                "NAV Rs. per unit as on June 30, 2026",
                "NAV Rs. per unit as on July 15, 2026",
            ],
        ]
        self.assertEqual(
            extract_as_of(
                rows,
                filename="Helios-Overnight-Fund-Fortnightly-Portfolio-as-on-15th-July-2026.xls",
            ),
            "2026-07-15",
        )

    def test_filename_wins_over_conflicting_sheet_date(self):
        rows = [["PORTFOLIO STATEMENT AS ON :", "30 June 2026"]]
        self.assertEqual(
            extract_as_of(
                rows,
                filename="Helios-Overnight-Fund-Fortnightly-Portfolio-as-on-15th-July-2026.xls",
            ),
            "2026-07-15",
        )

    def test_live_helios_overnight_xls(self):
        root = Path(__file__).resolve().parents[2]
        p = (
            root
            / "data/disclosures/fortnightly/2026-07-15/helios-mutual-fund"
            / "Helios-Overnight-Fund-Fortnightly-Portfolio-as-on-15th-July-2026.xls"
        )
        if not p.exists():
            self.skipTest("Helios July 15 fixture not on disk")
        sheets = sheet_rows_xlrd(p)
        hof = next(rows for name, rows in sheets if name == "HOF")
        self.assertEqual(
            extract_as_of(hof, filename=p.name),
            "2026-07-15",
        )
        # xlrd path must convert the statement date cell (not leave serial 46218)
        banner = " | ".join(hof[3])
        self.assertIn("2026-07-15", banner)

    def test_assert_zip_rejects_truncated(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "pack.zip"
            # Local file header only — no EOCD (ABSL failure mode).
            bad.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
            with self.assertRaises(zipfile.BadZipFile):
                assert_zip_readable(bad)

    def test_assert_zip_accepts_valid(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "pack.zip"
            with zipfile.ZipFile(good, "w") as zf:
                zf.writestr("sheet.xlsx", b"not-a-real-xlsx")
            assert_zip_readable(good)

    def test_parse_as_of_filename(self):
        self.assertEqual(
            parse_as_of("Helios-Overnight-Fund-Fortnightly-Portfolio-as-on-15th-July-2026.xls"),
            "2026-07-15",
        )


if __name__ == "__main__":
    unittest.main()
