# Holdings Cloud Pipeline — Session Report

**Date:** 2026-09-05  
**Automation ID:** `97f6c97a-a852-11f1-b532-320a589b8025`  
**Trigger:** Cron (`30 14 * * *` UTC)  
**Agent run started:** ~11:49 UTC  
**Report generated:** 2026-09-05 (follow-up session)

---

## 1. Objective

Execute the daily OpenFin Indian mutual fund holdings pipeline per `docs/CURSOR_CLOUD_HOLDINGS.md`:

1. Install dependencies (`npm ci`, Python venv + `requirements.txt`)
2. Export `GH_TOKEN` from `HOLDINGS_GH_TOKEN`
3. Run `npm run holdings:cloud -- --push` to fetch, parse, enrich, merge-sync, and publish to `kushagra-agarwal-a/fund-holdings-data`
4. Read the latest `data/probes/cloud-holdings-report-*.json`
5. DM a run summary via Slack (if configured)

**Safety constraints observed:**

- Did **not** use `--allow-regression` or prune/retention scripts
- Did **not** delete existing portfolio data
- Did **not** bypass regression guard (not triggered)

---

## 2. Environment

| Item | Value |
|------|-------|
| Checkout repo | `subscriptionmanager26-png/fund-disclosures` (parser mirror) |
| Branch | `main` |
| Layout | Standalone mirror — **no** `pipeline/` subdirectory; commands run from repo root |
| Data target | `kushagra-agarwal-a/fund-holdings-data` |
| Node.js | v22.14.0 |
| Holdings output (local) | `/workspace/.tmp/fund-holdings-data` |
| `HOLDINGS_GH_TOKEN` | Present |
| `EDELWEISS_API_SECRET` | Present |
| Slack DM action | **Not configured** (only `open_git_pr` available on automation) |

---

## 3. Setup Steps Performed

### 3.1 `npm ci`

```bash
cd /workspace && npm ci
```

**Result:** Success (1 package audited, 0 vulnerabilities).

### 3.2 Python virtual environment

```bash
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/pip install -q curl_cffi cryptography
```

**Result:** Success. Extra packages installed for Edelweiss AMC fetch (per prior automation notes).

### 3.3 Token export

```bash
export GH_TOKEN="$HOLDINGS_GH_TOKEN"
```

**Result:** Token available; `holdings_token_present: true` in final report.

---

## 4. Pipeline Execution

### 4.1 Attempt 1 — Failed

**Command:**

```bash
export GH_TOKEN="$HOLDINGS_GH_TOKEN"
npm run holdings:cloud -- --push
```

**Config (defaults):**

- `FETCH_CONCURRENCY=8`
- `FETCH_TIMEOUT_MS=180000`
- Periods: `2026-08`, `2026-09`

**What happened:**

- Pipeline started fetch for **monthly 2026-08** (51 AMCs, concurrency=8).
- Most AMCs completed; several returned **empty** (not yet published) or **error**:
  - **Axis Mutual Fund** — timeout
  - **DSP Mutual Fund** — timeout
  - **Jio BlackRock Mutual Fund** — fetch failed
- **Groww** and **Helios** (`static_html` adapters) were started but never logged completion before the process exited.
- Node.js v22 emitted:

  ```
  Warning: Detected unsettled top-level await at file:///workspace/scrapers/node/fetch-period.js:214
  ```

- `fetch-period.js` exited with **status 13** before writing its probe manifest or printing `Done. ok=...`.
- `cloud-holdings-update.mjs` threw:

  ```
  Error: fetch monthly 2026-08 failed (13)
  ```

- No `data/probes/cloud-holdings-report-*.json` was produced (pipeline aborted early).

**Root cause (inferred):** At concurrency=8, slow/hung `static_html` fetches (Groww, Helios) left the top-level `await mapPool(...)` unsettled; Node 22 exits with code 13 when the event loop drains while top-level await is still pending.

### 4.2 Attempt 2 — Succeeded

**Command (retry with tuned env):**

```bash
export GH_TOKEN="$HOLDINGS_GH_TOKEN"
export FETCH_CONCURRENCY=4
export FETCH_TIMEOUT_MS=300000
npm run holdings:cloud -- --push
```

**Timeline:**

| Phase | Time (UTC) |
|-------|------------|
| Started | 12:05:48 |
| Finished | 12:29:58 |
| Duration | ~24 minutes |

**Stages completed:**

1. **Fetch** — monthly + fortnightly for 2026-08 and 2026-09
2. **Parse** — all AMCs (`parse:amc --all`) per period
3. **Enrich** — `holdings:enrich --allow-incomplete`
4. **Assert locks** — `holdings:assert-locks`
5. **Sync** — `sync-asof-window.mjs` with `--merge --push`
6. **Filings** — `refresh-filings-catalog.mjs --push`
7. **Verify** — OpenFin filings API check
8. **Report** — wrote `data/probes/cloud-holdings-report-1788611398516.json`

**Final status:** `cloud-holdings-update: done`, `push_error: null`

---

## 5. Fetch Results (by period)

