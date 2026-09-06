#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from amc_parsers.disclosure_period import (  # noqa: E402
    disclosure_search_keys,
    disclosure_storage_key,
    disclosure_storage_keys,
)


class DisclosurePeriodTests(unittest.TestCase):
    def test_fortnightly_ym_expands_both_slices(self):
        self.assertEqual(
            disclosure_storage_keys("2026-08", "fortnightly"),
            ["2026-08-15", "2026-08-31"],
        )
        self.assertEqual(
            disclosure_search_keys("2026-08", "fortnightly"),
            ["2026-08-15", "2026-08-31", "2026-08"],
        )
        self.assertEqual(disclosure_storage_key("2026-08", "fortnightly"), "2026-08-15")

    def test_monthly_ym_is_month_end(self):
        self.assertEqual(
            disclosure_storage_keys("2026-08", "monthly"),
            ["2026-08-31"],
        )

    def test_full_date_is_single(self):
        self.assertEqual(
            disclosure_storage_keys("2026-08-31", "fortnightly"),
            ["2026-08-31"],
        )


if __name__ == "__main__":
    unittest.main()
