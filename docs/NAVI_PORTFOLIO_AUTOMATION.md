# Navi Mutual Fund portfolio downloads — automation guide

Pipeline implementation: `scrapers/python/fetch_navi.py` + `scrapers/python/lib/navi_browser_api.py`.

## Summary

| Step | Tool | Why |
|------|------|-----|
| List files | **Cloudflare Browser Rendering** (`/browser-rendering/content`) | `navi.com` is behind Cloudflare; datacenter `curl` fails |
| Get file URLs | **Same browser session** → `POST /wp-json/nv/v1/documents` | Needs fresh `wp-nonce` from `window.navi_property.nonce` |
| Download `.xlsx` | **Normal HTTP** | Files on `public-assets.prod.navi-tech.in` (no Cloudflare) |

**Rule:** one browser job per fetch run; then HTTP downloads from the CDN.

## Required secrets (Cloud Agent)

| Secret | Purpose |
|--------|---------|
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account for Browser Rendering |
| `CLOUDFLARE_API_TOKEN` | Token with **Browser Rendering / Browser Run – Edit** |

Optional: `NAVI_WP_NONCE` + `NAVI_COOKIE` for manual bootstrap without Browser Rendering.

Optional: `NAVI_WALK_BACK_MONTHS` (default `0`) — when >0, try earlier months if the target period has no files yet (manual/debug only; daily job uses exact `--months`).

## API reference

| Item | Value |
|------|--------|
| Portfolio UI | `https://navi.com/mutual-fund/downloads/portfolio` |
| Documents API | `POST https://navi.com/wp-json/nv/v1/documents` |
| Monthly `category` / `type` | `884` / `Monthly` |
| Fortnightly | `885` / `Fortnightly` |

See the full playbook (period walk, error codes, scheduling) in the team doc *NAVI portfolio automation guide*.