| Type | Period | Storage key | AMCs | ok | empty | error | Files |
|------|--------|---------------|------|-----|-------|-------|-------|
| monthly | 2026-08 | 2026-08-31 | 51 | 7 | 44 | 0 | 86 |
| fortnightly | 2026-08 | 2026-08-15 | 48 | 11 | 37 | 0 | 32 |
| monthly | 2026-09 | 2026-09-30 | 51 | 0 | 48 | 3 | 0 |
| fortnightly | 2026-09 | 2026-09-15 | 48 | 0 | 48 | 0 | 0 |

### Aggregate fetch totals

| Metric | Count |
|--------|-------|
| AMCs checked | 198 |
| ok | 18 |
| empty | 177 |
| error | 3 |
| Files downloaded | 118 |

### AMC errors (2026-09 monthly only)

- `icici-prudential-mutual-fund` — fetch failed
- `mirae-asset-mutual-fund` — fetch failed
- `nj-mutual-fund` — fetch failed

**Note:** `empty` is expected early in the month when AMCs have not yet published disclosures. These are not treated as bugs.

---

## 6. Push / Sync Results

### Before / after file counts

Baseline on fresh clone was empty (`baseline_files: {}`). After merge-sync push:

| As-of date | Portfolio files |
|------------|-----------------|
| 2026-06-15 | 561 |
| 2026-06-30 | 2,078 |
| 2026-07-15 | 557 |
| 2026-07-31 | 2,099 |
| 2026-08-15 | 500 |
| 2026-08-31 | 50 |

### New as-of dates added this run

| Date | Before | After | Added |
|------|--------|-------|-------|
| 2026-06-15 | 0 | 561 | 561 |
| 2026-06-30 | 0 | 2,078 | 2,078 |
| 2026-07-15 | 0 | 557 | 557 |
| 2026-07-31 | 0 | 2,099 | 2,099 |
| 2026-08-15 | 0 | 500 | 500 |
| 2026-08-31 | 0 | 50 | 50 |

**Total new portfolio files:** 5,845

**Regression guard:** Not triggered. Push completed without `--allow-regression`.

---

## 7. OpenFin Filings Snapshot

Verified at `https://openfin.pocketedge.in/api/v1/filings` after push:

| As-of | Cadence | Portfolio count |
|-------|---------|-----------------|
| 2026-08-31 | monthly | 50 |
| 2026-08-15 | fortnightly | 500 |
| 2026-07-31 | monthly | 2,099 |
| 2026-07-15 | fortnightly | 557 |
| 2026-06-30 | monthly | 2,078 |
| 2026-06-15 | fortnightly | 561 |

**Total slices:** 6 (2026-06-15 through 2026-08-31)

---

## 8. Deliverables & Gaps

### Completed

- [x] Environment setup (`npm ci`, venv, requirements)
- [x] Full holdings cloud pipeline with `--push`
- [x] Data published to `kushagra-agarwal-a/fund-holdings-data`
- [x] Filings catalog refreshed
- [x] JSON probe report written
- [x] Run summary documented in automation memory (`/cursor/stores/automation/memories/MEMORIES.md`)

### Not completed

- [ ] **Slack DM** — automation has no Slack action configured; summary was returned in the agent response only. Enable a Slack DM action in the [Cloud Agents dashboard](https://cursor.com/dashboard?tab=cloud-agents) for automatic delivery.

---

## 9. Recommendations

1. **Lower default concurrency** — Consider setting `FETCH_CONCURRENCY=4` (or `5`) in the automation environment to reduce Node exit-13 failures on hung `static_html` adapters (Groww, Helios).
2. **Enable Slack action** — Add a Slack DM step to automation `97f6c97a-a852-11f1-b532-320a589b8025` so daily reports reach the owner without relying on agent text output.
3. **Retry policy** — First-attempt failure at concurrency=8 is recoverable with a lower-concurrency retry; optionally bake `FETCH_CONCURRENCY=4` into the cron env to avoid the extra ~10 min failed attempt.
4. **2026-09 monthly errors** — ICICI, Mirae, and NJ failed on 2026-09 monthly fetch; likely transient or not-yet-published. Monitor on next daily run.

---

## 10. Artifacts

| Artifact | Path |
|----------|------|
| JSON run report | `data/probes/cloud-holdings-report-1788611398516.json` |
| Pipeline doc | `docs/CURSOR_CLOUD_HOLDINGS.md` |
| Automation memory | `/cursor/stores/automation/memories/MEMORIES.md` |
| This session report | `docs/reports/holdings-cloud-run-2026-09-05.md` |

---

## 11. Raw report JSON (summary fields)

```json
{
  "started_at": "2026-09-05T12:05:48.925Z",
  "finished_at": "2026-09-05T12:29:58.516Z",
  "mode": "push",
  "periods": ["2026-08", "2026-09"],
  "new_portfolio_files": 5845,
  "fetch_totals": {
    "amcs_checked": 198,
    "amcs_ok": 18,
    "amcs_empty": 177,
    "amcs_error": 3,
    "files_downloaded": 118
  },
  "push_error": null,
  "edelweiss_secret_present": true,
  "holdings_token_present": true
}
```
