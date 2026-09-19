#!/usr/bin/env python3
"""Tests for as-of folder clamping (monthly vs fortnightly)."""
from __future__ import annotations

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from asof_filter import canonical_as_of_for_folder


class CanonicalAsOfFolderTests(unittest.TestCase):
    def test_monthly_clamps_maturity_year_to_folder(self):
        self.assertEqual(
            canonical_as_of_for_folder("2028-04-30", "2026-08-31", "monthly"),
            "2026-08-31",
        )

    def test_monthly_clamps_missing_extracted(self):
        self.assertEqual(
            canonical_as_of_for_folder(None, "2026-08-31", "monthly"),
            "2026-08-31",
        )

    def test_fortnightly_clamps_same_month_mid(self):
        self.assertEqual(
            canonical_as_of_for_folder("2026-09-14", "2026-09-15", "fortnightly"),
            "2026-09-15",
        )

    def test_fortnightly_off_month_maturity_uses_folder(self):
        self.assertEqual(
            canonical_as_of_for_folder("2028-04-30", "2026-09-15", "fortnightly"),
            "2026-09-15",
        )


if __name__ == "__main__":
    unittest.main()
