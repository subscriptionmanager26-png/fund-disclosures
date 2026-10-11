"""
Navi MF documents API via Cloudflare Browser Rendering (one session per run).

See docs/NAVI_PORTFOLIO_AUTOMATION.md for the operational playbook.
"""
from __future__ import annotations

import json
import os
import re
import time
from html import unescape
from typing import Any

import httpx

PAGE_URL = "https://navi.com/mutual-fund/downloads/portfolio"
API_PATH = "/wp-json/nv/v1/documents"
RESULT_ELEMENT_ID = "navi-pipeline-result"

MONTH_NUM_TO_NAME = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}

FORTNIGHT_MONTH_END = {
    1: 31,
    2: 28,
    3: 31,
    4: 30,
    5: 31,
    6: 30,
    7: 31,
    8: 31,
    9: 30,
    10: 31,
    11: 30,
    12: 31,
}

MONTHLY_CATEGORY = "884"
FORTNIGHTLY_CATEGORY = "885"


def fiscal_year_for_month(year: int, month: int) -> str:
    if month >= 4:
        return f"{year}-{year + 1}"
    return f"{year - 1}-{year}"


def fortnight_values_for_month(year: int, month: int) -> list[str]:
    name = MONTH_NUM_TO_NAME[month]
    end = FORTNIGHT_MONTH_END[month]
    if month == 2 and year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        end = 29
    return [f"{name} 1-15", f"{name} 16-{end}"]


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, (idx % 12) + 1


def build_attempt_queries(
    year: int,
    month: int,
    *,
    fortnightly: bool,
    walk_back_months: int = 6,
    max_attempts: int = 36,
) -> list[dict[str, str]]:
    """Ordered API form payloads; newest target month first, then walk back."""
    attempts: list[dict[str, str]] = []
    for back in range(walk_back_months + 1):
        y, m = _shift_month(year, month, -back)
        if fortnightly:
            category = FORTNIGHTLY_CATEGORY
            doc_type = "Fortnightly"
            values = fortnight_values_for_month(y, m)
        else:
            category = MONTHLY_CATEGORY
            doc_type = "Monthly"
            values = [MONTH_NUM_TO_NAME[m]]
        fy = fiscal_year_for_month(y, m)
        for value in values:
            attempts.append(
                {
                    "financial_year": fy,
                    "value": value,
                    "category": category,
                    "type": doc_type,
                    "order": "DESC",
                }
            )
            if len(attempts) >= max_attempts:
                return attempts
    return attempts


def build_injected_script(targets: list[dict[str, Any]]) -> str:
    """
    targets: [{ "month_key": "2026-09", "attempts": [ {financial_year, value, ...}, ...] }, ...]
    """
    payload = json.dumps(targets, separators=(",", ":"))
    return f"""
(() => {{
  const TARGETS = {payload};
  const RESULT_ID = "{RESULT_ELEMENT_ID}";
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function run() {{
    let el = document.getElementById(RESULT_ID);
    if (!el) {{
      el = document.createElement("pre");
      el.id = RESULT_ID;
      el.style.display = "none";
      document.body.appendChild(el);
    }}

    let nonce = null;
    for (let i = 0; i < 40; i++) {{
      nonce = window.navi_property?.nonce || window.wpApiSettings?.nonce || null;
      if (nonce) break;
      await sleep(500);
    }}
    if (!nonce) {{
      el.textContent = JSON.stringify({{ error: "no_nonce" }});
      return;
    }}

    const out = {{ targets: [], error: null }};
    for (const target of TARGETS) {{
      const entry = {{
        month_key: target.month_key,
        query: null,
        rows: [],
        attempts: 0,
      }};
      for (const q of target.attempts || []) {{
        entry.attempts += 1;
        const body = new URLSearchParams(q).toString();
        try {{
          const resp = await fetch("{API_PATH}", {{
            method: "POST",
            headers: {{
              "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
              "wp-nonce": nonce,
              "X-Requested-With": "XMLHttpRequest",
            }},
            body,
          }});
          const json = await resp.json();
          if (json && json.success && Array.isArray(json.data) && json.data.length > 0) {{
            entry.query = q;
            entry.rows = json.data;
            break;
          }}
        }} catch (e) {{
          entry.last_error = String(e);
        }}
      }}
      out.targets.push(entry);
    }}
    el.textContent = JSON.stringify(out);
  }}

  run().catch((e) => {{
    const el = document.getElementById(RESULT_ID) || document.body;
    el.textContent = JSON.stringify({{ error: String(e) }});
  }});
}})();
""".strip()


