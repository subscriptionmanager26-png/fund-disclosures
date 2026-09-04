# Cursor Cloud — automated holdings update

Run from **`kushagra-agarwal-a/fund-disclosures`** (preferred) or `subscriptionmanager26-png/fund-disclosures` — same parser, mirrored.

Holdings publish **only** to `kushagra-agarwal-a/fund-holdings-data` (OpenFin’s sole data source).

## Required secrets (Cursor Cloud → Agent secrets)

| Secret | Purpose |
|--------|---------|
| `HOLDINGS_GH_TOKEN` | Push to `kushagra-agarwal-a/fund-holdings-data` |
| `EDELWEISS_API_SECRET` | Edelweiss AMC statutory fetch |
| `GH_TOKEN` | Optional; defaults to `HOLDINGS_GH_TOKEN` for git operations |

Never commit tokens. `.env` is gitignored.

## One-command update

```bash
npm ci
python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
export GH_TOKEN="${HOLDINGS_GH_TOKEN:-$GH_TOKEN}"
node scripts/cloud-holdings-update.mjs --push
```

`--push` publishes to GitHub. Omit for a dry run (fetch/parse/sync locally only).

## What the script does

1. **Fetch** — current and previous calendar month (`monthly` + `fortnightly` AMC packs)
2. **Parse** — resume-safe parse for those periods
3. **Enrich + locks** — `holdings:enrich`, `holdings:assert-locks`
4. **Sync** — `holdings:sync-window` for a rolling 3-month window → `kushagra-agarwal-a/fund-holdings-data`
5. **Catalog** — `holdings:refresh-filings --push`
6. **Verify** — `GET https://openfin.pocketedge.in/api/v1/filings` and spot-check one AMFI code

Regression guards block accidental portfolio deletions. If sync fails with a regression error, **do not** use `--allow-regression` unless the drop is intentional.

## Manual month-end (first weekday of month)

On the first run of each month, also run the full mapping loop from `docs/PIPELINE.md` steps 3–6 (`amfi:catalog`, `amfi:match:reuse`, `holdings:catalog`, `registry:persist`) before the cloud script.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `403` pushing holdings | `HOLDINGS_GH_TOKEN` must be a `kushagra-agarwal-a` PAT with `repo` scope |
| `Holdings data regression blocked` | Parsed data missing vs repo; re-fetch/parse or investigate — never force-push |
| OpenFin API stale | Wait 2 min (CDN TTL) or confirm `pocketedge` deploy has meta.json commit pinning |
| `.venv` missing | Re-run `python3 -m venv .venv && pip install -r requirements.txt` |

## Canonical repos (OpenFin)

| Repo | Account |
|------|---------|
| Data | `kushagra-agarwal-a/fund-holdings-data` |
| Parser | `kushagra-agarwal-a/fund-disclosures` (mirror: `subscriptionmanager26-png/fund-disclosures`) |

No holdings data on subscriptionmanager26-png.
