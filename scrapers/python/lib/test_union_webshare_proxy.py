#!/usr/bin/env python3
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from union_webshare_proxy import proxy_candidates, proxy_configured, proxy_url_for_host


class TestUnionProxy(unittest.TestCase):
    def test_proxy_url_for_host(self) -> None:
        with patch.dict(
            os.environ,
            {
                "UNION_WEBSHARE_PROXY_USER": "u",
                "UNION_WEBSHARE_PROXY_PASSWORD": "p",
            },
            clear=False,
        ):
            self.assertEqual(
                proxy_url_for_host("1.2.3.4:9999"),
                "http://u:p@1.2.3.4:9999",
            )

    def test_proxy_configured(self) -> None:
        with patch.dict(os.environ, {"UNION_WEBSHARE_PROXY_URL": "http://a:b@c:1"}, clear=False):
            self.assertTrue(proxy_configured())
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(proxy_configured())

    def test_proxy_candidates_default_hosts(self) -> None:
        with patch.dict(
            os.environ,
            {"UNION_WEBSHARE_PROXY_USER": "u", "UNION_WEBSHARE_PROXY_PASSWORD": "p"},
            clear=False,
        ):
            urls = proxy_candidates()
            self.assertGreaterEqual(len(urls), 3)
            self.assertTrue(all("31.59.20.176" in u or "45.38." in u or "31.58." in u for u in urls))


if __name__ == "__main__":
    unittest.main()
