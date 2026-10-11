# Union Mutual Fund — Webshare proxy fetch

`unionmf.com` blocks datacenter IPs before TLS completes. Cloud Agents **cannot** reach Union without a residential/datacenter **Webshare** HTTP proxy. Browsers and Cloudflare Browser Rendering do not help.

Pipeline entrypoint: **`scrapers/python/fetch_union.py`** (monthly + fortnightly via `fetch-period`).

---

## Required secrets (Cloud Agent dashboard)

Use **one** of these patterns (never commit values to git):

### Option A — single URL

| Secret | Example shape |
|--------|----------------|
| `UNION_WEBSHARE_PROXY_URL` | `http://<user>:<password>@31.59.20.176:6754` |

### Option B — user/password + host list (recommended)

| Secret | Purpose |
|--------|---------|
| `UNION_WEBSHARE_PROXY_USER` | Webshare proxy username |
| `UNION_WEBSHARE_PROXY_PASSWORD` | Webshare proxy password (dashboard → Proxy → Password) |
| `UNION_WEBSHARE_PROXY_HOSTS` | Optional comma-separated `host:port` list; defaults to known-good IPs below |

**Default hosts** (free-plan direct IPs — do **not** use gateway `p.webshare.io:80`, it returns **407**):

- `31.59.20.176:6754`
- `45.38.107.97:6014`
- `31.58.9.4:6077`

The fetcher rotates through hosts on connection errors.

---

## Quick verify (operator)

```bash
export UNION_WEBSHARE_PROXY_URL='http://USER:PASS@31.59.20.176:6754'
curl --proxy "$UNION_WEBSHARE_PROXY_URL" -A 'Mozilla/5.0' \
  -s -o /dev/null -w '%{http_code}\n' \
  'https://www.unionmf.com/about-us/downloads'
```

Expect **200**. Use **GET** only — **HEAD** returns **500** on this site.

---

## How the fetcher works

1. **Monthly:** scrape `https://www.unionmf.com/about-us/downloads/monthly-portfolio` for CDN links under  
   `/docs/default-source/funddetail-downloads/fund-portfolio/<month>-<year>/…xlsx`  
   Fallback: OData folder API (`FolderId` for monthly portfolios), then optional URL derivation from the next month.

2. **Fortnightly:** scrape `…/fortnightly-portfolio` and filter filenames with `--as-of YYYY-MM-DD`.

3. **OData index** (debug / discovery):

   ```text
   GET https://www.unionmf.com/api/downloads/documents?$skip=0
   ```

   Paginate with `$skip=1500` steps; filter with `$filter=FolderId eq …` for the monthly portfolio folder.

4. All HTTP goes through the Webshare proxy when secrets are set (`lib/union_webshare_proxy.py`).

---

## Failure modes

| Symptom | Action |
|---------|--------|
| SSL EOF / connection refused (no proxy) | Set `UNION_WEBSHARE_PROXY_*` secrets |
| **407** | Wrong password or using `p.webshare.io` gateway on free plan — use direct IP:port |
| **500** on health check | You used HEAD; use GET |
| Timeout on one IP | Automatic retry on next host in `UNION_WEBSHARE_PROXY_HOSTS` |
| All hosts fail | Check Webshare dashboard for remaining proxies (10 on free tier) |

**Bandwidth:** free plan ~1 GB/month; one monthly pull (~35 xlsx) is typically well under that. Do not re-download unchanged files in custom scripts.

**Upgrade:** if every datacenter proxy fails, residential proxies are paid — confirm with account owner before purchasing.

---

## Pipeline

Daily `npm run holdings:cloud -- --push` calls `fetch_union.py` for **monthly** and **fortnightly** on the current + previous month window, same as other AMCs.

Manual smoke test:

```bash
export UNION_WEBSHARE_PROXY_USER=…
export UNION_WEBSHARE_PROXY_PASSWORD=…
.venv/bin/python3 scrapers/python/fetch_union.py --months 2026-09 \
  --root data/staging/python/monthly
npm run fetch -- --amc=union-mutual-fund --type=monthly --period=2026-09-30
```

---

## Backup (manual)

If Webshare is down: download files from a non-cloud network (or Webshare dashboard test), place under  
`data/staging/python/<cadence>/amcs/union-mutual-fund/<YYYY-MM>/` with `manifest.json`, then run parse + sync for that as-of. Do not disable regression guards.
