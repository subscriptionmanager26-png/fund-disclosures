# OpenFin API (pocketedge) — catalog slim response

The live API at `openfin.pocketedge.in/api/v1/catalog` is implemented in
[`subscriptionmanager26-png/pocketedge`](https://github.com/subscriptionmanager26-png/pocketedge).

## Deploy

1. Copy `api/_lib/fundHoldingsCdn.ts` and `api/v1/catalog.ts` into the pocketedge repo.
2. Deploy pocketedge to Vercel (production).

The catalog endpoint serves `catalog/amfi-public.json` from fund-holdings-data
(four fields per scheme). Holdings resolution still uses full `amfi-lookup.json`.

## Fields

| Field | Purpose |
|-------|---------|
| `amfi_code` | AMFI scheme code |
| `parent_name` | Parent fund name |
| `available_as_of` | Published as-of dates (newest first) |
| `latest_as_of` | Newest holdings date for this scheme |
