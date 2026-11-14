"""Edelweiss Nov-2026 session protocol: ECDH P-256 handshake, HKDF, AES-GCM responses."""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
pytest.importorskip("curl_cffi")

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402

import fetch_edelweiss as fe  # noqa: E402

MENU = {
    "menuName": "Portfolio of scheme(s)",
    "files": [
        {
            "subMenuName": "Monthly Portfolio",
            "fileTitle": "Monthly Portfolio - October 2026",
            "year": "2026",
            "month": "Oct",
            "filePath": "/Files/MF/Monthly_Portfolio_Oct2026.xlsx",
            "fileId": 1,
        },
        {
            "subMenuName": "Fortnightly Portfolio",
            "fileTitle": "Fortnightly Portfolio - 31 Oct 2026",
            "year": "2026",
            "month": "Oct",
            "filePath": "/Files/MF/Fortnightly_31Oct2026.xlsx",
            "fileId": 2,
        },
    ],
}


class Resp:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeEdelweiss:
    """Server side of the SPA protocol."""

    def __init__(self, wrap: bool = False):
        self.priv = ec.generate_private_key(ec.SECP256R1())
        self.salt = os.urandom(16)
        self.key = None
        self.wrap = wrap
        self.seen_headers = None

    def post(self, url, json=None, headers=None, timeout=None):
        assert url.endswith("/analytics/init")
        client_pub = serialization.load_der_public_key(base64.b64decode(json["clientPublickey"]))
        shared = self.priv.exchange(ec.ECDH(), client_pub)
        self.key = HKDF(algorithm=hashes.SHA256(), length=32, salt=self.salt, info=b"aes-session-key").derive(shared)
        spki = self.priv.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        body = {
            "serverPublicKey": base64.b64encode(spki).decode(),
            "salt": base64.b64encode(self.salt).decode(),
            "sessionId": "sess-123",
            "expiresIn": 900,
        }
        return Resp(200, {"data": body} if self.wrap else body)

    def get(self, url, headers=None, timeout=None):
        self.seen_headers = headers or {}
        if self.seen_headers.get("x-app-context") != "sess-123":
            return Resp(500, {"statusCode": 500, "message": "Internal server error"})
        iv = os.urandom(12)
        payload = {"statusCode": 200, "data": MENU} if self.wrap else MENU
        ct = AESGCM(self.key).encrypt(iv, json.dumps(payload).encode(), None)
        return Resp(
            200,
            {
                "cipherText": base64.b64encode(ct[:-16]).decode(),
                "iv": base64.b64encode(iv).decode(),
                "authTag": base64.b64encode(ct[-16:]).decode(),
            },
        )


@pytest.mark.parametrize("wrap", [False, True])
def test_session_protocol_round_trip(wrap):
    server = FakeEdelweiss(wrap=wrap)
    payload = fe.fetch_menu_payload_session(server)
    assert payload["files"][0]["fileId"] == 1
    assert server.seen_headers["x-app-context"] == "sess-123"
    assert len(server.seen_headers["x-request-id"]) == 36
    files = fe.monthly_files_for(payload, 2026, 10)
    assert [f["file_id"] for f in files] == [1]
    assert [f["file_id"] for f in fe.monthly_files_for(payload, 2026, 10, fortnightly=True)] == [2]


def test_without_session_headers_server_returns_500():
    """What the old fetcher saw: the API answers 500 when x-app-context is missing."""
    server = FakeEdelweiss()
    assert server.get(fe.API_SINGLE, headers={}).status_code == 500
