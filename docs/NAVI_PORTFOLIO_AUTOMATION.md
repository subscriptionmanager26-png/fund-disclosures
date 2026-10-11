# Navi Mutual Fund — portfolio fetch (Cloud Agent)

Daily `holdings:cloud` and manual Navi runs use **`scrapers/python/fetch_navi.py`** with the flow below. Datacenter `curl` to `navi.com` always fails (Cloudflare); do not use `NAVI_WP_NONCE` in production unless debugging.

---

## 1. Primary: Cloudflare Browser Rendering (automated)

### Secrets (Cursor Cloud Agent dashboard)

| Secret | Example shape | Purpose |
|--------|----------------|---------|
| `CLOUDFLARE_ACCOUNT_ID` | 32-char hex | Account that owns Browser Rendering |
| `CLOUDFLARE_API_TOKEN` | `cfat_…` | **Browser Rendering / Browser Run – Edit** |

Also required for CDN publish (unchanged): `HOLDINGS_GH_TOKEN`.

Verify token (account-owned tokens use the account path, not `/user/tokens/verify`):

```bash
curl -sS "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/tokens/verify" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN"
```

### What the fetcher does (one browser job per cache window)

1. **POST** `https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/browser-rendering/content`
   - `url`: `https://navi.com/mutual-fund/downloads/portfolio`
   - `gotoOptions.waitUntil`: `networkidle2` (timeout ≤ **60s**)
   - `addScriptTag`: poll `window.navi_property.nonce`, then `fetch('/wp-json/nv/v1/documents', …)` for each target period
   - `waitForTimeout`: **35s** default (`NAVI_CF_WAIT_MS`, max 60s) — do **not** use `waitForSelector` on a hidden result node (API rejects or times out)

2. **Cache** (`data/staging/python/navi_cf_cache.json`, TTL **1h**): the first Navi fetch in a run warms **both monthly and fortnightly** for the requested `YYYY-MM` months so later cadence fetches do not trigger extra browser calls (avoids **429** rate limits).

3. **Download** each `public-assets.prod.navi-tech.in/…xlsx` via **curl_cffi** (Chrome impersonation). Plain `urllib`/`curl` often returns **403** on the CDN.

### Documents API form fields

| Report | `category` | `type` |
|--------|------------|--------|
| Monthly | `884` | `Monthly` |
| Fortnightly | `885` | `Fortnightly` |

Example monthly body:

```text
financial_year=2026-2027&value=September&category=884&type=Monthly&order=DESC
```

Fortnightly `value` examples: `September 1-15`, `September 16-30`.

### Pipeline commands (same as other AMCs)

Fetched automatically inside `npm run holdings:cloud -- --push` via `fetch-period` → `fetch_navi.py` for **monthly** and **fortnightly** on the current + previous month window.

Manual smoke test:

```bash
cd pipeline   # or repo root on mirror checkout
export CLOUDFLARE_ACCOUNT_ID=…
export CLOUDFLARE_API_TOKEN=…
.venv/bin/python3 scrapers/python/fetch_navi.py --months 2026-09 --root data/staging/python/monthly
.venv/bin/python3 scrapers/python/fetch_navi.py --months 2026-09 --fortnightly --root data/staging/python/fortnightly
```

### Cloudflare errors

| HTTP / code | Action |
|-------------|--------|
| **400** | `gotoOptions.timeout` or `waitForTimeout` > 60s; fix env caps |
| **422** timeout | Increase `NAVI_CF_WAIT_MS` (≤60000); ensure `waitForTimeout` not `waitForSelector` on hidden nodes |
| **429** | Wait several minutes; only **one** browser call per hour per cache TTL; do not parallelize Navi fetches |
| Empty `data` | Period not published yet (normal early in month) |

Optional env:

| Var | Default | Purpose |
|-----|---------|---------|
| `NAVI_CF_WAIT_MS` | `35000` | In-page wait after script injection |
| `NAVI_CF_CACHE_TTL_SEC` | `3600` | Reuse monthly+fortnightly API rows across cadence fetches |
| `NAVI_WALK_BACK_MONTHS` | `0` | Debug only — try earlier months if target empty |

---

## 2. Backup: desktop browser (manual)

Use when Cloudflare credentials are missing, token is revoked, or Browser Rendering returns repeated 429/5xx.

**Do not** schedule this path on cron; it is for operators only.

1. Open `https://navi.com/mutual-fund/downloads/portfolio` in Chrome (pass “Just a moment…” if shown).
2. DevTools → Console:

```javascript
const nonce = window.navi_property?.nonce || window.wpApiSettings?.nonce;
const body = new URLSearchParams({
  financial_year: '2026-2027',
  value: 'September',
  category: '884',
  type: 'Monthly',
  order: 'DESC',
});
const r = await fetch('/wp-json/nv/v1/documents', {
  method: 'POST',
  headers: { 'wp-nonce': nonce, 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest' },
  body,
});
await r.json();
```

3. Download each `data[].url` with a browser-like client (or copy links into `data/staging/python/<cadence>/amcs/navi-mutual-fund/<YYYY-MM>/` and add `manifest.json` entries matching other AMC staging files).
4. Re-run parse + sync for that period:

```bash
npm run parse:amc -- --amc=navi-mutual-fund --type=monthly --period=2026-09-30
export GH_TOKEN="$HOLDINGS_GH_TOKEN"
node scripts/sync-asof-holdings-to-github.mjs --asof=2026-09-30 --cadence=monthly --merge --push
```

Alternative: export `NAVI_WP_NONCE` (+ `NAVI_COOKIE` if needed) from the same session and run `fetch_navi.py` once from a machine that can reach `navi.com` without Browser Rendering.

---

## 3. Automation checklist

- [ ] `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` on the Cloud Agent
- [ ] `HOLDINGS_GH_TOKEN` for CDN/data-repo push
- [ ] Daily job uses `holdings:cloud -- --push` (Navi included; no `--allow-regression`)
- [ ] On Navi `error` in fetch probe: check CF 429, then fall back to §2 and stage files manually
- [ ] After parser changes on mirror: merge to `main` so the scheduled agent picks up `fetch_navi.py`

Implementation: `scrapers/python/lib/navi_browser_api.py`.
