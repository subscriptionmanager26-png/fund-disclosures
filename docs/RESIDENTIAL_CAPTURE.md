# Residential link capture (Navi, Union, …)

Some AMC sites block datacenter IPs. When cloud fetch fails, use a phone or home network to capture download URLs, then publish a small JSON manifest the daily agent reads automatically.

## Capture page (mobile-friendly)

After GitHub Pages deploys:

**https://subscriptionmanager26-png.github.io/fund-disclosures/residential-capture/**

1. Choose AMC, period (`YYYY-MM`), cadence.
2. Paste API JSON (e.g. Navi `documents` POST response) **or** paste CDN URLs one per line.
3. Tap **Build manifest** → **Copy JSON** or **Open GitHub to save**.
4. Commit to:

```text
data/residential-capture/manifests/<amc-slug>/<YYYY-MM>.<cadence>.json
```

Example: `data/residential-capture/manifests/navi-mutual-fund/2026-09.fortnightly.json`

## Navi on phone (bookmarklet)

1. Open the capture page → **Navi helper** tab.
2. Copy the bookmarklet → add as a Safari/Chrome bookmark (URL = bookmarklet code).
3. On [navi.com portfolio page](https://navi.com/mutual-fund/downloads/portfolio), run the bookmarklet.
4. It calls the API from your phone, copies manifest JSON to clipboard.
5. Paste into capture page or save directly to GitHub.

## How the cloud agent uses manifests

When Python fetch is empty or errors, `pythonRef` falls back to:

1. Local file: `data/residential-capture/manifests/...`
2. Remote raw URL (same path on `main`):

```text
https://raw.githubusercontent.com/subscriptionmanager26-png/fund-disclosures/main/data/residential-capture/manifests/<slug>/<period>.<cadence>.json
```

CDN links (e.g. `public-assets.prod.navi-tech.in`) download fine from the cloud — only the AMC API/HTML is blocked.

Set `RESIDENTIAL_MANIFEST_FIRST=1` to skip Python fetch and use manifests only.

## Manifest format

```json
{
  "amc_id": "navi-mutual-fund",
  "period": "2026-09",
  "cadence": "fortnightly",
  "captured_at": "2026-10-01T12:00:00Z",
  "files": [
    {
      "title": "Navi Liquid Fund 1st to 15th September 2026",
      "download_url": "https://public-assets.prod.navi-tech.in/.../file.xlsx",
      "saved_as": "Navi_Liquid_Fund_....xlsx"
    }
  ]
}
```

## Desktop scripts (optional)

```bash
bash scripts/fetch-navi-local.sh 2026-09
bash scripts/fetch-union-local.sh 2026-09
```

These stage under `data/staging/python/`; residential manifests are the lighter path when you only need to paste API output from DevTools.
