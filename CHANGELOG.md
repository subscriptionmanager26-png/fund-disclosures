# Changelog

All notable changes to the **Fund Disclosures** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

- Skip if holdings already exists ([#9](https://github.com/Raghavendra17N/fund-disclosures/pull/9)) by @Raghavendra17N

- Date fixes in Nightly Cloud Run Job ([#8](https://github.com/Raghavendra17N/fund-disclosures/pull/8)) by @Raghavendra17N

- Skip upload when files already exists ([#7](https://github.com/Raghavendra17N/fund-disclosures/pull/7)) by @Raghavendra17N

- Option to upload only PARQUET_ONLY=true. ([#6](https://github.com/Raghavendra17N/fund-disclosures/pull/6)) by @Raghavendra17N

- Docker Image size reduction  ([#5](https://github.com/Raghavendra17N/fund-disclosures/pull/5)) by @Raghavendra17N

- Changelog testing ([#4](https://github.com/Raghavendra17N/fund-disclosures/pull/4)) by @Raghavendra17N

- Changelog change ([#3](https://github.com/Raghavendra17N/fund-disclosures/pull/3)) by @Raghavendra17N

### Added
- Multi-cloud ingestion support for **Google Cloud Storage (GCS)**, **Amazon Web Services (S3)**, and **Microsoft Azure (Blob Storage)**.
- Unified container entrypoint (`scripts/cloud_job.sh`) with dynamic cloud provider routing.
- Columnar Snappy-compressed Parquet export with official API contract types (`holding_type`, numeric `quantity`, `market_value`, `pct_nav`, `coupon`, `ytm`).
- Validation auditors for GCP (`validate_gcp_output.py`), AWS (`validate_s3_output.py`), and Azure (`validate_azure_output.py`).
- Comprehensive Cloud Deployment & GCP Cloud Run Scheduler documentation (`docs/MULTI_CLOUD_DEPLOYMENT.md`, `docs/GCP_CLOUD_RUN_SCHEDULER.md`).
- Automated changelog update workflow on PR merge.

### Changed
- Dropped duplicate JSON uploads in favor of high-performance analytical Parquet datasets.

---

## [1.0.0] - 2026-08-26

### Added
- Statutory AMC disclosure fetchers (monthly and fortnightly).
- Unified multi-family Excel and ZIP portfolio parsers.
- AMFI canonical scheme code identifier enrichment engine.
- Web API and local holdings browser UI.
- Base test suite and shortcode mapping registry for 44 AMCs.
