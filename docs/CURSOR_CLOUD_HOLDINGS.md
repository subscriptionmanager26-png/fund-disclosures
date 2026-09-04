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

| Secret | Purpose |
|--------|---------|
| `HOLDINGS_GH_TOKEN` | `kushagra-agarwal-a` PAT with `repo` on `fund-holdings-data` |
| `EDELWEISS_API_SECRET` | Edelweiss AMC fetch |

Optional env (defaults are fine):

| Var | Default | Purpose |
|-----|---------|---------|
| `FETCH_TIMEOUT_MS` | `180000` | Per-request timeout (avoids false errors on slow AMC sites) |
| `FETCH_CONCURRENCY` | `4` | Parallel AMC fetches (lower = fewer timeouts) |

## What `holdings:cloud` does

1. Fetch **monthly + fortnightly** for the last **3 calendar months**
2. Parse all AMCs (`--all`)
3. Enrich identifiers (`--allow-incomplete` — partial months are normal early in the month)
4. **Merge-sync** to GitHub (never deletes existing portfolio files)
5. Refresh `catalog/filings.json` + pin `meta.json`
6. Verify `https://openfin.pocketedge.in/api/v1/filings`
7. Write JSON report under `pipeline/data/probes/cloud-holdings-report-*.json`

## Safety rules

- **Never** use `--allow-regression` or prune scripts in the daily job
- Sync always uses `--merge` (additive fortnightly; monthly only replaces schemes present in the new parse)
- If regression guard blocks a push, investigate — do not bypass

## Interpreting fetch results

| Status | Meaning |
|--------|---------|
| **ok** | Files downloaded for this period |
| **empty** | Scraper ran; AMC has not published this period yet (common early in the month) |
| **error** | Request failed (timeout, network) — retry next day or lower concurrency |

`empty` is not a bug. Most AMCs publish month-end monthly 3–7 days after month close.

## Slack report (automation prompt)

After the run, DM a short summary: periods fetched, AMC ok/empty/error counts, new portfolio files, new as-of dates, OpenFin filings list, and any push failures.

## Canonical repo

**Only** `kushagra-agarwal-a/fund-holdings-data` — data + parser. No holdings on other accounts.

`subscriptionmanager26-png/fund-disclosures` is an optional parser mirror (`npm run parser:mirror-subscriptionmanager`).
