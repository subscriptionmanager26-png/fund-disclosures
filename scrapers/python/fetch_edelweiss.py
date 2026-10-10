#!/usr/bin/env python3
"""
Edelweiss Mutual Fund — monthly portfolio via AMC API (no browser).

Page: https://www.edelweissmf.com/statutory/portfolio-of-schemes
API:  api.edelweissmf.com statutory-menus/single (Chrome TLS + AES body decrypt)

Akamai blocks plain curl; curl_cffi Chrome impersonation works.
Response body is OpenSSL-salted AES-CBC; passphrase =
  HmacSHA256(SECRET + x-ip-address + x-timestamp, HASH_KEY).hexdigest()

Keys are in the public SPA (`main.*.js` encryption block). Update DEFAULT_* when
the bundle rotates. Set EDELWEISS_API_SECRET to that `secreat` or leave unset.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import time
from hashlib import md5
from pathlib import Path
from urllib.parse import unquote, urljoin

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

try:
    from curl_cffi import requests as cf_requests
except ImportError as e:
    raise SystemExit(
        "curl_cffi required:  .venv/bin/pip install curl_cffi cryptography\n" + str(e)
    ) from e

PAGE = "https://www.edelweissmf.com/statutory/portfolio-of-schemes"
CDN = "https://www.edelweissmf.com"
API_SINGLE = (
    "https://api.edelweissmf.com/edelweissmf/api/v1/mf/statutory-menus/single"
    "?type=Statutory&fundType=MF&menuName=Portfolio%20of%20scheme(s)"
)

# Client-side constants from the Edelweiss SPA (see main.*.js encryption block).
DEFAULT_SECRET = (
    "97a9497605fa7f0ea64fbcbed6a9b7bea8f06650d771b84db0b5ffa2882d1000"
)
DEFAULT_HASH_KEY = (
    "45de59a4bd24ceabe59c52775532e2cf7904f2b7457d73579eafd06bc1e02c41"
)
_env = __import__("os").environ
# Override only when both env vars are set (paired rotation). A stale
# EDELWEISS_API_SECRET alone must not block the current SPA defaults.
if _env.get("EDELWEISS_HASH_KEY", "").strip():
    SECRET = _env.get("EDELWEISS_API_SECRET", "").strip() or DEFAULT_SECRET
    HASH_KEY = _env.get("EDELWEISS_HASH_KEY", "").strip()
else:
    SECRET = DEFAULT_SECRET
    HASH_KEY = DEFAULT_HASH_KEY
DEFAULT_IP = "103.0.123.175"

# Main consolidated monthly workbook (skip allocation annexures in the same submenu).
MONTHLY_PORTFOLIO_TITLE_RE = re.compile(r"^Monthly Portfolio\s*[-–]", re.I)

MONTH_ABBR = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Aug",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dec",
}


def parse_month(mk: str) -> tuple[int, int]:
    y, m = mk.split("-")
    return int(y), int(m)


def evp_bytes_to_key(password: str | bytes, salt: bytes, key_len: int = 32, iv_len: int = 16):
    password_bytes = password.encode("utf-8") if isinstance(password, str) else password
    derived = b""
    block = b""
    while len(derived) < (key_len + iv_len):
        block = md5(block + password_bytes + salt).digest()
        derived += block
    return derived[:key_len], derived[key_len : key_len + iv_len]


def unpad_pkcs7(data: bytes) -> bytes:
    n = data[-1]
    if 1 <= n <= 16 and data.endswith(bytes([n]) * n):
        return data[:-n]
    return data


def decrypt_body(encrypted_text: str, ip_address: str, timestamp: int | str):
    passphrase = hmac.new(
        HASH_KEY.encode("utf-8"),
        (SECRET + ip_address + str(timestamp)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    raw = base64.b64decode(encrypted_text)
    if raw[:8] != b"Salted__":
        raise ValueError("not OpenSSL salted ciphertext")
    key, iv = evp_bytes_to_key(passphrase, raw[8:16])
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    plain = unpad_pkcs7(decryptor.update(raw[16:]) + decryptor.finalize())
    return json.loads(plain.decode("utf-8"))


def safe_name(name: str) -> str:
    base = unquote(name).strip() or "edelweiss.xlsx"
    return re.sub(r"[^\w.\-() ]+", "_", base)[:220]


def abs_url(path: str | None) -> str | None:
    if not path or not isinstance(path, str):
        return None
    if path.startswith("http"):
        return path
    return urljoin(CDN + "/", path.lstrip("/"))


def fetch_menu_payload(session, ip: str) -> dict:
    ts = int(time.time() * 1000)
    headers = {
        "accept": "application/json, text/plain, */*",
        "origin": "https://www.edelweissmf.com",
        "referer": PAGE,
        "x-ip-address": ip,
        "x-timestamp": str(ts),
    }
    r = session.get(API_SINGLE, headers=headers, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "body" not in data:
        raise RuntimeError(f"unexpected response keys: {list(data)[:20]}")
    payload = decrypt_body(data["body"], ip, ts)
    if not isinstance(payload, dict):
        raise RuntimeError(f"decrypted payload is {type(payload).__name__}, expected dict")
    return payload


def monthly_files_for(payload: dict, year: int, month: int, *, fortnightly: bool = False) -> list[dict]:
    abbr = MONTH_ABBR[month]
    y = str(year)
    out = []
    for f in payload.get("files") or []:
        if not isinstance(f, dict):
            continue
        submenu = (f.get("subMenuName") or f.get("heading") or "")
        if fortnightly:
            if "Fortnightly" not in submenu:
                continue
        elif "Monthly Portfolio" not in submenu:
            continue
        else:
            title = (f.get("fileTitle") or "").strip()
            if title and not MONTHLY_PORTFOLIO_TITLE_RE.match(title):
                continue
        if str(f.get("year") or "") != y:
            continue
        if str(f.get("month") or "") != abbr:
            continue
        # Prefer filePath — downloadFile is often double-prefixed (/Files/MF//Files/MF/…).
        path = (
            f.get("filePath")
            or f.get("excelFilePath")
            or f.get("downloadFile")
            or f.get("pdfFilePath")
        )
        if isinstance(path, str):
            path = path.replace("/Files/MF//Files/MF/", "/Files/MF/")
        url = abs_url(path)
        if not url:
            continue
        # Prefer CDN basename (stable, no commas); fall back to systemFileName
        fname = path.rstrip("/").split("/")[-1] or f.get("systemFileName") or "edelweiss.xlsx"
        out.append(
            {
                "url": url,
                "title": f.get("fileTitle") or "",
                "saved_as": safe_name(fname),
                "file_id": f.get("fileId"),
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Fetch Edelweiss monthly portfolios (AMC API, curl_cffi + decrypt)"
    )
    ap.add_argument("--months", nargs="+", required=True, help="YYYY-MM")
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent.parent,
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ip", default=DEFAULT_IP, help="x-ip-address header value")
    ap.add_argument(
        "--fortnightly",
        action="store_true",
        help="Accept submenu containing Fortnightly instead of Monthly Portfolio",
    )
    args = ap.parse_args()

    session = cf_requests.Session(impersonate="chrome131")
    amc_dir = args.root / "amcs" / "edelweiss-mutual-fund"

    print(f"GET+decrypt statutory menu…", flush=True)
    payload = fetch_menu_payload(session, args.ip)
    n_files = len(payload.get("files") or [])
    print(f"  decrypted; {n_files} file record(s)", flush=True)

    for mk in args.months:
        year, month = parse_month(mk)
        selected = monthly_files_for(payload, year, month, fortnightly=args.fortnightly)
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest: list[dict] = []
        kind = "fortnightly" if args.fortnightly else "monthly"
        print(f"\n{mk}: {len(selected)} {kind} file(s)", flush=True)

        for d in selected:
            url = d["url"]
            fname = d["saved_as"]
            rec = {
                "month": mk,
                "download_url": url,
                "title": d["title"],
                "saved_as": fname,
                "file_id": d.get("file_id"),
            }
            if args.dry_run:
                print(f"  dry-run {fname}", flush=True)
                manifest.append({**rec, "dry_run": True})
                continue
            rr = session.get(
                url,
                headers={"referer": PAGE, "origin": "https://www.edelweissmf.com"},
                timeout=120,
            )
            if rr.status_code != 200 or b"Access Denied" in rr.content[:400]:
                print(f"  FAIL {fname} http_{rr.status_code}", flush=True)
                manifest.append({**rec, "error": f"http_{rr.status_code}"})
                continue
            (out_dir / fname).write_bytes(rr.content)
            print(f"  OK {fname} ({len(rr.content)} bytes)", flush=True)
            manifest.append({**rec, "bytes": len(rr.content)})

        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"  wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
