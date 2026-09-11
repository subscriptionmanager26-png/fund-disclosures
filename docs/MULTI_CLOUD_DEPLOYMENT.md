# Multi-Cloud Ingestion & Storage Architecture (GCP, AWS, Azure)

Unified, cloud-agnostic container pipeline for Indian Mutual Fund holdings ingestion and analytical Parquet storage across **Google Cloud Platform (GCP)**, **Amazon Web Services (AWS)**, and **Microsoft Azure**.

---

## 1. Overview

The pipeline packages statutory scrapers, Excel family parsers, AMFI identifier enrichers, and cloud object exporters into a single multi-runtime Docker image (`python:3.11-slim` + `Node.js 20`).

The container dynamically routes uploads to your cloud provider based on environment variables:
* **GCP**: Google Cloud Run Job $\rightarrow$ Google Cloud Storage (GCS)
* **AWS**: Amazon ECS / Fargate / Batch $\rightarrow$ Amazon S3
* **Azure**: Azure Container Apps / Batch $\rightarrow$ Azure Blob Storage

---

## 2. Directory Structure

```text
scripts/
├── cloud_job.sh                  # Unified Master Entrypoint (auto-detects provider)
├── gcp/                          # Google Cloud Platform
│   ├── gcp_exporter.py           # Uploads raw & Parquet to GCS
│   ├── validate_gcp_output.py    # Reads back & audits GCS Parquet
│   └── cloud_run_job.sh          # Cloud Run Job runner
├── aws/                          # Amazon Web Services
│   ├── s3_exporter.py            # Uploads raw & Parquet to Amazon S3
│   ├── validate_s3_output.py     # Reads back & audits S3 Parquet
│   └── aws_job.sh                # ECS / Batch runner
└── azure/                        # Microsoft Azure
    ├── azure_exporter.py         # Uploads raw & Parquet to Azure Blob
    ├── validate_azure_output.py  # Reads back & audits Azure Parquet
    └── azure_job.sh              # Container Apps runner
```

---

## 3. Object Storage Layout (All Clouds)

Both raw statutory source workbooks and contract-shaped Parquet files follow identical deterministic partitioning across all storage providers:

### A. Raw Statutory Source Files
* **GCS**: `gs://<bucket>/fund_holdings/raw/{cadence}/{as_of}/{amc_id}/{filename}`
* **S3**: `s3://<bucket>/fund_holdings/raw/{cadence}/{as_of}/{amc_id}/{filename}`
* **Azure**: `https://<account>.blob.core.windows.net/<container>/fund_holdings/raw/{cadence}/{as_of}/{amc_id}/{filename}`

*Metadata attached on upload:* `amc_id`, `as_of`, `cadence`, `sha256`, `retrieved_at`.

### B. Normalized Columnar Parquet Files
* **GCS**: `gs://<bucket>/fund_holdings/normalized/as_of={as_of}/{amfi_code}.parquet`
* **S3**: `s3://<bucket>/fund_holdings/normalized/as_of={as_of}/{amfi_code}.parquet`
* **Azure**: `https://<account>.blob.core.windows.net/<container>/fund_holdings/normalized/as_of={as_of}/{amfi_code}.parquet`

---

## 4. Analytical Parquet Schema (API Contract Compatible)

Every Parquet file adheres strictly to the official API contract schema:

| Column | Type | Description |
| :--- | :--- | :--- |
| `holding_type` | `string` | Auto-classified: `equity`, `debt`, `money_market`, `cash`, `derivative`, `commodity`, `fund_unit`, `other` |
| `instrument` | `string` | Security/Issuer name (e.g. `HDFC Bank Limited`) |
| `isin` | `string` | 12-character ISIN code (e.g. `INE040A01034`), or `null` for cash |
| `section` | `string` | Disclosed regulatory portfolio section |
| `industry` | `string` | Sector/Industry classification (e.g. `Banks`) |
| `rating` | `string` | Credit rating (e.g. `CRISIL AAA`, `SOVEREIGN`), or `null` for equity |
| `coupon` | `float64` | Coupon percentage (e.g. `7.18`), or `null` |
| `maturity_date` | `string` | Maturity date string (`YYYY-MM-DD`), or `null` |
| `quantity` | `int64` / `float64` | Disclosed units/shares |
| `market_value` | `float64` | Market value in ₹ Lakhs (`INR_LAKH`) |
| `pct_nav` | `float64` | Percentage of fund NAV (`percent`) |
| `ytm` | `float64` | Yield to maturity, or `null` |
| `ytc` | `float64` | Yield to call, or `null` |
| `instrument_yield`| `float64`| Disclosed yield, or `null` |
| `listed_status` | `string` | `listed` / `unlisted` / `awaiting_listing`, or `null` |
| `underlying` | `string` | Underlying asset for derivatives, or `null` |
| `position_side` | `string` | `long` / `short`, or `null` |
| `amfi_code` | `string` | Canonical 6-digit AMFI Scheme Code |
| `amfi_name` | `string` | Canonical AMFI Scheme Name |
| `amc_id` | `string` | AMC slug identifier (e.g. `nj-mutual-fund`, `quant-mutual-fund`) |
| `as_of` | `string` | Calendar disclosure date (`YYYY-MM-DD`) |
| `source_file` | `string` | AMC disclosure filename |

---

## 5. Execution & Deployment

### Build the Docker Image
```bash
docker build -t fund-holding-backend:latest .
```

### Run on Google Cloud Platform (GCP)
```bash
docker run --rm \
  -e CLOUD_PROVIDER="gcp" \
  -e GCS_BUCKET="my-gcs-bucket" \
  -e PERIOD="2026-07" \
  -e AMC="quant-mutual-fund" \
  -e GOOGLE_APPLICATION_CREDENTIALS="/app/gcp-sa.json" \
  -v "$HOME/.config/gcloud/application_default_credentials.json:/app/gcp-sa.json:ro" \
  fund-holding-backend:latest
```

### Run on Amazon Web Services (AWS)
```bash
docker run --rm \
  -e CLOUD_PROVIDER="aws" \
  -e S3_BUCKET="my-s3-bucket" \
  -e PERIOD="2026-07" \
  -e AMC="quant-mutual-fund" \
  -e AWS_ACCESS_KEY_ID="AKIA..." \
  -e AWS_SECRET_ACCESS_KEY="..." \
  -e AWS_DEFAULT_REGION="ap-south-1" \
  fund-holding-backend:latest
```

### Run on Microsoft Azure
```bash
docker run --rm \
  -e CLOUD_PROVIDER="azure" \
  -e AZURE_CONTAINER="fund-holdings" \
  -e AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=...;AccountKey=..." \
  -e PERIOD="2026-07" \
  -e AMC="quant-mutual-fund" \
  fund-holding-backend:latest
```

---

## 6. Verification & Automated Auditing

Each cloud provider has a standalone validation auditor that reads back exported Parquet files directly from the cloud bucket and validates row counts, column types, and AMFI codes:

* **GCP Audit**: `python scripts/gcp/validate_gcp_output.py --bucket=BUCKET --period=2026-07`
* **AWS Audit**: `python scripts/aws/validate_s3_output.py --bucket=BUCKET --period=2026-07`
* **Azure Audit**: `python scripts/azure/validate_azure_output.py --container=CONTAINER --period=2026-07`
