# Fund Disclosures

Open-source toolkit for **Indian mutual fund portfolio disclosures**: fetch AMC Excel packs, parse holdings, map them to AMFI scheme codes, and serve a public holdings API.

Live API: [https://fund-holdings-browser.vercel.app](https://fund-holdings-browser.vercel.app)

```text
GET /api/amfi/{amfi_code}
GET /api/amfi/{amfi_code}?as_of=2026-07-31
```

Every holding uses the same JSON keys. `meta.market_value_unit` is `INR_LAKH`. Previous/next filing links are on each response; missing periods return `"No Data Found"`.

This is research software, not investment advice. Source files are AMC statutory disclosures.

## Pillars

1. **AMC fetch** (monthly + fortnightly) — `scrapers/`
2. **Excel → holdings parsers** — `parsers/`
3. **Disclosure ↔ AMFI maps** — `data/sources/` + `exports/`
4. **AMFI universe** — `amfi/`
5. **Matching** — `matching/`
6. **Holdings API / browser** — `holdings-browser/`

Also: **QC** (`qc/`), **registry** (`registry/`), **pipeline docs** (`docs/PIPELINE.md`).

## Quick start

```bash
git clone https://github.com/subscriptionmanager26-png/fund-disclosures.git
cd fund-disclosures
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

npm run list -- --stats
npm run fetch -- --type=monthly --period=2026-07 --list-only
npm run parse:amc -- --list
```

Node ≥ 20. Full month-end loop: [docs/PIPELINE.md](docs/PIPELINE.md).

Local holdings browser (after a parse + catalog build):

```bash
npm run holdings:catalog
npm run holdings:browse   # http://127.0.0.1:8777
```

## Public holdings API

| | |
|---|---|
| Latest | `GET https://fund-holdings-browser.vercel.app/api/amfi/122639` |
| By date | `GET https://fund-holdings-browser.vercel.app/api/amfi/122639?as_of=2026-07-31` |

CORS is open. Holdings JSON is stored in object storage; this repo keeps parsers, maps, and the API code.

Dated uploads use:

`fund-disclosures/holdings/{YYYY-MM-DD}/{amc_id}/{amfi}/portfolio.json`

## Layout

```text
registry/           AMC registry, parser families, shortcode map
scrapers/           node fetch CLI + python AMC fetchers
parsers/            AMC Excel parsers
holdings-browser/   public API + scheme search UI
amfi/               NAVAll / as-of / populate-scheme
matching/           disclosure ↔ AMFI match
qc/                 holdings compare
exports/            mapping workbooks
data/               disclosures/ and parsed/ are gitignored (regenerate locally)
docs/               runbooks
```

## Cloud Deployment & Ingestion Pipelines

The ingestion pipeline can be run as a containerized job that fetches statutory disclosures, parses portfolios, enriches them with canonical AMFI codes, and exports Snappy-compressed Parquet datasets directly to cloud object storage.

* **[Multi-Cloud Deployment Guide](docs/MULTI_CLOUD_DEPLOYMENT.md)** — Run on **Google Cloud Platform (GCS)**, **Amazon Web Services (S3)**, or **Microsoft Azure (Blob)** with a single unified container image.
* **[GCP Cloud Run & Cloud Scheduler Guide](docs/GCP_CLOUD_RUN_SCHEDULER.md)** — Step-by-step setup for automated monthly Cloud Run Jobs triggered via Cloud Scheduler.
* **[Data Layout & Conventions](docs/DATA_LAYOUT.md)** — Calendar date partitioning and storage path standard.
* **[Portfolio Deduplication Model](docs/GITHUB_HOLDINGS.md)** — Deduplicating 8,600 share-classes into ~1,900 unique portfolios.

## Policy

- **AMC-direct only** (no Advisorkhoj as primary source).
- In scope: fortnightly + monthly. Semi-annual deferred.
- Edelweiss fetch needs `EDELWEISS_API_SECRET` in the environment. Do not commit secrets.
- Holdings API object storage uses `B2_KEY_ID` / `B2_APPLICATION_KEY` (see `holdings-browser/.env.example`).

## Mapping status (baseline)

Frozen under `exports/baseline/` after Aug 2026 QC:

- Disclosure rows mapped ≈ **2386 / 2387** (Taurus IE pool ignored)
- Shortcode map: `registry/disclosure_shortcode_map.json`

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for history of releases and PR additions.

## License

[MIT](LICENSE)
