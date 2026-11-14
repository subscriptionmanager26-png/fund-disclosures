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

Nov 2026: the SPA moved to a per-session ECDH handshake (the legacy call above
now returns HTTP 500). Protocol, from main.79fb35c62ee428a6.js (`L2` crypto
service + `F2` HTTP interceptor):
  1. POST {API}/analytics/init  {"clientPublickey": b64(SPKI of an ECDH P-256 key)}
     -> {serverPublicKey (b64 SPKI), salt (b64), sessionId, expiresIn (s)}
  2. key = HKDF-SHA256(ECDH(client_priv, server_pub), salt, info="aes-session-key", 32 bytes)
  3. GET requests carry  x-app-context: <sessionId>,  x-request-id: <uuid4>
  4. responses are {cipherText, iv, authTag} (b64); AES-GCM decrypt(cipherText||authTag)
fetch_menu_payload() tries this first and falls back to the legacy call.
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

import uuid

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

try:
    from curl_cffi import requests as cf_requests
except ImportError as e:
    raise SystemExit(
        "curl_cffi required:  .venv/bin/pip install curl_cffi cryptography\n" + str(e)
    ) from e

PAGE = "https://www.edelweissmf.com/statutory/portfolio-of-schemes"
CDN = "https://www.edelweissmf.com"
API_BASE = "https://api.edelweissmf.com/edelweissmf/api/v1/"
HANDSHAKE_URL = API_BASE + "analytics/init"
HKDF_INFO = b"aes-session-key"
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


def _b64d(s: str) -> bytes:
    return base64.b64decode(s)


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unwrap(obj, key: str):
    """Find a dict containing `key` (responses are sometimes wrapped in data/result)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj
        for v in obj.values():
            hit = _unwrap(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _unwrap(v, key)
            if hit is not None:
                return hit
    return None


class SessionCrypto:
    """ECDH P-256 + HKDF-SHA256 + AES-256-GCM session, as in the Edelweiss SPA (Nov 2026)."""

    def __init__(self, http, *, handshake_url: str = HANDSHAKE_URL):
        self.http = http
        self.handshake_url = handshake_url
        self.session_id: str | None = None
        self.key: bytes | None = None
        self.expires_at = 0.0

    def handshake(self) -> None:
        priv = ec.generate_private_key(ec.SECP256R1())
        spki = priv.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        r = self.http.post(
            self.handshake_url,
            json={"clientPublickey": _b64e(spki)},
            headers={
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "origin": "https://www.edelweissmf.com",
                "referer": PAGE,
            },
            timeout=60,
        )
        r.raise_for_status()
        d = _unwrap(r.json(), "serverPublicKey")
        if not d:
            raise RuntimeError(f"handshake: no serverPublicKey in {list(r.json())[:10]}")
        server_pub = serialization.load_der_public_key(_b64d(d["serverPublicKey"]))
        shared = priv.exchange(ec.ECDH(), server_pub)
        self.key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=_b64d(d["salt"]), info=HKDF_INFO
        ).derive(shared)
        self.session_id = str(d["sessionId"])
        self.expires_at = time.time() + float(d.get("expiresIn") or 300) - 5

    def ensure(self) -> None:
        if not self.session_id or time.time() > self.expires_at:
            self.handshake()

    def decrypt(self, body: dict):
        assert self.key is not None
        plain = AESGCM(self.key).decrypt(
            _b64d(body["iv"]), _b64d(body["cipherText"]) + _b64d(body["authTag"]), None
        )
        return json.loads(plain.decode("utf-8"))

    def get_json(self, url: str):
        self.ensure()
        r = self.http.get(
            url,
            headers={
                "accept": "application/json, text/plain, */*",
                "origin": "https://www.edelweissmf.com",
                "referer": PAGE,
                "x-app-context": self.session_id,
                "x-request-id": str(uuid.uuid4()),
            },
            timeout=60,
        )
        r.raise_for_status()
        body = r.json()
        if isinstance(body, dict) and {"cipherText", "iv", "authTag"} <= body.keys():
            return self.decrypt(body)
        return body


def fetch_menu_payload_session(session) -> dict:
    data = SessionCrypto(session).get_json(API_SINGLE)
    payload = _unwrap(data, "files")
    if not isinstance(payload, dict):
        raise RuntimeError(f"session API: no 'files' in response (keys: {list(data)[:15] if isinstance(data, dict) else type(data).__name__})")
    return payload


def fetch_menu_payload(session, ip: str) -> dict:
    """Current session protocol first; legacy salted-AES call as fallback."""
    try:
        return fetch_menu_payload_session(session)
    except Exception as e_new:  # noqa: BLE001
        print(f"  session protocol failed: {type(e_new).__name__}: {str(e_new)[:200]}", flush=True)
        try:
            return fetch_menu_payload_legacy(session, ip)
        except Exception as e_old:
            raise RuntimeError(
                f"Edelweiss menu: session protocol: {e_new}; legacy protocol: {e_old}"
            ) from e_old


def fetch_menu_payload_legacy(session, ip: str) -> dict:
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
