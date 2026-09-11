#!/usr/bin/env bash
set -eo pipefail

PERIOD="${PERIOD:-2026-07}"
TYPE="${TYPE:-monthly}"
AMC="${AMC:-}"
S3_BUCKET="${S3_BUCKET:-}"

echo "================================================================="
echo " AWS Ingestion Job (ECS / Fargate / Batch): Fund Disclosures"
echo " Period:    $PERIOD | AMC: ${AMC:-ALL} | S3 Bucket: ${S3_BUCKET:-NONE}"
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

# 4. S3 Export & Audit
if [ -n "$S3_BUCKET" ]; then
  if [ -n "$AMC" ]; then
    python3 scripts/aws/s3_exporter.py --period="$PERIOD" --bucket="$S3_BUCKET" --cadence="$TYPE" --amc="$AMC"
    python3 scripts/aws/validate_s3_output.py --period="$PERIOD" --bucket="$S3_BUCKET" --amc="$AMC"
  else
    python3 scripts/aws/s3_exporter.py --period="$PERIOD" --bucket="$S3_BUCKET" --cadence="$TYPE"
    python3 scripts/aws/validate_s3_output.py --period="$PERIOD" --bucket="$S3_BUCKET"
  fi
fi

echo "================================================================="
echo " AWS Job Complete"
echo "================================================================="
