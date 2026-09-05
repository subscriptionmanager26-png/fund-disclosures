#!/usr/bin/env bash
set -eo pipefail

PERIOD="${PERIOD:-2026-07}"
TYPE="${TYPE:-monthly}"
AMC="${AMC:-}"
AZURE_CONTAINER="${AZURE_CONTAINER:-}"

echo "================================================================="
echo " Azure Ingestion Job (Container Apps / Batch): Fund Disclosures"
echo " Period:     $PERIOD | AMC: ${AMC:-ALL} | Container: ${AZURE_CONTAINER:-NONE}"
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

# 4. Azure Blob Export & Audit
if [ -n "$AZURE_CONTAINER" ]; then
  if [ -n "$AMC" ]; then
    python3 scripts/azure/azure_exporter.py --period="$PERIOD" --container="$AZURE_CONTAINER" --cadence="$TYPE" --amc="$AMC"
    python3 scripts/azure/validate_azure_output.py --period="$PERIOD" --container="$AZURE_CONTAINER" --amc="$AMC"
  else
    python3 scripts/azure/azure_exporter.py --period="$PERIOD" --container="$AZURE_CONTAINER" --cadence="$TYPE"
    python3 scripts/azure/validate_azure_output.py --period="$PERIOD" --container="$AZURE_CONTAINER"
  fi
fi

echo "================================================================="
echo " Azure Job Complete"
echo "================================================================="
