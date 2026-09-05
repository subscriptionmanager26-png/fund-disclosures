#!/usr/bin/env python3
"""Validate AWS S3 Raw & Normalized Parquet outputs."""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
import boto3
import pandas as pd


def validate_aws(bucket_name: str, period: str, amc_filter: str | None = None) -> dict:
    s3 = boto3.client("s3")
    
    # 1. Audit Raw
    raw_prefix = "fund_holdings/raw/monthly/"
    raw_res = s3.list_objects_v2(Bucket=bucket_name, Prefix=raw_prefix)
    raw_files = [
        obj["Key"] for obj in raw_res.get("Contents", []) 
        if period in obj["Key"] and (not amc_filter or f"/{amc_filter}/" in obj["Key"])
    ]

    # 2. Audit Parquet
    norm_prefix = "fund_holdings/normalized/"
    norm_res = s3.list_objects_v2(Bucket=bucket_name, Prefix=norm_prefix)
    parquet_files = [
        obj["Key"] for obj in norm_res.get("Contents", []) 
        if obj["Key"].endswith(".parquet") and period in obj["Key"]
    ]

    print(f"\n--- [AWS S3 Audit] Raw Files: {len(raw_files)}, Parquet Schemes: {len(parquet_files)} ---")
    
    total_rows = 0
    errors = []

    for key in parquet_files:
        obj = s3.get_object(Bucket=bucket_name, Key=key)
        df = pd.read_parquet(io.BytesIO(obj["Body"].read()))
        row_count = len(df)
        total_rows += row_count
        amfi_code = key.split("/")[-1].replace(".parquet", "")

        for col in ["amfi_code", "instrument", "market_value", "pct_nav", "holding_type"]:
            if col not in df.columns:
                errors.append(f"{key}: missing column {col}")

        if row_count == 0:
            errors.append(f"{key}: 0 rows")

        scheme_name = df["amfi_name"].iloc[0] if "amfi_name" in df.columns else "unknown"
        print(f"  ✓ [AMFI: {amfi_code}] {scheme_name}: {row_count} rows")

    passed = len(errors) == 0 and len(parquet_files) > 0
    print(f"\n=== AWS Validation Status: {'PASS' if passed else 'FAIL'} (Total Rows: {total_rows}) ===")
    return {"passed": passed, "total_rows": total_rows, "errors": errors}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--period", required=True)
    ap.add_argument("--amc")
    args = ap.parse_args()
    res = validate_aws(args.bucket, args.period, args.amc)
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
