"""
Webshare HTTP proxy for unionmf.com (datacenter IPs are blocked at TCP/TLS).

Secrets via env only — never commit credentials. See docs/UNION_MF_WEBSHARE_PROXY.md.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.request import ProxyHandler, build_opener

DEFAULT_PROXY_HOSTS = (
    "31.59.20.176:6754",
    "45.38.107.97:6014",
    "31.58.9.4:6077",
)

HTTP_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}


def _proxy_user() -> str:
    return (os.environ.get("UNION_WEBSHARE_PROXY_USER") or "").strip()


def _proxy_password() -> str:
    return (os.environ.get("UNION_WEBSHARE_PROXY_PASSWORD") or "").strip()


def proxy_url_for_host(host_port: str) -> str | None:
    """Build http://user:pass@host:port when credentials are set."""
    direct = (os.environ.get("UNION_WEBSHARE_PROXY_URL") or "").strip()
    if direct:
        return direct
    user, pw = _proxy_user(), _proxy_password()
    if not user or not pw or not host_port:
        return None
    return f"http://{user}:{pw}@{host_port}"


def proxy_candidates() -> list[str]:
    """Every configured proxy, most specific first.

    UNION_WEBSHARE_PROXY_URL used to shadow USER/PASSWORD entirely (the same URL
    was returned for every host), so one stale secret meant HTTP 407 on every
    attempt (Nov 2026). Now: the explicit URL first, then user:pass on each
    host. Duplicates removed.
    """
    raw = (os.environ.get("UNION_WEBSHARE_PROXY_HOSTS") or "").strip()
    hosts = [h.strip() for h in raw.split(",") if h.strip()] if raw else list(DEFAULT_PROXY_HOSTS)
    urls: list[str] = []
    direct = (os.environ.get("UNION_WEBSHARE_PROXY_URL") or "").strip()
    if direct:
        urls.append(direct)
    user, pw = _proxy_user(), _proxy_password()
    if user and pw:
        for host in hosts:
            urls.append(f"http://{user}:{pw}@{host}")
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


def describe_proxy(url: str) -> str:
    """host:port plus where the credentials came from — never the secret itself."""
    host = url.rsplit("@", 1)[-1]
    src = "UNION_WEBSHARE_PROXY_URL" if url == (os.environ.get("UNION_WEBSHARE_PROXY_URL") or "").strip() else "USER/PASSWORD"
    return f"{host} ({src})"


def proxy_configured() -> bool:
    if (os.environ.get("UNION_WEBSHARE_PROXY_URL") or "").strip():
        return True
    return bool(_proxy_user() and _proxy_password())


def http_get(
    url: str,
    *,
    referer: str,
    accept: str = "*/*",
    timeout: int = 120,
) -> bytes:
    """
    GET only (unionmf.com returns 500 on HEAD). Uses Webshare when configured.
    """
    headers = {**HTTP_HEADERS_BASE, "Accept": accept, "Referer": referer}
    proxies_list = proxy_candidates()

    if proxies_list:
        failures: list[str] = []
        for proxy in proxies_list:
            try:
                return _get_via_curl_cffi(url, headers=headers, proxy=proxy, timeout=timeout)
            except Exception as e:
                msg = str(e)
                if " 407" in msg or "407 " in msg:
                    msg = "HTTP 407 proxy authentication rejected (check Webshare username/password/plan)"
                failures.append(f"{describe_proxy(proxy)}: {msg[:160]}")
                continue
        raise ConnectionError("all Webshare proxies failed — " + " | ".join(failures))

    return _get_via_curl_cffi(url, headers=headers, proxy=None, timeout=timeout)


def _get_via_curl_cffi(
    url: str,
    *,
    headers: dict[str, str],
    proxy: str | None,
    timeout: int,
) -> bytes:
    proxies: dict[str, str] | None = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}
    try:
        from curl_cffi import requests as creq  # type: ignore

        r = creq.get(
            url,
            headers=headers,
            impersonate="chrome131",
            timeout=timeout,
            proxies=proxies,
        )
        r.raise_for_status()
        return r.content
    except ImportError:
        pass
    except Exception:
        if proxy:
            raise
        # fall through to urllib without proxy

    if proxy:
        opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
        req = _urllib_request(url, headers)
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()

    from urllib.request import Request, urlopen

    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _urllib_request(url: str, headers: dict[str, str]) -> Any:
    from urllib.request import Request

    return Request(url, headers=headers)
