#!/usr/bin/env bash
# Run on a residential IP (your laptop). Fetches Union Aug 2026 monthly xlsx
# and commits them onto branch cursor/union-monthly-aug2026-195b for the cloud agent.
set -euo pipefail
MONTH="${1:-2026-08}"
REPO_URL="${REPO_URL:-https://github.com/subscriptionmanager26-png/fund-disclosures.git}"
BRANCH="${BRANCH:-cursor/union-monthly-aug2026-195b}"
WORKDIR="${TMPDIR:-/tmp}/union-mf-fetch-$$"

echo "→ cloning $BRANCH …"
git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$WORKDIR"
cd "$WORKDIR"

python3 -m venv .venv
.venv/bin/pip -q install curl_cffi

echo "→ fetching Union monthly $MONTH (residential egress) …"
.venv/bin/python scrapers/python/fetch_union.py --months "$MONTH" --root data/staging/python

STAGE="data/staging/python/amcs/union-mutual-fund/$MONTH"
count=$(find "$STAGE" -type f \( -name '*.xlsx' -o -name '*.xls' -o -name '*.xlsb' \) 2>/dev/null | wc -l | tr -d ' ')
echo "→ downloaded $count spreadsheet(s) under $STAGE"
ls -la "$STAGE" | head -40
if [[ "$count" -lt 1 ]]; then
  echo "ERROR: no files downloaded — check https://www.unionmf.com/about-us/downloads/monthly-portfolio" >&2
  exit 1
fi

# Staging is gitignored — force-add the month folder so the cloud agent can pull it.
git add -f "$STAGE"
git -c user.email="union-local-fetch@users.noreply.github.com" -c user.name="union-local-fetch" \
  commit -m "chore: stage Union monthly $MONTH portfolios (residential fetch)"
git push origin "HEAD:$BRANCH"
echo "✓ pushed staged Union files to $BRANCH — tell the cloud agent to continue"
