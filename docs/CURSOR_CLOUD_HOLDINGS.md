# Cursor Cloud — automated holdings update

Run from **`kushagra-agarwal-a/fund-holdings-data`** (clone repo, work in `pipeline/`).

Holdings publish to **repo root** (`catalog/`, `portfolios/`, `meta.json`). OpenFin reads only those paths.

## Required secrets

| Secret | Purpose |
|--------|---------|
| `HOLDINGS_GH_TOKEN` | `kushagra-agarwal-a` PAT with `repo` on `fund-holdings-data` |
| `EDELWEISS_API_SECRET` | Edelweiss AMC fetch |

## One-command update

```bash
git clone https://github.com/kushagra-agarwal-a/fund-holdings-data.git
cd fund-holdings-data/pipeline
npm ci
python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
export GH_TOKEN="$HOLDINGS_GH_TOKEN"
npm run holdings:cloud -- --push
```

## Canonical repo

**Only** `kushagra-agarwal-a/fund-holdings-data` — data + parser. No holdings on other accounts.

`subscriptionmanager26-png/fund-disclosures` is an optional parser mirror (`npm run parser:mirror-subscriptionmanager`).
