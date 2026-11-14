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
| `CLOUDFLARE_ACCOUNT_ID` | **Required for Navi MF** — Browser Rendering account id |
| `CLOUDFLARE_API_TOKEN` | **Required for Navi MF** — `cfat_…` with Browser Rendering / Browser Run **Edit** |
| `UNION_WEBSHARE_PROXY_USER` | **Required for Union MF** — Webshare proxy username |
| `UNION_WEBSHARE_PROXY_PASSWORD` | **Required for Union MF** — Webshare proxy password |
| `UNION_WEBSHARE_PROXY_HOSTS` | Optional — comma-separated `host:port` (defaults in `docs/UNION_MF_WEBSHARE_PROXY.md`) |
| `UNION_WEBSHARE_PROXY_URL` | Optional alternative to user/password — full `http://user:pass@host:port` |
| `EDELWEISS_API_SECRET` | Optional — only with `EDELWEISS_HASH_KEY` (paired SPA rotation). Fetcher uses current keys from the public `main.*.js` bundle when hash key is unset. |
| `EDELWEISS_HASH_KEY` | Optional paired override for statutory API AES (see `fetch_edelweiss.py` DEFAULT_*). |

**Do not rely on Cursor’s built-in `GH_TOKEN`.** That is `cursor[bot]` on the checkout repo (`subscriptionmanager26-png/fund-disclosures`). It **cannot** push to `kushagra-agarwal-a/fund-holdings-data` (403). The daily script requires `HOLDINGS_GH_TOKEN` and uses it for the data-repo push.

Also enable a **Slack** action on the automation (DM yourself) so the run report is posted. `open_git_pr` alone is not enough.

Optional env (defaults are fine):

| Var | Default | Purpose |
|-----|---------|---------|
| `FETCH_TIMEOUT_MS` | `180000` | Per-request HTTP timeout (`httpFetch`) |
| `FETCH_LIST_TIMEOUT_MS` | `900000` | Wall clock for whole `adapter.listFiles` (pagination / slow hubs) |
| `FETCH_CONCURRENCY` | `2` | Parallel AMC fetches for fortnightly jobs |
| `FETCH_MONTHLY_CONCURRENCY` | `1` | Monthly jobs run serially — avoids HDFC/ICICI/Axis timeouts under load |

**Sep 2026 half-coverage:** the daily job used `FETCH_TIMEOUT_MS` as the **listFiles** wall clock (3m) with **concurrency 4**. Large CMS AMCs timed out during the September monthly fetch; FN-31 debt still merged, so the month looked half-full. Monthly now defaults to concurrency **1** with a separate **15m** list timeout and a serial retry pass for any remaining errors.

## What `holdings:cloud` does

1. Fetch **fortnightly mid-month, fortnightly month-end, and monthly** for the **previous + current** calendar month (never skip FN-31 — new debt launches often appear only there)
2. **Cadence gate + quarantine resolution (automatic, before parse)** — `holdings:cloud` parses straight after each fetch, so these rules are enforced in code: filename cadence guards in `portfolioFilter.js`, and `resolve_quarantine.py` for undated files (promoted only when the in-workbook as-of matches the slice). The rules:
   - **Fortnightly mid-month (`…-15`)** — mid-month / fortnightly / debt-scheme packs dated ~15th. Reject monthly packs, `31-Jul` / month-end files, equity/ELSS/flexi workbooks, and stale archives misfiled into the FN tree.
   - **Fortnightly month-end (`…-31`)** — month-end debt/fortnightly packs only (same cadence tree as mid-month FN, different date folder).
   - **Monthly (`…-31` or month-end)** — full monthly portfolios; do not mix mid-month FN packs here.
   - Misfiled files must be **moved** to the correct cadence/date folder (or deleted), never synced “as-is” because `meta.as_of` happened to match.
   - Verdicts are recorded in `data/probes/quarantine-*.json`.
3. Parse all AMCs (`--all`) — after quarantine resolution
4. Enrich identifiers (`--allow-incomplete` — partial months are normal early in the month)
5. **Merge-sync** to GitHub via `sync-asof-window.mjs --merge --continue-on-error` (fortnightly slice failures do not block month-end monthly; fallback monthly sync if the window still aborts)
6. Refresh `catalog/filings.json` + pin `meta.json`
7. Verify `https://openfin.pocketedge.in/api/v1/filings`
8. **Completeness check** per AMC vs last month's same slice (after SEBI deadline) → `run_status`
9. Write JSON report under `data/probes/cloud-holdings-report-*.json`; copy log + probes to `/opt/cursor/artifacts`

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
| **empty** | Nothing new listed for this period (only older / other-slice files). Normal before the AMC publishes |
| **needs_review** | Files were listed but **none had a readable date in the name** — they were quarantined, not dropped. `resolve_quarantine.py` promotes the ones whose in-workbook as-of matches; whatever is left is in `data/probes/quarantine-*.json` |
| **error** | Listing or every download failed (timeout, WAF/HTML block page, network). Errored AMCs are retried serially once per job |

`empty` before the SEBI deadline is not a bug. **After the deadline it is** — the completeness check below names those AMCs.

### Quarantine (undated files)

Filename rules used to **drop** any file without a parseable date (`undated_no_month`), so every AMC rename (hash names, `October` with no year, `oct15`, `31 Oct, 2026`) silently emptied that AMC until someone patched a regex. Now:

