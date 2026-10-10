"""Lightweight regression tests for AMC fetch helpers."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_bajaj import is_monthly_for, month_search_fragments
from fetch_boi import docname_to_month_key


class TestBoiDocname(unittest.TestCase):
    def test_september_abbrev(self) -> None:
        self.assertEqual(
            docname_to_month_key("MONTHLY-PORTFOLIO - 30-SEP-2026"),
            "2026-09",
        )

    def test_august_full_name(self) -> None:
        self.assertEqual(
            docname_to_month_key("MONTHLY-PORTFOLIO - 31-AUGUST-2026"),
            "2026-08",
        )


class TestBajajSearch(unittest.TestCase):
    def test_sep_slug_variants(self) -> None:
        frags = month_search_fragments("2026-09")
        self.assertIn("as-on-30-sep-2026", frags)
        self.assertIn("as-on-30-sep2026", frags)

    def test_monthly_matches_sep2026_slug(self) -> None:
        item = {
            "slug": "bajaj-finserv-flexi-cap-fund_monthly-portfolio-as-on-30-sep2026-xls",
            "title": {"rendered": ""},
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "source_url": "https://example.com/file.xls.xlsx",
        }
        self.assertTrue(is_monthly_for(item, "as-on-30-sep2026"))


if __name__ == "__main__":
    unittest.main()
