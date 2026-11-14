#!/usr/bin/env python3
"""
Read-only diagnostics for AMCs that only answer from the Cloud Agent network
(Edelweiss: Akamai blocks most datacenter IPs; Union: needs the Webshare
residential proxy). Run inside the Cursor automation environment:

  .venv/bin/python3 scripts/diagnose_blocked_amcs.py --out /opt/cursor/artifacts/diag-amcs.txt

Writes HTTP statuses, link patterns, the Edelweiss SPA's current API calls /
key material (public, from its JS bundle) and the fetchers' own dry-run output.
Never prints proxy credentials. Changes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "scrapers" / "python"
sys.path.insert(0, str(PY))

OUT: list[str] = []
SAVE_DIR: Path | None = None


def log(*parts) -> None:
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    OUT.append(line)


def section(title: str) -> None:
    log("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def run_fetcher(script: str, args: list[str]) -> None:
    with tempfile.TemporaryDirectory() as td:
        cmd = [sys.executable, str(PY / script), *args, "--dry-run", "--root", td]
        log("$", " ".join(cmd[1:]))
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(ROOT))
            log(f"exit={res.returncode}")
            tail = (res.stdout + "\n" + res.stderr).strip().splitlines()[-40:]
            for line in tail:
                log("  |", line[:300])
        except Exception as e:  # noqa: BLE001
            log("fetcher failed to run:", e)


def snippets(text: str, pattern: str, width: int = 160, limit: int = 6) -> list[str]:
    out = []
    for m in re.finditer(pattern, text):
        s = max(0, m.start() - width)
        out.append(text[s : m.end() + width].replace("\n", " "))
        if len(out) >= limit:
            break
    return out


def diag_union() -> None:
    section("UNION (unionmf.com via Webshare proxy)")
    try:
        import fetch_union as fu
        from lib.union_webshare_proxy import proxy_configured

        log("proxy_configured:", proxy_configured())
        # Each credential source / host separately (no secrets printed).
        from lib.union_webshare_proxy import _get_via_curl_cffi, describe_proxy, proxy_candidates

        for proxy in proxy_candidates():
            t0 = time.time()
            try:
                body = _get_via_curl_cffi(
                    "https://www.unionmf.com/", headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
                    proxy=proxy, timeout=60,
                )
                log(f"  proxy {describe_proxy(proxy)}: OK {len(body)} bytes {time.time() - t0:.1f}s")
            except Exception as e:  # noqa: BLE001
                log(f"  proxy {describe_proxy(proxy)}: FAIL {str(e)[:160]}")
        for label, url in (("monthly page", fu.PAGE_URL), ("fortnightly page", fu.FORTNIGHTLY_PAGE_URL)):
            t0 = time.time()
            try:
                html = fu._http_get(url, referer=fu.BASE, accept="text/html,*/*").decode("utf-8", "ignore")
            except Exception as e:  # noqa: BLE001
                log(f"{label}: ERROR {type(e).__name__}: {str(e)[:300]}")
                continue
            log(f"{label}: {len(html)} bytes in {time.time() - t0:.1f}s")
            log("  URL_RE matches:", len(fu.URL_RE.findall(html)), "| FORTNIGHTLY_URL_RE:", len(fu.FORTNIGHTLY_URL_RE.findall(html)))
            docs = sorted(set(re.findall(r"""(?:https?://www\.unionmf\.com)?/docs/[^"'\s<>]+""", html)))
            log("  /docs/ links:", len(docs))
            for d in [d for d in docs if re.search(r"portfolio", d, re.I)][:25]:
                log("   ", d[:220])
            spread = sorted(set(re.findall(r"""[^"'\s<>]+\.(?:xlsx|xls|xlsb|zip)(?:\?[^"'\s<>]*)?""", html, re.I)))
            log("  any spreadsheet hrefs:", len(spread))
            for d in spread[:15]:
                log("   ", d[:220])
            log("  mentions 'october-2026':", "october-2026" in html.lower(), "| 'oct-2026':", "oct-2026" in html.lower())
            apis = sorted(set(re.findall(r"""/api/[A-Za-z0-9_/\-]+""", html)))
            log("  /api/ paths in page:", apis[:20])
        try:
            docs = fu.load_api_docs()
            log(f"OData API: {len(docs)} docs")
            for d in docs[:20]:
                log("   ", json.dumps({k: d.get(k) for k in ("Title", "Url", "PublicationDate", "Yearfilter")})[:260])
        except Exception as e:  # noqa: BLE001
            log("OData API: ERROR", type(e).__name__, str(e)[:300])
    except Exception:  # noqa: BLE001
        log(traceback.format_exc()[-1500:])
    run_fetcher("fetch_union.py", ["--months", "2026-10"])
    run_fetcher("fetch_union.py", ["--months", "2026-10", "--fortnightly", "--as-of", "2026-10-31"])


def diag_edelweiss() -> None:
    section("EDELWEISS (edelweissmf.com SPA + api.edelweissmf.com)")
    try:
        from curl_cffi import requests as cf

        import fetch_edelweiss as fe

        s = cf.Session(impersonate="chrome131")
        r = s.get(fe.PAGE, timeout=60)
        log("page:", r.status_code, len(r.content), "bytes")
        html = r.text
        srcs = re.findall(r"""<script[^>]+src=["']([^"']+\.js)["']""", html)
        log("scripts:", srcs[:15])
        bundle = ""
        for src in srcs:
            url = src if src.startswith("http") else fe.CDN.rstrip("/") + "/" + src.lstrip("/")
            if not re.search(r"main|app|chunk|runtime|polyfill", url):
                continue
            try:
                js = s.get(url, timeout=60)
                log(f"  {js.status_code} {len(js.content):>8} {url[:160]}")
                bundle += "\n" + js.text
            except Exception as e:  # noqa: BLE001
                log("  ERR", url[:160], e)
        log("bundle bytes:", len(bundle))
        if SAVE_DIR:
            SAVE_DIR.mkdir(parents=True, exist_ok=True)
            (SAVE_DIR / "edelweiss-main.js").write_text(bundle, encoding="utf-8")
            (SAVE_DIR / "edelweiss-page.html").write_text(html, encoding="utf-8")
            log("saved bundle + page to", SAVE_DIR)
        # Code around the statutory-menu call sites (constant name varies per build).
        for const in re.findall(r'([A-Za-z_$][\w$]*)="mf/statutory-menus/single"', bundle):
            for h in snippets(bundle, r"\b" + re.escape(const) + r"\b", width=700, limit=4):
                log("\n-- use of", const, "…", h[:1500])
        for pat in (
            r"statutory-menus[^\"'`]{0,120}",
            r"secreat|secret[A-Za-z]*\s*[:=]",
            r"hash[_-]?[Kk]ey\s*[:=]",
            r"x-ip-address|x-timestamp|x-[a-z-]+-(?:key|token|signature)",
            r"HmacSHA256|CryptoJS\.AES|enc\.Utf8",
            r"api\.edelweissmf\.com[^\"'`]{0,120}",
        ):
            hits = snippets(bundle, pat)
            log(f"\n-- /{pat}/ : {len(hits)} hit(s)")
            for h in hits:
                log("   …", h[:420])
        log("\nfetcher constants: SECRET[:8]=", fe.SECRET[:8], "HASH_KEY[:8]=", fe.HASH_KEY[:8])
        for label, headers in (
            ("as fetcher", None),
            ("no custom headers", {}),
        ):
            ts = int(time.time() * 1000)
            h = (
                {
                    "accept": "application/json, text/plain, */*",
                    "origin": "https://www.edelweissmf.com",
                    "referer": fe.PAGE,
                    "x-ip-address": fe.DEFAULT_IP,
                    "x-timestamp": str(ts),
                }
                if headers is None
                else headers
            )
            try:
                a = s.get(fe.API_SINGLE, headers=h, timeout=60)
                log(f"API ({label}): {a.status_code} {a.headers.get('content-type')} body[:400]= {a.text[:400]!r}")
            except Exception as e:  # noqa: BLE001
                log(f"API ({label}): ERROR {e}")
    except Exception:  # noqa: BLE001
        log(traceback.format_exc()[-1500:])
    run_fetcher("fetch_edelweiss.py", ["--months", "2026-10"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "probes" / "diag-amcs.txt"))
    ap.add_argument("--only", choices=["union", "edelweiss"], default=None)
    ap.add_argument("--save-dir", default="", help="save Edelweiss page + JS bundle here")
    args = ap.parse_args()
    global SAVE_DIR
    SAVE_DIR = Path(args.save_dir) if args.save_dir else None
    log("diagnose_blocked_amcs", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    log("env present:", {k: bool(os.environ.get(k)) for k in (
        "UNION_WEBSHARE_PROXY_USER", "UNION_WEBSHARE_PROXY_PASSWORD", "UNION_WEBSHARE_PROXY_URL",
        "EDELWEISS_API_SECRET", "EDELWEISS_HASH_KEY")})
    if args.only in (None, "union"):
        diag_union()
    if args.only in (None, "edelweiss"):
        diag_edelweiss()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
