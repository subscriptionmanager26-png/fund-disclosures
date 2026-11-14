#!/usr/bin/env python3
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import union_webshare_proxy as uwp
from union_webshare_proxy import describe_proxy, proxy_candidates, proxy_configured, proxy_url_for_host


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

    def test_url_does_not_shadow_user_password(self) -> None:
        """Nov 2026: a stale UNION_WEBSHARE_PROXY_URL hid valid USER/PASSWORD (all 407)."""
        env = {
            "UNION_WEBSHARE_PROXY_URL": "http://old:stale@9.9.9.9:1",
            "UNION_WEBSHARE_PROXY_USER": "u",
            "UNION_WEBSHARE_PROXY_PASSWORD": "p",
            "UNION_WEBSHARE_PROXY_HOSTS": "1.1.1.1:10,2.2.2.2:20",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                proxy_candidates(),
                ["http://old:stale@9.9.9.9:1", "http://u:p@1.1.1.1:10", "http://u:p@2.2.2.2:20"],
            )
            self.assertEqual(describe_proxy("http://u:p@1.1.1.1:10"), "1.1.1.1:10 (USER/PASSWORD)")

    def test_all_proxies_failing_names_each_without_secrets(self) -> None:
        env = {"UNION_WEBSHARE_PROXY_USER": "u", "UNION_WEBSHARE_PROXY_PASSWORD": "s3cret", "UNION_WEBSHARE_PROXY_HOSTS": "1.1.1.1:10"}

        def boom(url, *, headers, proxy, timeout):
            raise RuntimeError("curl: (7) CONNECT tunnel failed, response 407")

        with patch.dict(os.environ, env, clear=True), patch.object(uwp, "_get_via_curl_cffi", boom):
            with self.assertRaises(ConnectionError) as ctx:
                uwp.http_get("https://www.unionmf.com/", referer="https://www.unionmf.com/")
        msg = str(ctx.exception)
        self.assertIn("1.1.1.1:10 (USER/PASSWORD)", msg)
        self.assertIn("407 proxy authentication rejected", msg)
        self.assertNotIn("s3cret", msg)


if __name__ == "__main__":
    unittest.main()
