# Data layout (local parsed ↔ GitHub CDN)

One rule: **the calendar as-of date is the folder key everywhere.**

## Local pipeline (`fund-disclosures-gh`)

```text
data/disclosures/{cadence}/{YYYY-MM-DD}/{amc}/…     raw Excel/ZIP from AMC
data/parsed/{cadence}/{YYYY-MM-DD}/{amc}/{fund}/   parsed holdings
  portfolio.json                                   meta.as_of = YYYY-MM-DD
  portfolio.csv
```

| Cadence | Folder examples | `meta.as_of` |
|---------|-----------------|--------------|
| fortnightly | `2026-07-15`, `2026-07-31` | same date |
| monthly | `2026-06-30`, `2026-07-31` | month-end |

**Legacy:** older runs used `YYYY-MM` folders (e.g. `2026-07`). Parsers/sync still scan
those as fallbacks, but new fetches should use **`YYYY-MM-DD` only**.

**YYYY-MM shorthand:** `npm run fetch -- --type=fortnightly --period=2026-08` expands to
**both** `2026-08-15` and `2026-08-31` (daily cloud job does the same explicitly). Monthly
`YYYY-MM` still maps to month-end only.

Sync to GitHub filters on `meta.as_of`, not the folder name — so a Jul-15 portfolio can
temporarily sit under `fortnightly/2026-07/` until re-fetched into `fortnightly/2026-07-15/`.
**Do not trust that alone:** month-end or equity packs can be mis-stamped `as_of=YYYY-MM-15`.
After every fetch, an **LLM cadence gate** must confirm each Excel/ZIP belongs in its
`{cadence}/{YYYY-MM-DD}` folder before parse/publish (see [CURSOR_CLOUD_HOLDINGS.md](./CURSOR_CLOUD_HOLDINGS.md)
and [PIPELINE.md](./PIPELINE.md)).

## GitHub CDN (`fund-holdings-data`)

```text
portfolios/asof/{YYYY-MM-DD}/{portfolio_id}.json   ← sole portfolio store
catalog/amfi-lookup.json                           per-scheme latest_as_of + available_as_of
catalog/filings.json                               deduped counts per as-of date
meta.json
```

There is **no** separate canonical store under `portfolios/latest/`. “Latest holdings” for API consumers =

`catalog[amfi].latest_as_of` → `portfolios/asof/{latest_as_of}/{portfolio_id}.json`.

After each sync, `mirrorLatestPortfolios()` also copies the newest as-of file to
`portfolios/latest/{portfolio_id}.json` for legacy CDN clients. The sync fails if
catalog `latest_as_of` points at a missing as-of file.

## Counting schemes for an as-of date

| Question | Where to look |
|----------|----------------|
| How many Jul-15 fortnightly books? | `collectAsOfPortfolios(…, asOf='2026-07-15')` or count local `meta.as_of === '2026-07-15'` |
| What filings API shows | `catalog/filings.json` → deduped parent `portfolio_id` files under `portfolios/asof/2026-07-15/` |
| Wrong approach | Counting only `data/parsed/fortnightly/2026-07-15/` (may be a partial re-fetch slice) |

## Sync commands

```bash
# One calendar date (preferred)
node scripts/sync-asof-holdings-to-github.mjs --asof=2026-07-15 --cadence=fortnightly --push

# Full catalog refresh from local latest parsed rows (writes each book to its meta.as_of)
node scripts/sync-holdings-to-github.mjs --push
```

Each asof sync **prunes** stale files in that date folder (including legacy child-AMFI duplicate keys).

**Month-end order:** sync fortnightly first (merge — keeps existing books), then monthly last (replace — full universe). Use `npm run holdings:sync-window -- --push` or `scripts/sync-asof-window.mjs`.
