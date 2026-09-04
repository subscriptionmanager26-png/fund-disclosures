# GitHub holdings store (deduped portfolios, zero paid cloud)

Public AMFI holdings live on GitHub under a dedicated data account and are read
via **jsDelivr**. No card-on-file cloud.

## Accounts / repo

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full split.

| Role | Value |
|------|--------|
| **Data (OpenFin CDN)** | [`kushagra-agarwal-a/fund-holdings-data`](https://github.com/kushagra-agarwal-a/fund-holdings-data) — **only** copy |
| **Parser** | [`kushagra-agarwal-a/fund-disclosures`](https://github.com/kushagra-agarwal-a/fund-disclosures) + [`subscriptionmanager26-png/fund-disclosures`](https://github.com/subscriptionmanager26-png/fund-disclosures) (mirrored) |

Do **not** use `subscriptionmanager26-png/fund-holdings-data` — deprecated stale mirror.

## Dedup model (important)

AMFI lists ~8600 **schemes** (share classes). Most siblings share one portfolio book.

| Layer | Typical count | Stored as |
|------|---------------|-----------|
| Schemes | ~8607 | Catalog rows only |
| Unique portfolios | ~1942 | `portfolios/latest/{portfolio_id}.json` |
| Local seed available | ~local unique books | Uploaded when `local_path` exists |

**Never** upload one holdings file per AMFI code. Upload one file per `portfolio_id`,
then link every child scheme to it in the catalog.

`portfolio_id` is the id already used in B2 paths:

`fund-disclosures/holdings/latest/{amc}/{portfolio_id}/portfolio.json`

Sibling share-classes always share that id. A few distinct AMFI “parents” can also
collapse onto the same id (legacy plan codes).

## Object layout

```text
portfolios/asof/{yyyy-mm-dd}/{portfolio_id}.json
catalog/amfi-lookup.json
catalog/filings.json
meta.json
```

See also [DATA_LAYOUT.md](./DATA_LAYOUT.md) for local ↔ CDN folder conventions.
### Portfolio object

```json
{
  "portfolio_id": "152310",
  "member_amfi_codes": ["152307", "152308", "152309", "152310"],
  "scheme": { "...canonical parent / representative scheme card..." },
  "meta": { "as_of": "...", "holding_count": 61, "portfolio_id": "152310", "member_count": 4 },
  "holdings": [ /* shaped rows */ ]
}
```

### Catalog row (excerpt)

```json
{
  "amfi_code": "152309",
  "parent_amfi": "152310",
  "name": "…",
  "has_holdings": true,
  "portfolio_id": "152310",
  "latest_as_of": "2026-07-31",
  "available_as_of": ["2026-07-31", "2026-07-15"],
  "portfolio_key": "portfolios/asof/2026-07-31/152310.json",
  "portfolio_url": "https://cdn.jsdelivr.net/gh/kushagra-agarwal-a/fund-holdings-data@main/portfolios/asof/2026-07-31/152310.json"
}
```

## How to access (API / CDN)

### Two-hop CDN (no server required)

1. Fetch catalog (cache aggressively):

```text
https://cdn.jsdelivr.net/gh/kushagra-agarwal-a/fund-holdings-data@main/catalog/amfi-lookup.json
```

2. Look up AMFI code → `portfolio_id` / `portfolio_url`.

3. Fetch the shared portfolio:

```text
https://cdn.jsdelivr.net/gh/kushagra-agarwal-a/fund-holdings-data@main/portfolios/latest/{portfolio_id}.json
```

Historical (day-level as-of):

```text
https://cdn.jsdelivr.net/gh/kushagra-agarwal-a/fund-holdings-data@main/portfolios/asof/2026-07-15/{portfolio_id}.json
https://cdn.jsdelivr.net/gh/kushagra-agarwal-a/fund-holdings-data@main/catalog/filings.json
```
4. Overlay the requesting scheme’s name/NAV from the catalog row onto the payload
   if you need share-class-specific fields (the portfolio object carries the
   canonical scheme card only).

### Optional thin resolve API (later)

A free Vercel/holdings-browser route can wrap the two hops:

| Route | Behavior |
|--------|----------|
| `GET /v1/catalog` | catalog (or redirect to CDN) |
| `GET /v1/holdings/:amfi` | resolve catalog → portfolio → return shaped for that AMFI |
| `GET /v1/holdings/:amfi?as_of=` | historical book for that calendar date |
| `GET /v1/filings` | published as-of dates (monthly + fortnightly) |
| `GET /v1/portfolios/:id` | raw shared portfolio |

Not required for v1; CDN URLs are enough.

### Direct portfolio fetch

If you already know `portfolio_id` (parent book id), skip the catalog and hit
`portfolios/latest/{id}.json` directly.

## Sync

```bash
export GH_TOKEN='github_pat_…'   # contents:read/write on fund-holdings-data

node scripts/sync-holdings-to-github.mjs --limit=20 --dry-run
node scripts/sync-holdings-to-github.mjs --limit=50 --push
node scripts/sync-holdings-to-github.mjs --push          # all local unique portfolios
node scripts/sync-asof-holdings-to-github.mjs --asof=2026-08-15 --cadence=fortnightly --push
node scripts/sync-asof-holdings-to-github.mjs --asof=2026-06-30 --cadence=monthly --push
```

`--limit` caps **unique portfolios**, not schemes.

Env overrides: `HOLDINGS_DATA_OWNER`, `HOLDINGS_DATA_REPO`, `HOLDINGS_DATA_BRANCH`.

### AMFI NAV history columns

AMFI added **Plan** and **Option** columns to the NAV history download (~2024):

`Scheme Code;NAV Name;Plan;Option;ISIN…;ISIN…;Net Asset Value;Date`

`amfi/amfi_nav_history_asof.py` detects this layout from the header. If parsing drifts,
`npm run amfi:asof` and `npm run holdings:catalog` fail fast with a NAV/ISIN sanity check.
Regression: `.venv/bin/python3 amfi/test_nav_history_parse.py`.

## Smoke test

```bash
curl -sS 'https://cdn.jsdelivr.net/gh/kushagra-agarwal-a/fund-holdings-data@main/meta.json'
curl -sS 'https://cdn.jsdelivr.net/gh/kushagra-agarwal-a/fund-holdings-data@main/catalog/filings.json'
# pick a child AMFI from catalog, read portfolio_id, then:
curl -sS 'https://cdn.jsdelivr.net/gh/kushagra-agarwal-a/fund-holdings-data@main/portfolios/latest/152310.json' | head -c 400
curl -sS 'https://cdn.jsdelivr.net/gh/kushagra-agarwal-a/fund-holdings-data@main/portfolios/asof/2026-06-30/152310.json' | head -c 200
```

## Quotas

- GitHub soft ~1 GB / hard ~5 GB per repo — ~2k portfolios is fine.
- jsDelivr free CDN; pin `@<sha>` for immutable production reads.
- No R2 / Workers / paid object store.

## Security

Never commit PATs. If a token was pasted into chat, revoke it and mint a fresh
contents-only token for sync.
