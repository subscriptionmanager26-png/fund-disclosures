# Residential capture — APIs from your phone

Cloud servers cannot call Navi / Union APIs (IP blocks). **Your phone can.** The capture tool runs the **same API calls in your mobile browser** on each AMC’s website, uploads CDN links to a Cloudflare Worker, and the daily agent downloads files from there.

## Why not one server-side call?

Navi and Union reject datacenter IPs (Cloudflare 403, TLS reset). The APIs work from a residential/mobile IP. Browsers only allow those calls when JavaScript runs **on the AMC’s own domain** (`navi.com`, `unionmf.com`, …) — that is exactly what this tool does.

## One-time setup

```bash
export CLOUDFLARE_API_TOKEN=… CLOUDFLARE_ACCOUNT_ID=… CAPTURE_PIN=4829
bash scripts/deploy-residential-capture.sh
```

Add Cloud Agent secret: `RESIDENTIAL_CAPTURE_URL=<worker *.workers.dev URL>`.

On your phone:

https://subscriptionmanager26-png.github.io/fund-disclosures/residential-capture/

1. Enter worker URL + PIN → **Save**
2. Tap **Install auto-helper (Tampermonkey)** → confirm install (one time)
3. Optional: copy the **Capture all** bookmark if you prefer not to use Tampermonkey

## Each fortnight — one button

1. Tap **Capture all AMCs**
2. Your phone opens Navi → calls its API → Union → Abakkus → uploads manifests (~20s)
3. Done screen. Cloud agent uses manifests on the next run.

With Tampermonkey installed, you only tap once. Without it, tap the **Capture all** bookmark when each new AMC page opens (~3 taps) — browsers cannot auto-run code on a new website without an extension.

## AMCs in the chain

| Order | AMC | Method |
|-------|-----|--------|
| 1 | Navi | `wp-json/nv/v1/documents` API |
| 2 | Union | Fortnightly HTML / OData API |
| 3 | Abakkus | `verticals-data` JSON on disclosures page |

Add more in `workers/residential-capture/public/chain.mjs`.

## Cloud agent

`pythonRef` falls back to `RESIDENTIAL_CAPTURE_URL` when datacenter fetch fails. CDN downloads (`public-assets.prod.navi-tech.in`, `unionmf.com/docs/…`) work from cloud.
