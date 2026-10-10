# Cursor Cloud — daily holdings update

## Cursor Automation (recommended setup)

**Use the parser mirror repo for checkout** — Cursor’s GitHub App can resolve branches there.

| Setting | Value |
|---------|--------|
| **Repo** | `subscriptionmanager26-png/fund-disclosures` |
| **Branch** | `main` |
| **Schedule** | Daily 6:30 AM IST → cron `0 1 * * *` (UTC) |
| **Runtime** | Cloud Agent |

**Data still publishes to** `kushagra-agarwal-a/fund-holdings-data` via the `HOLDINGS_GH_TOKEN` secret (not via automation repo checkout).

### Why not `kushagra-agarwal-a/fund-holdings-data`?

Cursor Automations resolve branches through the **Cursor GitHub App** on your linked account. If the app is installed on `subscriptionmanager26-png` but not `kushagra-agarwal-a`, you get **“Cannot resolve branch”** even though `main` exists. That’s an integration scope issue, not a missing branch.

**To use the monorepo directly:** install the [Cursor GitHub App](https://cursor.com/docs/integrations/github) on `kushagra-agarwal-a` and grant `fund-holdings-data` access, then set repo to `kushagra-agarwal-a/fund-holdings-data` and run from `pipeline/`.

### One-command update (standalone mirror)

```bash
npm ci
python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
export GH_TOKEN="$HOLDINGS_GH_TOKEN"
npm run holdings:cloud -- --push
```

### One-command update (monorepo, if GitHub App has access)

```bash
cd pipeline
npm ci
python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
export GH_TOKEN="$HOLDINGS_GH_TOKEN"
npm run holdings:cloud -- --push
```

## Required secrets (Cloud Agent dashboard)

Add these on [cursor.com/dashboard?tab=cloud-agents](https://cursor.com/dashboard?tab=cloud-agents) (Secrets), **not** in the repo `.env`. Cloud Agents do not load this repo’s `.env`.

| Secret | Purpose |
|--------|---------|
| `HOLDINGS_GH_TOKEN` | `kushagra-agarwal-a` PAT with `repo` write on `fund-holdings-data` |
| `EDELWEISS_API_SECRET` | Optional — only with `EDELWEISS_HASH_KEY` (paired SPA rotation). Fetcher uses current keys from the public `main.*.js` bundle when hash key is unset. |
| `EDELWEISS_HASH_KEY` | Optional paired override for statutory API AES (see `fetch_edelweiss.py` DEFAULT_*). |

**Do not rely on Cursor’s built-in `GH_TOKEN`.** That is `cursor[bot]` on the checkout repo (`subscriptionmanager26-png/fund-disclosures`). It **cannot** push to `kushagra-agarwal-a/fund-holdings-data` (403). The daily script requires `HOLDINGS_GH_TOKEN` and uses it for the data-repo push.

Also enable a **Slack** action on the automation (DM yourself) so the run report is posted. `open_git_pr` alone is not enough.

Optional env (defaults are fine):

| Var | Default | Purpose |
|-----|---------|---------|
| `FETCH_TIMEOUT_MS` | `180000` | Per-request timeout (avoids false errors on slow AMC sites) |
| `FETCH_CONCURRENCY` | `4` | Parallel AMC fetches (8 can hang Node with exit 13) |

## What `holdings:cloud` does

1. Fetch **fortnightly mid-month, fortnightly month-end, and monthly** for the **previous + current** calendar month (never skip FN-31 — new debt launches often appear only there)
2. **LLM cadence gate (required before parse/publish)** — after Excel/ZIP files land under `data/disclosures/{cadence}/{YYYY-MM-DD}/{amc}/`, the agent must inspect filenames (and paths) and confirm each file belongs in that cadence folder:
   - **Fortnightly mid-month (`…-15`)** — mid-month / fortnightly / debt-scheme packs dated ~15th. Reject monthly packs, `31-Jul` / month-end files, equity/ELSS/flexi workbooks, and stale archives misfiled into the FN tree.
   - **Fortnightly month-end (`…-31`)** — month-end debt/fortnightly packs only (same cadence tree as mid-month FN, different date folder).
   - **Monthly (`…-31` or month-end)** — full monthly portfolios; do not mix mid-month FN packs here.
   - Misfiled files must be **moved** to the correct cadence/date folder (or deleted), never synced “as-is” because `meta.as_of` happened to match.
   - Record the verdict (keep / move / drop) in `data/probes/` before continuing.
3. Parse all AMCs (`--all`) — only after the cadence gate
4. Enrich identifiers (`--allow-incomplete` — partial months are normal early in the month)
5. **Merge-sync** to GitHub via `sync-asof-window.mjs --merge --continue-on-error` (fortnightly slice failures do not block month-end monthly; fallback monthly sync if the window still aborts)
6. Refresh `catalog/filings.json` + pin `meta.json`
7. Verify `https://openfin.pocketedge.in/api/v1/filings`
8. Write JSON report under `data/probes/cloud-holdings-report-*.json`

## Filings API vs portfolio files

OpenFin (`/api/v1/filings`) and the holdings browser **do not scan** `portfolios/asof/*`. They read **`catalog/filings.json`** only — one row per disclosure date (as-of, cadence, portfolio count).

If a sync rebuilds that file from a **partial local** `portfolios/asof` tree (sparse checkout, or only the date being synced materialized on disk), the index can list **only the latest month-ends** while **fortnightly and older slices still exist on GitHub**. The API then looks “empty” for those dates even though CDN JSON is intact.

**Prevention (in repo):**

1. Before sync/refresh: `ensureFullPortfoliosAsOfCheckout` materializes all `portfolios/asof/*` from git HEAD.
2. When rebuilding filings: merge prior `filings.json` rows + block row drops (`assertFilingsIndexNotShrunk`).
3. After rebuild: `assertFilingsCoverOnDisk` — every on-disk as-of folder must have an index row.
4. Daily `holdings:cloud --push` runs `holdings:verify-filings` after refresh; the job **fails** if the index drifts again.

Manual check: `npm run holdings:verify-filings` (uses `.tmp/fund-holdings-data` or monorepo root).

## Safety rules

- **Never** use `--allow-regression` or prune scripts in the daily job
- Sync always uses `--merge` (additive fortnightly; monthly only replaces schemes present in the new parse)
- If regression guard blocks a **monthly** push, investigate — do not bypass. Fortnightly sync only guards **on-disk portfolio file counts** (catalog `available_as_of` link counts can drop when phantom FN stamps are pruned).
- **Ship fetch/parser fixes on `main` before expecting CDN gains** — feature branches do not affect `holdings:cloud` until merged.
- **Groww monthly** uses one consolidated workbook (`fetch_groww.py`); expect ~60 parent schemes on CDN, not hub HTML link counts (~150+).
- **Never publish** portfolios whose source workbook is not a real disclosure for that as-of/cadence (filename/date/type must agree). Heuristic filters help; they do not replace the LLM cadence gate above.

## Interpreting fetch results

| Status | Meaning |
|--------|---------|
| **ok** | Files downloaded for this period |
| **empty** | Scraper ran; AMC has not published this period yet (common early in the month) |
| **error** | Request failed (timeout, network) — retry next day or lower concurrency |

`empty` is not a bug. Most AMCs publish month-end monthly 3–7 days after month close.

## Troubleshooting fetch rejections (agent playbook)

When `fetch-period` reports `rejectedCount > 0` or monthly files land but never sync, check the rejection `reason` in the fetch JSON / stderr.

### `undated_no_month` on API-filtered monthly hubs

**Symptom:** Python adapter returns dozens of files, but Node rejects them all as `undated_no_month` (Invesco per-scheme slugs, Jio CMS hashes, Mahindra UUIDs, Sundaram `monthlyportfolio_*` hashes).

**Cause:** `portfolioFilter.js` rejects spreadsheets with no parseable month when a concrete `storageKey` (e.g. `2026-08-31`) is set. API adapters already filter rows to the target `YYYY-MM`.

**Fix:** Set `"trust_adapter_period": true` on that AMC's `fetch.monthly` entry in `registry/amcs.json`. The filter then keeps adapter-scoped files (still drops PRC / half-yearly via `EXCLUDE`). **Abakkus** also needs `Sep30_2026`-style tokens in `disclosureDates.js` / `disclosure_date.py` (see `test_abakkus_sep30_underscore_year`).

### `wrong_month` — LIC upload timestamp vs folder month

**Symptom:** LIC files under `/portfolio/monthly/2026/8/` rejected as `wrong_month` because filenames embed upload time (`09-09-2026`).

**Cause:** Filename parser reads the upload stamp as September; the real disclosure month is in the URL path.

**Fix:** `disclosureDates.js` / `disclosure_date.py` now parse `/monthly/YYYY/M/` path segments. Also set `trust_adapter_period: true` for LIC as a belt-and-suspenders guard.

### Akamai / Cloudflare / TLS blocks (`HTTP 403`, `SSL: UNEXPECTED_EOF`)

**Symptom:** `fetch_navi.py` 403 on nonce bootstrap (Cloudflare *Just a moment…* page); `fetch_union.py` SSL EOF on HTML/API; Edelweiss CDN 403 from Node download.

**Fix pattern:**

1. Use `curl_cffi` with Chrome impersonation in the Python fetcher (`impersonate="chrome131"`).
2. Add the script to `forceRealFetch` in `scrapers/node/adapters/pythonRef.js` so Node stages files via Python instead of re-fetching URLs.

**If curl_cffi still 403/SSL from a Cloud Agent VM:** Navi (`navi.com`) and Union (`www.unionmf.com`) may block datacenter IPs at the edge. The fetcher code is correct; run a one-off fetch from a residential/non-cloud IP (or wait for the block to lift) and stage files under `data/staging/python/amcs/<slug>/<YYYY-MM>/`. Do not disable the as-of filter globally — only use `trust_adapter_period` for API-scoped adapters.

### `excluded_non_portfolio` — Kotak Consolidated SEBI monthly

**Symptom:** Kotak monthly fetch keeps only `FortnightlyPortfolio*312026.xlsx` (~24 debt schemes); `ConsolidatedSEBIPortfolio*.xlsx` is rejected.

**Cause:** `portfolioFilter.js` treated `\bsebi\b` in the S3 path (`Consolidated-SEBI-Portfolio`) as a regulatory non-portfolio pack. Monthly jobs also kept fortnightly debt packs when the full consolidated SEBI workbook exists.

**Fix:** Allow `consolidated … sebi … portfolio` filenames/paths in the filter. `fetch_kotak.py` prefers consolidated SEBI rows for monthly (`--fortnightly` unchanged for FN cadence).

**Follow-up (Aug 2026):** Forms API listed only `FortnightlyPortfolioAugust312026.xlsx` while `ConsolidatedSEBIPortfolioAugust2026.xlsx` was already on S3 under `FormsDownloads/Portfolios/…` (not `FAD/…`). Monthly jobs then published ~105 debt schemes. `fetch_kotak.py` now probes known FAD + FormsDownloads Consolidated SEBI URL patterns when a requested month lacks a SEBI row.

### Stale title regex / date formats (Axis, Mirae)

**Symptom:** Adapter returns `empty` despite files on the disclosure page.

**Checks:**

- Axis CMS titles changed format (`Monthly Portfolio 31-08-2026` vs older patterns) — update adapter regex.
- Underscore dates (`15_08_2026`) or glued month-year (`aug2026`) — extend `disclosureDates.js` + tests in `scrapers/python/lib/test_disclosure_date.py`.

### Transient `error` vs structural `empty`

| Pattern | Action |
|---------|--------|
| Single AMC `error` with timeout / 5xx | Retry next run; optionally lower `FETCH_CONCURRENCY` |
| AMC `empty` early in month | Normal — wait for publication |
| AMC `empty` after month-end + peers published | Investigate adapter (API change, TLS, regex) |
| Files fetched but `rejectedCount` high | See rejection reasons above; do **not** bypass with `--allow-regression` |

## Slack / Cloud Agent prompt

Keep the prompt short. After setup:

1. Run fetch for the target periods.
2. **LLM cadence gate** — verify every new disclosure file sits in the correct `fortnightly` vs `monthly` date folder; move/drop misfiles and note verdicts under `data/probes/`.
3. Only then run `npm run holdings:cloud -- --push` (or parse → enrich → sync).
4. Print the latest `data/probes/cloud-holdings-report-*.json`.

Do not search the repo, Slack tools, or automations APIs afterwards. If Slack is configured, DM that summary.

## Canonical repo

**Only** `kushagra-agarwal-a/fund-holdings-data` — data + parser. No holdings on other accounts.

`subscriptionmanager26-png/fund-disclosures` is an optional parser mirror (`npm run parser:mirror-subscriptionmanager`).
