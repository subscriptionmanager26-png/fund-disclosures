# Repository architecture (OpenFin)

## `kushagra-agarwal-a` — single source of truth

| Path | Role |
|------|------|
| [`fund-holdings-data`](https://github.com/kushagra-agarwal-a/fund-holdings-data) | **Everything OpenFin needs** |
| `catalog/`, `portfolios/`, `meta.json` | Published holdings (CDN) |
| `pipeline/` | Parser (fetch, parse, sync) |

OpenFin (`openfin.pocketedge.in`) reads only `catalog/`, `portfolios/`, `meta.json` at repo root.

## `subscriptionmanager26-png` — parser mirror only

| Repo | Role |
|------|------|
| [`fund-disclosures`](https://github.com/subscriptionmanager26-png/fund-disclosures) | Parser mirror (no holdings copy) |
| [`pocketedge`](https://github.com/subscriptionmanager26-png/pocketedge) | OpenFin API app (Vercel) |

Do **not** maintain holdings on `subscriptionmanager26-png`.

## Workflow

```text
pipeline/  (in fund-holdings-data, or mirror on subscriptionmanager)
  fetch → parse → holdings:sync* --push  →  repo root (catalog, portfolios)
                                              ↓
                              openfin.pocketedge.in/api/v1/*
```

## Secrets

`HOLDINGS_GH_TOKEN` — `kushagra-agarwal-a` PAT with `repo` on `fund-holdings-data`.