1. `fetch-period.js` downloads undated candidates to `data/quarantine/{type}/{as_of}/{amc}/` (all candidates when the AMC kept nothing; otherwise only portfolio-looking names; max `QUARANTINE_MAX_PER_AMC`, default 40).
2. `scrapers/python/resolve_quarantine.py` opens each workbook (zip members too), requires a holdings table (ISIN + %NAV / market value) and an *as on* banner, and promotes the file into `data/disclosures/…` only when that date matches the slice. Month-end fortnightly needs an explicit *fortnight* marker so monthly equity packs never leak into the FN tree.
3. Verdicts: `data/probes/quarantine-{type}-{as_of}.json` (`content_as_of_matches`, `as_of_other_period`, `not_a_portfolio_workbook`, `no_as_of_banner`, `unreadable`, …).

Adapters that already filter by month (API / CMS / python fetchers) should still set `trust_adapter_period: true` — that skips quarantine entirely. Only plain HTML hubs (`static_html`) need it.

Downloads that are really HTML (WAF "Request Rejected", Cloudflare challenge) saved under a spreadsheet name now fail as `html_not_spreadsheet` instead of counting as `ok`.

### Completeness check and `run_status`

After sync, every fetched as-of date is compared per AMC with the same slice last month (`scripts/lib/completeness.mjs`), once the SEBI deadline has passed (monthly: month end + 10 days; fortnightly: + 5 days; +1 day grace):

- **MISSING** — AMC had portfolios last month, has none now.
- **DROPPED** — AMC lost ≥25% and ≥3 portfolios.

Each row carries the fetch diagnosis (status, error text, rejection reasons, quarantine verdicts) so the cause is in the report, not on a dead VM.

`report.run_status`: `ok` | `incomplete` (published what it had, but AMC gaps past deadline — **exit code 2**) | `push_failed` (exit 1).

### Run evidence

`holdings:cloud` tees its full output to `data/probes/holdings-cloud-run-*.log` and copies the log, `fetch-*.json`, `fetch-rejections-*`, `quarantine-*.json` and the report to `/opt/cursor/artifacts` (Cursor exposes these via `GET /v0/agents/{id}/artifacts`), or to `HOLDINGS_ARTIFACTS_DIR`. This happens even if the run crashes.

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

**Navi (`navi.com`):** Follow **`docs/NAVI_PORTFOLIO_AUTOMATION.md`** — Cloudflare Browser Rendering is the primary path (monthly + fortnightly). One cached browser job per hour warms both cadences; CDN downloads use curl_cffi. If Browser Rendering fails, use the desktop backup section in that doc (manual); do not bypass regression guards.

**Union (`unionmf.com`):** Follow **`docs/UNION_MF_WEBSHARE_PROXY.md`** — route all Union traffic through Webshare (`UNION_WEBSHARE_PROXY_USER` / `UNION_WEBSHARE_PROXY_PASSWORD`). Datacenter egress without proxy fails with SSL EOF / connection refused; Browser Rendering does not help.

**If curl_cffi still 403/SSL from a Cloud Agent VM:** Navi (`navi.com`) and Union (`www.unionmf.com`) may block datacenter IPs at the edge. Use **residential capture**: open the [mobile capture page](https://subscriptionmanager26-png.github.io/fund-disclosures/residential-capture/) on your phone, tap **Capture all AMCs** (APIs run in your mobile browser on each AMC site), and set `RESIDENTIAL_CAPTURE_URL` so the cloud agent reads manifests from the Worker. See `docs/RESIDENTIAL_CAPTURE.md`. Legacy: commit `data/residential-capture/manifests/…` or `scripts/fetch-navi-local.sh` / `fetch-union-local.sh`. Do not disable the as-of filter globally — only use `trust_adapter_period` for API-scoped adapters.

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

## Runtime / splitting the daily job

If the monolithic `holdings:cloud --push` run is too slow (~1h+ pipeline, more with cold setup), see **[`CLOUD_HOLDINGS_OPTIMIZATION.md`](./CLOUD_HOLDINGS_OPTIMIZATION.md)** for trace-based breakdown and parallel automation layouts.

## Slack / Cloud Agent prompt

**The automation checks out `subscriptionmanager26-png/fund-disclosures` (repo root, no `pipeline/`).** The prompt must say the same thing — an Oct–Nov 2026 prompt pointed at the monorepo while the agent actually ran the mirror, so fixes committed to one never reached the other.

**Fixes only count once merged to `main` of the repo the automation checks out.** In Nov 2026 five `cursor/*` branches held fixes (timeouts, Shriram cadence, fortnightly trust flags) for the exact failures that recurred, but were never merged. Fix agents must open a PR to `main`; review and merge it before the next run.

Suggested automation prompt:

```text
You maintain the OpenFin Indian mutual fund holdings pipeline.
Repo: subscriptionmanager26-png/fund-disclosures (branch main), run from the repo root.
Follow docs/CURSOR_CLOUD_HOLDINGS.md.

1. npm ci
2. python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
3. export GH_TOKEN="$HOLDINGS_GH_TOKEN"
4. npm run holdings:cloud -- --push   (exit 2 = published but incomplete; still continue)

Safety: never use --allow-regression or prune/retention scripts; never delete portfolio data;
if the regression guard blocks a push, stop and report.

Report (Slack DM): read the newest data/probes/cloud-holdings-report-*.json and send
run_status, fetch totals incl. needs_review and quarantine promoted/unresolved,
empty_amcs and amc_errors per job, and the completeness section verbatim
(every MISSING / DROPPED AMC with its diagnosis). Run evidence is already in /opt/cursor/artifacts.
```

The previous manual "LLM cadence gate" step is now enforced in code (filename cadence guards + content-date quarantine resolution); the agent no longer needs to move files by hand.

## Canonical repo

**Only** `kushagra-agarwal-a/fund-holdings-data` — data + parser. No holdings on other accounts.

`subscriptionmanager26-png/fund-disclosures` is an optional parser mirror (`npm run parser:mirror-subscriptionmanager`).
