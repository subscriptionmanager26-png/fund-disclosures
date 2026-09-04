# Repository architecture (OpenFin)

## Rule of thumb

| What | Where | OpenFin uses |
|------|--------|--------------|
| **Holdings data** (catalog, portfolios, filings, meta) | **`kushagra-agarwal-a/fund-holdings-data` only** | Yes — sole data source |
| **Parser / pipeline** (fetch, parse, sync scripts) | **Both** accounts (mirrored) | No — internal tooling |

OpenFin (`openfin.pocketedge.in`) reads **only** from `kushagra-agarwal-a/fund-holdings-data`.

Do **not** maintain holdings copies on `subscriptionmanager26-png` or anywhere else.

## GitHub accounts

### `kushagra-agarwal-a` (canonical for OpenFin)

| Repo | Role |
|------|------|
| [`fund-holdings-data`](https://github.com/kushagra-agarwal-a/fund-holdings-data) | Public CDN store — catalog + `portfolios/asof/*` |
| [`fund-disclosures`](https://github.com/kushagra-agarwal-a/fund-disclosures) | Parser pipeline (mirror of subscriptionmanager copy) |

### `subscriptionmanager26-png` (parser mirror)

| Repo | Role |
|------|------|
| [`fund-disclosures`](https://github.com/subscriptionmanager26-png/fund-disclosures) | Parser pipeline (development mirror) |
| [`pocketedge`](https://github.com/subscriptionmanager26-png/pocketedge) | OpenFin API **app** (Vercel); reads kushagra data |
| ~~`fund-holdings-data`~~ | **Deprecated** — do not sync or reference |

## Sync flow

```text
fund-disclosures (either account)
  fetch → parse → enrich → holdings:sync* --push
                              ↓
              kushagra-agarwal-a/fund-holdings-data
                              ↓
              openfin.pocketedge.in/api/v1/*
```

## Keeping parser repos in sync

After committing on either account:

```bash
npm run parser:push-both
```

Or manually:

```bash
git push origin main          # subscriptionmanager26-png
git push kushagra main        # kushagra-agarwal-a
```

Requires `HOLDINGS_GH_TOKEN` (kushagra-agarwal-a PAT with `repo` on **both** repos).

## Environment

| Variable | Value |
|----------|--------|
| `HOLDINGS_DATA_OWNER` | `kushagra-agarwal-a` (default in sync scripts) |
| `HOLDINGS_DATA_REPO` | `fund-holdings-data` |
