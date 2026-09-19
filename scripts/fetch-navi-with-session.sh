#!/usr/bin/env bash
# One-shot: use a browser-captured wp-nonce + cookie (from the XHR you pasted)
# to list + download Navi August portfolios and push them to the agent branch.
#
#   export NAVI_WP_NONCE='d9e48ee921'
#   export NAVI_COOKIE='_cfuvid=…; _ga=…'
#   bash scripts/fetch-navi-with-session.sh
set -euo pipefail

MONTH="${1:-2026-08}"
YEAR="${MONTH%-*}"
MON_NUM="${MONTH#*-}"
MON_NUM=$((10#$MON_NUM))
MONTH_NAME=$(python3 - <<PY
import calendar
print(calendar.month_name[$MON_NUM])
PY
)
if [[ "$MON_NUM" -ge 4 ]]; then
  FY="${YEAR}-$((YEAR + 1))"
else
  FY="$((YEAR - 1))-${YEAR}"
fi

: "${NAVI_WP_NONCE:?set NAVI_WP_NONCE from the wp-nonce request header}"
: "${NAVI_COOKIE:?set NAVI_COOKIE from the browser Cookie header}"

REPO_URL="${REPO_URL:-https://github.com/subscriptionmanager26-png/fund-disclosures.git}"
BRANCH="${BRANCH:-cursor/uti-navi-dsp-aug2026-195b}"
WORKDIR="${TMPDIR:-/tmp}/navi-session-fetch-$$"
STAGE_REL="data/staging/python/amcs/navi-mutual-fund/$MONTH"

echo "→ cloning $BRANCH …"
git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$WORKDIR"
cd "$WORKDIR"
mkdir -p "$STAGE_REL"

API='https://navi.com/wp-json/nv/v1/documents'
PAGE='https://navi.com/mutual-fund/downloads/portfolio'
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

echo "→ listing $MONTH_NAME $FY via documents API …"
curl -fsS --url "$API" \
  -H 'accept: application/json, text/javascript, */*; q=0.01' \
  -H 'content-type: application/x-www-form-urlencoded; charset=UTF-8' \
  -H "origin: https://navi.com" \
  -H "referer: $PAGE" \
  -H "user-agent: $UA" \
  -H "wp-nonce: $NAVI_WP_NONCE" \
  -H 'x-requested-with: XMLHttpRequest' \
  -H "cookie: $NAVI_COOKIE" \
  --data-raw "financial_year=${FY}&value=${MONTH_NAME}&category=884&type=Monthly&order=DESC" \
  -o "$STAGE_REL/documents.json"

python3 - <<'PY' "$STAGE_REL"
import json, sys, subprocess, os
from pathlib import Path
from urllib.parse import unquote, urlparse

stage = Path(sys.argv[1])
obj = json.loads((stage / "documents.json").read_text())
if not obj.get("success"):
    raise SystemExit(f"API unsuccessful: {obj!r[:500]}")
rows = obj.get("data") or []
if not isinstance(rows, list) or not rows:
    raise SystemExit("API returned no documents")

cookie = os.environ["NAVI_COOKIE"]
nonce = os.environ["NAVI_WP_NONCE"]
ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
seen = set()
manifest = []
for row in rows:
    title = row.get("title") or ""
    urls = row.get("url")
    if isinstance(urls, str):
        urls = [urls]
    if not isinstance(urls, list):
        continue
    for url in urls:
        url = str(url).replace("\\/", "/").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        name = unquote(urlparse(url).path.rsplit("/", 1)[-1]) or "navi.xlsx"
        dest = stage / name
        print(f"  GET {name}")
        subprocess.check_call([
            "curl", "-fsSL", url,
            "-H", f"user-agent: {ua}",
            "-H", "referer: https://navi.com/mutual-fund/downloads/portfolio",
            "-H", f"cookie: {cookie}",
            "-o", str(dest),
        ])
        manifest.append({"title": title, "download_url": url, "saved_as": name, "bytes": dest.stat().st_size})
(stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(f"→ saved {len(manifest)} file(s)")
PY

count=$(find "$STAGE_REL" -type f \( -name '*.xlsx' -o -name '*.xls' \) | wc -l | tr -d ' ')
if [[ "$count" -lt 1 ]]; then
  echo "ERROR: no spreadsheets downloaded" >&2
  exit 1
fi
ls -la "$STAGE_REL" | head -40

git add -f "$STAGE_REL"
git -c user.email="navi-local-fetch@users.noreply.github.com" -c user.name="navi-local-fetch" \
  commit -m "chore: stage Navi monthly $MONTH portfolios (browser session)"
git push origin "HEAD:$BRANCH"
echo "✓ pushed staged Navi files to $BRANCH — tell the cloud agent to continue"
