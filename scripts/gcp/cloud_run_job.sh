#!/usr/bin/env bash
set -eo pipefail

# Auto-compute previous calendar month (e.g. 2026-08 when running in Sep 2026) if PERIOD is not explicitly set
if [ -z "$PERIOD" ]; then
  PERIOD=$(python3 -c "from datetime import datetime, timedelta; now = datetime.now(); print((now.replace(day=1) - timedelta(days=1)).strftime('%Y-%m'))")
fi
TYPE="${TYPE:-monthly}"
AMC="${AMC:-}"
GCS_BUCKET="${GCS_BUCKET:-}"
PARQUET_ONLY="${PARQUET_ONLY:-true}"

PARQUET_FLAG=""
if [ "$PARQUET_ONLY" = "true" ] || [ "$PARQUET_ONLY" = "1" ]; then
  PARQUET_FLAG="--parquet-only"
fi

echo "================================================================="
echo " GCP Cloud Run Job: Fund Disclosures Ingestion"
echo " Period:       $PERIOD"
echo " AMC:          ${AMC:-ALL}"
echo " GCS Bucket:   ${GCS_BUCKET:-NONE}"
echo " Parquet Only: ${PARQUET_ONLY:-false}"
echo "================================================================="

# 1. Fetch
if [ -n "$AMC" ]; then
  node scrapers/node/fetch-period.js --type="$TYPE" --period="$PERIOD" --amc="$AMC"
else
  node scrapers/node/fetch-period.js --type="$TYPE" --period="$PERIOD"
fi

# 2. Parse
if [ -n "$AMC" ]; then
  python3 parsers/run_amc_parser.py --type="$TYPE" --period="$PERIOD" --amc="$AMC"
else
  python3 parsers/run_amc_parser.py --type="$TYPE" --period="$PERIOD" --all
fi

# 3. Enrich
python3 scripts/enrich_holdings_identifiers.py --allow-incomplete

# 4. GCS Export & Audit
if [ -n "$GCS_BUCKET" ]; then
  if [ -n "$AMC" ]; then
    python3 scripts/gcp/gcp_exporter.py --period="$PERIOD" --bucket="$GCS_BUCKET" --cadence="$TYPE" --amc="$AMC" $PARQUET_FLAG
    python3 scripts/gcp/validate_gcp_output.py --period="$PERIOD" --bucket="$GCS_BUCKET" --amc="$AMC"
  else
    python3 scripts/gcp/gcp_exporter.py --period="$PERIOD" --bucket="$GCS_BUCKET" --cadence="$TYPE" $PARQUET_FLAG
    python3 scripts/gcp/validate_gcp_output.py --period="$PERIOD" --bucket="$GCS_BUCKET"
  fi
fi

echo "================================================================="
echo " GCP Job Complete"
echo "================================================================="

