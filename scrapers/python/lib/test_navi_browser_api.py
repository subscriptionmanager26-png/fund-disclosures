#!/usr/bin/env python3
"""Unit tests for Navi Browser Rendering helpers."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from navi_browser_api import (
    RESULT_ELEMENT_ID,
    build_attempt_queries,
    build_injected_script,
    fiscal_year_for_month,
    parse_pipeline_result_html,
)


class TestNaviBrowserApi(unittest.TestCase):
    def test_fiscal_year(self) -> None:
        self.assertEqual(fiscal_year_for_month(2026, 9), "2026-2027")
        self.assertEqual(fiscal_year_for_month(2026, 3), "2025-2026")

    def test_monthly_attempts_exact_month(self) -> None:
        attempts = build_attempt_queries(2026, 9, fortnightly=False, walk_back_months=0)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["value"], "September")
        self.assertEqual(attempts[0]["financial_year"], "2026-2027")
        self.assertEqual(attempts[0]["category"], "884")

    def test_fortnightly_attempts_two_slices(self) -> None:
        attempts = build_attempt_queries(2026, 9, fortnightly=True, walk_back_months=0)
        self.assertEqual(len(attempts), 2)
        self.assertIn("September 1-15", attempts[0]["value"])

    def test_injected_script_contains_targets(self) -> None:
        script = build_injected_script(
            [{"month_key": "2026-09", "attempts": build_attempt_queries(2026, 9, fortnightly=False)}]
        )
        self.assertIn("navi_property", script)
        self.assertIn("2026-09", script)

    def test_parse_pipeline_result_html(self) -> None:
        payload = {
            "targets": [
                {
                    "month_key": "2026-09",
                    "rows": [{"title": "Navi Flexi Cap Fund", "url": "https://example.com/a.xlsx"}],
                }
            ]
        }
        html = f'<html><body><pre id="{RESULT_ELEMENT_ID}">{json.dumps(payload)}</pre></body></html>'
        parsed = parse_pipeline_result_html(html)
        self.assertEqual(parsed["targets"][0]["month_key"], "2026-09")
        self.assertEqual(len(parsed["targets"][0]["rows"]), 1)

    @patch("navi_browser_api.httpx.Client")
    def test_fetch_documents_via_browser_mock(self, client_cls) -> None:
        from navi_browser_api import fetch_documents_via_browser  # noqa: PLC0415

        payload = {
            "targets": [
                {
                    "month_key": "2026-09",
                    "rows": [{"title": "Test", "url": "https://cdn/x.xlsx"}],
                }
            ]
        }
        html = f'<pre id="{RESULT_ELEMENT_ID}">{json.dumps(payload)}</pre>'
        mock_resp = unittest.mock.Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "result": html}
        mock_resp.raise_for_status = unittest.mock.Mock()
        client_cls.return_value.__enter__.return_value.post.return_value = mock_resp

        with patch.dict(
            "os.environ",
            {"CLOUDFLARE_ACCOUNT_ID": "acct", "CLOUDFLARE_API_TOKEN": "tok"},
            clear=False,
        ):
            out = fetch_documents_via_browser([(2026, 9)], fortnightly=False)
        self.assertIn("2026-09", out)
        self.assertEqual(out["2026-09"][0]["title"], "Test")


if __name__ == "__main__":
    unittest.main()
