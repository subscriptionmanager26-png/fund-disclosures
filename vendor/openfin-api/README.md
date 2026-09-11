# OpenFin API (pocketedge) — catalog slim response

The live API at `openfin.pocketedge.in/api/v1/catalog` is implemented in
[`subscriptionmanager26-png/pocketedge`](https://github.com/subscriptionmanager26-png/pocketedge).

## Deploy (required for `/api/v1/catalog` to slim down)

The slim file is already on CDN (`catalog/amfi-public.json`). The live API still
serves the old shape until pocketedge is updated:

1. Copy `vendor/openfin-api/api/_lib/fundHoldingsCdn.ts` → `pocketedge/api/_lib/fundHoldingsCdn.ts`
2. Copy `vendor/openfin-api/api/v1/catalog.ts` → `pocketedge/api/v1/catalog.ts`
3. Commit and deploy pocketedge to Vercel (production).

After deploy, `GET /api/v1/catalog` proxies `catalog/amfi-public.json` (four fields).
Holdings resolution still uses full `catalog/amfi-lookup.json`.

**Interim:** clients may fetch the slim CDN file directly (same four fields).

## Fields

| Field | Purpose |
|-------|---------|
| `amfi_code` | AMFI scheme code |
| `parent_name` | Parent fund name |
| `available_as_of` | Published as-of dates (newest first) |
| `latest_as_of` | Newest holdings date for this scheme |
