#!/usr/bin/env python3
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from disclosure_date import (
    blob_matches_year_month,
    dates_match_as_of,
    extract_all_year_months,
    extract_dates,
    extract_year_month,
)
from asof_filter import file_matches_asof_strict


class DisclosureDateTests(unittest.TestCase):
    def test_nippon_abbrev_and_two_digit_year(self):
        self.assertEqual(
            extract_year_month("Debt Schemes Portfolio as on 15th Aug 2026"),
            (2026, 8),
        )
        self.assertEqual(
            extract_year_month(
                "",
                "/InvestorServices/FactsheetsDocuments/NIMF-FORTNIGHTLY-PORTFOLIO-15-Aug-26.xls",
            ),
            (2026, 8),
        )
        self.assertEqual(
            extract_dates("NIMF-FORTNIGHTLY-PORTFOLIO-31-Aug-26.xls")[0],
            date(2026, 8, 31),
        )

    def test_franklin_14_aug_maps_to_mid_month_slice(self):
        name = "Fortnightly-Portfolio-ISIN-14-Aug-2026.xlsx"
        self.assertEqual(extract_dates(name)[0], date(2026, 8, 14))
        self.assertTrue(dates_match_as_of(name, as_of="2026-08-15"))
        self.assertFalse(dates_match_as_of(name, as_of="2026-08-31"))
        self.assertTrue(file_matches_asof_strict(name, name, "2026-08-15"))
        self.assertFalse(file_matches_asof_strict(name, name, "2026-08-31"))

    def test_july_full_name_still_matches(self):
        name = "Fortnightly-Portfolio-ISIN-15-July-2026.xlsx"
        self.assertTrue(file_matches_asof_strict(name, name, "2026-07-15"))
        self.assertFalse(file_matches_asof_strict(name, name, "2026-07-31"))

    def test_choice_and_helios_live_filenames(self):
        self.assertTrue(
            dates_match_as_of(
                "Choice Mutual Fund_Fortnightly Portfolio_OV_15 Aug 2026.xlsx",
                as_of="2026-08-15",
            )
        )
        self.assertTrue(
            dates_match_as_of(
                "helios-overnight-fund-fortnightly-portfolio-as-on-15th-august-2026.xls",
                as_of="2026-08-15",
            )
        )
        self.assertFalse(
            dates_match_as_of(
                "helios-overnight-fund-fortnightly-portfolio-as-on-31st-august-2026.xls",
                as_of="2026-08-15",
            )
        )
        self.assertTrue(
            dates_match_as_of("Debt Schemes Portfolio as on 31st Aug 2026", as_of="2026-08-31")
        )
        self.assertTrue(
            file_matches_asof_strict("Helios-OV-August-2026.xls", "", "2026-08-15")
        )

    def test_kotak_glued_month_day_year(self):
        for name, want in [
            ("FortnightlyPortfolioJuly312026.xlsx", date(2026, 7, 31)),
            ("FortnightlyPortfolioJuly152026.xlsx", date(2026, 7, 15)),
            ("FortnightlyPortfolioAugust312026.xlsx", date(2026, 8, 31)),
            ("FortnightlyPortfolioAugust152026.xlsx", date(2026, 8, 15)),
        ]:
            self.assertEqual(extract_dates(name)[0], want)
            self.assertTrue(
                file_matches_asof_strict(name, name, want.isoformat())
            )

    def test_edelweiss_glued_day_month_year(self):
        for name, want, as_of, other in [
            (
                "EDEL_Fortnightly_Disclosure_31Aug2026_04092026104330.xlsx",
                date(2026, 8, 31),
                "2026-08-31",
                "2026-08-15",
            ),
            (
                "EDEL_Fortnightly_Disclosure_15Aug2026_20082026095502.xlsx",
                date(2026, 8, 15),
                "2026-08-15",
                "2026-08-31",
            ),
        ]:
            self.assertEqual(extract_dates(name)[0], want)
            self.assertTrue(dates_match_as_of(name, as_of=as_of))
            self.assertFalse(dates_match_as_of(name, as_of=other))
            self.assertTrue(file_matches_asof_strict(name, name, as_of))
            self.assertFalse(file_matches_asof_strict(name, name, other))

    def test_kotak_path_comma_hyphen_year(self):
        url = (
            "https://vatseelabs-s3.kotakmf.com/FAD/Portfolios/"
            "Fortnightly-Portfolio-as-on-August-31,-2026/"
            "FortnightlyPortfolioAugust312026.xlsx"
        )
        dates = extract_dates(url)
        self.assertIn(date(2026, 8, 31), dates)
        self.assertTrue(blob_matches_year_month(url, year=2026, month=8))
        self.assertFalse(blob_matches_year_month(url, year=2031, month=8))
        self.assertTrue(file_matches_asof_strict(url, url, "2026-08-31"))
        self.assertFalse(file_matches_asof_strict(url, url, "2026-08-15"))
        self.assertTrue(dates_match_as_of(url, as_of="2026-08-31"))
        self.assertFalse(dates_match_as_of(url, as_of="2026-08-15"))

    def test_abakkus_jul_31_hash_not_year_2031(self):
        name = "Final_Monthly_Portfolio_Jul_31_a313e9e6dd.xls"
        self.assertEqual(extract_dates(name), [])
        self.assertIsNone(extract_year_month(name))
        self.assertIsNone(blob_matches_year_month(name, year=2026, month=7))
        # No false month → disclosure-page keep (strict filter allows undated).
        self.assertTrue(file_matches_asof_strict(name, name, "2026-07-31"))

    def test_pgim_maturity_year_does_not_hide_as_of(self):
        name = "PGIM INDIA CRISIL IBX GILT INDEX - APR 2028  Jul 2026.xlsx"
        self.assertEqual(
            extract_all_year_months(name),
            [(2028, 4), (2026, 7)],
        )
        self.assertEqual(extract_year_month(name), (2026, 7))
        self.assertTrue(blob_matches_year_month(name, year=2026, month=7))
        self.assertTrue(file_matches_asof_strict(name, name, "2026-07-31"))


if __name__ == "__main__":
    unittest.main()
