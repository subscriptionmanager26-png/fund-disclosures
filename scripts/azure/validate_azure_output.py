#!/usr/bin/env python3
"""Validate Azure Blob Storage Raw & Normalized Parquet outputs."""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path
from azure.storage.blob import BlobServiceClient
import pandas as pd


def validate_azure(container_name: str, period: str, amc_filter: str | None = None) -> dict:
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        account_name = os.getenv("AZURE_STORAGE_ACCOUNT")
        blob_service_client = BlobServiceClient(account_url=f"https://{account_name}.blob.core.windows.net")
    else:
        blob_service_client = BlobServiceClient.from_connection_string(conn_str)

    container_client = blob_service_client.get_container_client(container_name)

    raw_prefix = "fund_holdings/raw/monthly/"
    raw_blobs = [
        b.name for b in container_client.list_blobs(name_starts_with=raw_prefix)
        if period in b.name and (not amc_filter or f"/{amc_filter}/" in b.name)
    ]

    norm_prefix = "fund_holdings/normalized/"
    parquet_blobs = [
        b.name for b in container_client.list_blobs(name_starts_with=norm_prefix)
        if b.name.endswith(".parquet") and period in b.name
    ]

    print(f"\n--- [Azure Audit] Raw Files: {len(raw_blobs)}, Parquet Schemes: {len(parquet_blobs)} ---")
    
    total_rows = 0
    errors = []

    for name in parquet_blobs:
        blob_client = container_client.get_blob_client(name)
        data = blob_client.download_blob().readall()
        df = pd.read_parquet(io.BytesIO(data))
        row_count = len(df)
        total_rows += row_count
        amfi_code = name.split("/")[-1].replace(".parquet", "")

        for col in ["amfi_code", "instrument", "market_value", "pct_nav", "holding_type"]:
            if col not in df.columns:
                errors.append(f"{name}: missing column {col}")

        if row_count == 0:
            errors.append(f"{name}: 0 rows")

        scheme_name = df["amfi_name"].iloc[0] if "amfi_name" in df.columns else "unknown"
        print(f"  ✓ [AMFI: {amfi_code}] {scheme_name}: {row_count} rows")

    passed = len(errors) == 0 and len(parquet_blobs) > 0
    print(f"\n=== Azure Validation Status: {'PASS' if passed else 'FAIL'} (Total Rows: {total_rows}) ===")
    return {"passed": passed, "total_rows": total_rows, "errors": errors}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", required=True)
    ap.add_argument("--period", required=True)
    ap.add_argument("--amc")
    args = ap.parse_args()
    res = validate_azure(args.container, args.period, args.amc)
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
