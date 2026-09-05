#!/usr/bin/env bash
set -eo pipefail

# ==============================================================================
# Unified Multi-Cloud Ingestion Job Entrypoint (GCP / AWS / Azure)
# ==============================================================================

if [ -z "$PERIOD" ]; then
  PERIOD=$(python3 -c "from datetime import datetime, timedelta; now = datetime.now(); print((now.replace(day=1) - timedelta(days=1)).strftime('%Y-%m'))" 2>/dev/null || echo "")
fi
TYPE="${TYPE:-monthly}"
AMC="${AMC:-}"
CLOUD_PROVIDER="${CLOUD_PROVIDER:-}"

# Auto-detect Cloud Provider from env variables if not explicitly provided
if [ -z "$CLOUD_PROVIDER" ]; then
  if [ -n "$GCS_BUCKET" ]; then
    CLOUD_PROVIDER="gcp"
  elif [ -n "$S3_BUCKET" ] || [ -n "$AWS_STORAGE_BUCKET" ]; then
    CLOUD_PROVIDER="aws"
  elif [ -n "$AZURE_CONTAINER" ] || [ -n "$AZURE_STORAGE_ACCOUNT" ]; then
    CLOUD_PROVIDER="azure"
  else
    CLOUD_PROVIDER="gcp"
  fi
fi

echo "================================================================="
echo " Fund Disclosures Unified Ingestion Job"
echo " Started at:     $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo " Cloud Provider: $CLOUD_PROVIDER"
echo " Period:         $PERIOD"
echo " Type:           $TYPE"
echo " AMC Filter:     ${AMC:-ALL}"
echo "================================================================="

case "$CLOUD_PROVIDER" in
  gcp)
    echo "--> Launching GCP Cloud Run workflow..."
    exec /bin/bash scripts/gcp/cloud_run_job.sh
    ;;
  aws)
    echo "--> Launching AWS (S3 / ECS / Batch) workflow..."
    exec /bin/bash scripts/aws/aws_job.sh
    ;;
  azure)
    echo "--> Launching Azure (Blob / Container Apps) workflow..."
    exec /bin/bash scripts/azure/azure_job.sh
    ;;
  *)
    echo "ERROR: Unknown CLOUD_PROVIDER '$CLOUD_PROVIDER'. Must be gcp, aws, or azure."
    exit 1
    ;;
esac
