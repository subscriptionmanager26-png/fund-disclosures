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
| `EDELWEISS_API_SECRET` | Edelweiss AMC fetch |

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
5. **Merge-sync** to GitHub (skips empty slices; never deletes existing portfolio files unless an explicit verified `--keep-ids` + `--no-merge` repair)
6. Refresh `catalog/filings.json` + pin `meta.json`
7. Verify `https://openfin.pocketedge.in/api/v1/filings`
8. Write JSON report under `data/probes/cloud-holdings-report-*.json`

## Safety rules

- **Never** use `--allow-regression` or prune scripts in the daily job
- Sync always uses `--merge` (additive fortnightly; monthly only replaces schemes present in the new parse)
- If regression guard blocks a push, investigate — do not bypass
- **Never publish** portfolios whose source workbook is not a real disclosure for that as-of/cadence (filename/date/type must agree). Heuristic filters help; they do not replace the LLM cadence gate above.

## Interpreting fetch results

| Status | Meaning |
|--------|---------|
| **ok** | Files downloaded for this period |
| **empty** | Scraper ran; AMC has not published this period yet (common early in the month) |
| **error** | Request failed (timeout, network) — retry next day or lower concurrency |

`empty` is not a bug. Most AMCs publish month-end monthly 3–7 days after month close.

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
