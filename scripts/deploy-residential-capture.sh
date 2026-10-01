#!/usr/bin/env bash
# One-time deploy of the residential capture Worker (Cloudflare KV).
# Needs: CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, CAPTURE_PIN
set -euo pipefail
cd "$(dirname "$0")/../workers/residential-capture"

if [[ -z "${CLOUDFLARE_API_TOKEN:-}" || -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
  echo "Set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID" >&2
  exit 1
fi
if [[ -z "${CAPTURE_PIN:-}" ]]; then
  echo "Set CAPTURE_PIN (4+ digits, used on phone)" >&2
  exit 1
fi

npm install -g wrangler 2>/dev/null || true

if ! grep -q 'REPLACE_KV_NAMESPACE_ID' wrangler.toml; then
  echo "KV namespace already configured in wrangler.toml"
else
  echo "→ Creating KV namespace…"
  id=$(npx wrangler kv namespace create MANIFESTS 2>&1 | sed -n 's/.*id = "\([^"]*\)".*/\1/p' | head -1)
  if [[ -z "$id" ]]; then
    echo "Could not parse KV id from wrangler output" >&2
    exit 1
  fi
  sed -i "s/REPLACE_KV_NAMESPACE_ID/$id/" wrangler.toml
fi

echo "→ Setting CAPTURE_PIN secret…"
printf '%s' "$CAPTURE_PIN" | npx wrangler secret put CAPTURE_PIN

echo "→ Deploying worker…"
npx wrangler deploy

echo ""
echo "✓ Done. Worker URL is shown above — paste it into the capture page on your phone."
echo "  Then set RESIDENTIAL_CAPTURE_URL in Cloud Agent secrets for the daily job."
