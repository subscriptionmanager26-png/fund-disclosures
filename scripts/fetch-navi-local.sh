#!/usr/bin/env bash
# Run on a residential IP (your laptop). Fetches Navi Aug 2026 monthly xlsx
# via the wp-json/nv/v1/documents API and pushes them onto
# cursor/uti-navi-dsp-aug2026-195b for the cloud agent.
#
# Usage:
#   bash scripts/fetch-navi-local.sh              # default 2026-08
#   bash scripts/fetch-navi-local.sh 2026-08
#
# Optional (if CF still challenges the Python bootstrap):
#   export NAVI_WP_NONCE='…'   # from request header wp-nonce
#   export NAVI_COOKIE='…'     # from browser Cookie header
set -euo pipefail
MONTH="${1:-2026-08}"
REPO_URL="${REPO_URL:-https://github.com/subscriptionmanager26-png/fund-disclosures.git}"
BRANCH="${BRANCH:-cursor/uti-navi-dsp-aug2026-195b}"
WORKDIR="${TMPDIR:-/tmp}/navi-mf-fetch-$$"

echo "→ cloning $BRANCH …"
git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$WORKDIR"
cd "$WORKDIR"

python3 -m venv .venv
.venv/bin/pip -q install curl_cffi

echo "→ fetching Navi monthly $MONTH (residential egress) …"
.venv/bin/python scrapers/python/fetch_navi.py --months "$MONTH" --root data/staging/python

STAGE="data/staging/python/amcs/navi-mutual-fund/$MONTH"
count=$(find "$STAGE" -type f \( -name '*.xlsx' -o -name '*.xls' -o -name '*.xlsb' \) 2>/dev/null | wc -l | tr -d ' ')
echo "→ downloaded $count spreadsheet(s) under $STAGE"
ls -la "$STAGE" | head -40
if [[ "$count" -lt 1 ]]; then
  echo "ERROR: no files downloaded — check https://navi.com/mutual-fund/downloads/portfolio" >&2
  echo "Tip: export NAVI_WP_NONCE and NAVI_COOKIE from your browser XHR, then re-run." >&2
  exit 1
fi

git add -f "$STAGE"
git -c user.email="navi-local-fetch@users.noreply.github.com" -c user.name="navi-local-fetch" \
  commit -m "chore: stage Navi monthly $MONTH portfolios (residential fetch)"
git push origin "HEAD:$BRANCH"
echo "✓ pushed staged Navi files to $BRANCH — tell the cloud agent to continue"