def parse_pipeline_result_html(html: str) -> dict[str, Any]:
    text = unescape(html or "")
    m = re.search(
        rf'id=["\']{RESULT_ELEMENT_ID}["\'][^>]*>(.*?)</',
        text,
        re.I | re.S,
    )
    if not m:
        raise RuntimeError(
            f"Browser session did not produce #{RESULT_ELEMENT_ID} (Cloudflare challenge or timeout)."
        )
    raw = m.group(1).strip()
    if raw.startswith("{"):
        return json.loads(raw)
    return json.loads(unescape(raw))


def cloudflare_account_id() -> str:
    return (os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()


def cloudflare_api_token() -> str:
    return (os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()


def browser_credentials_configured() -> bool:
    return bool(cloudflare_account_id() and cloudflare_api_token())


def fetch_documents_via_browser(
    month_targets: list[tuple[int, int]],
    *,
    fortnightly: bool,
    walk_back_months: int = 6,
    timeout_s: float = 120.0,
    max_retries: int = 3,
) -> dict[str, list[dict]]:
    """
    One Browser Rendering /content call: load portfolio page, poll nonce, POST documents API.
    Returns month_key -> list of API row dicts (title, url).
    """
    account_id = cloudflare_account_id()
    token = cloudflare_api_token()
    if not account_id or not token:
        raise RuntimeError(
            "Set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN for Navi fetch from cloud VMs."
        )

    targets = []
    for year, month in month_targets:
        mk = f"{year}-{month:02d}"
        targets.append(
            {
                "month_key": mk,
                "attempts": build_attempt_queries(
                    year,
                    month,
                    fortnightly=fortnightly,
                    walk_back_months=walk_back_months,
                ),
            }
        )

    script = build_injected_script(targets)
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/content"
    )
    body = {
        "url": PAGE_URL,
        "gotoOptions": {"waitUntil": "networkidle2", "timeout": int(timeout_s * 1000)},
        "addScriptTag": [{"content": script}],
        "waitForSelector": {
            "selector": f"#{RESULT_ELEMENT_ID}",
            "timeout": int(timeout_s * 1000),
            "visible": False,
        },
    }

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=timeout_s + 30) as client:
                resp = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
            if resp.status_code == 429:
                time.sleep(min(60, 2 ** attempt * 5))
                continue
            resp.raise_for_status()
            envelope = resp.json()
            if not envelope.get("success"):
                raise RuntimeError(
                    f"Browser Rendering error: {envelope.get('errors') or envelope}"
                )
            parsed = parse_pipeline_result_html(envelope.get("result") or "")
            if parsed.get("error"):
                raise RuntimeError(f"Navi browser job failed: {parsed['error']}")
            by_month: dict[str, list[dict]] = {}
            for block in parsed.get("targets") or []:
                mk = str(block.get("month_key") or "")
                rows = block.get("rows") or []
                if mk and isinstance(rows, list):
                    by_month[mk] = rows
            return by_month
        except Exception as e:
            last_err = e
            if attempt + 1 < max_retries:
                time.sleep(2 ** attempt * 3)
    raise RuntimeError(f"Cloudflare Browser Rendering failed after retries: {last_err}")
