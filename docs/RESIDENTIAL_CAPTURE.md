# Residential capture (one tap)

For Navi when cloud fetch is blocked.

## One-time setup (~2 minutes)

1. **Deploy upload worker** (from laptop):
   ```bash
   export CLOUDFLARE_API_TOKEN=… CLOUDFLARE_ACCOUNT_ID=… CAPTURE_PIN=4829
   bash scripts/deploy-residential-capture.sh
   ```
   Copy the `*.workers.dev` URL. Add Cloud Agent secret: `RESIDENTIAL_CAPTURE_URL=<that URL>`.

2. **On your phone** — open  
   https://subscriptionmanager26-png.github.io/fund-disclosures/residential-capture/  
   Enter worker URL + PIN → **Save**.

3. Tap **Install bookmark** → add bookmark named **Navi capture** (paste URL when prompted).

## Every fortnight (one tap)

1. Open [navi.com portfolio](https://navi.com/mutual-fund/downloads/portfolio) on your phone.
2. Tap **Navi capture** bookmark.
3. See “Published N files” — done.

The daily cloud agent reads manifests from the worker and downloads xlsx from CDN.

## Alternative

If on the capture page while already on `navi.com`, tap **Capture & publish Navi** (same as bookmark).
