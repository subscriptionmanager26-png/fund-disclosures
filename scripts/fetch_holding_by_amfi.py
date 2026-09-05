#!/usr/bin/env python3
import io
import json
import os
import sys
from pathlib import Path
import pandas as pd
from google.cloud import storage
from google.oauth2 import service_account

sa_path = Path(__file__).resolve().parents[2] / "tracker-backend" / "gcp-sa.json"

def fetch_holding(amfi_code: str = "120828", period: str = "2026-07-31", bucket_name: str = "investmentflow-market-data"):
    if sa_path.exists():
        creds = service_account.Credentials.from_service_account_file(str(sa_path))
        client = storage.Client(credentials=creds, project=creds.project_id)
    else:
        client = storage.Client()
    bucket = client.bucket(bucket_name)

    # 1. Resolve canonical code via catalog map if available
    canonical_code = amfi_code
    plan_info = {}
    catalog_blob = bucket.blob("fund_holdings/catalog/amfi_to_portfolio_id_map.json")
    if catalog_blob.exists():
        try:
            catalog = json.loads(catalog_blob.download_as_bytes())
            if amfi_code in catalog:
                plan_info = catalog[amfi_code]
                canonical_code = plan_info.get("canonical_amfi_code") or amfi_code
                print(f"[Catalog Lookup] Resolved AMFI {amfi_code} ({plan_info.get('plan_name')}) -> Canonical {canonical_code} ({plan_info.get('parquet_file')})")
        except Exception as e:
            print(f"[Catalog Notice] Could not load catalog map: {e}")

    blob_path = f"fund_holdings/normalized/as_of={period}/{canonical_code}.parquet"
    blob = bucket.blob(blob_path)

    if not blob.exists():
        print(f"Error: gs://{bucket_name}/{blob_path} does not exist.")
        return 1

    content = blob.download_as_bytes()
    df = pd.read_parquet(io.BytesIO(content))

    scheme_name = plan_info.get("scheme_name") or (df["amfi_name"].iloc[0] if "amfi_name" in df.columns else "Unknown Scheme")
    amc_id = df["amc_id"].iloc[0] if "amc_id" in df.columns else ""
    as_of = df["as_of"].iloc[0] if "as_of" in df.columns else period

    print(f"\n{'='*90}")
    print(f" Scheme:    {scheme_name}")
    print(f" Requested AMFI: {amfi_code} | Canonical AMFI: {canonical_code} | AMC: {amc_id} | As Of: {as_of}")
    print(f" Total Rows in Portfolio: {len(df)}")
    print(f"{'='*90}\n")


    display_cols = ["instrument", "isin", "holding_type", "pct_nav", "market_value", "industry", "rating"]
    available_cols = [c for c in display_cols if c in df.columns]

    sorted_df = df.sort_values(by="pct_nav", ascending=False).reset_index(drop=True)
    pd.set_option("display.max_rows", 150)
    pd.set_option("display.max_columns", 10)
    pd.set_option("display.width", 120)
    pd.set_option("display.float_format", "{:.2f}".format)

    print(sorted_df[available_cols].to_string(index=True))
    print(f"\n{'='*90}")
    print(f" Summary: {len(df)} total holdings fetched directly from gs://{bucket_name}/{blob_path}")
    print(f"{'='*90}\n")
    return 0

if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "120828"
    p = sys.argv[2] if len(sys.argv) > 2 else "2026-07-31"
    b = sys.argv[3] if len(sys.argv) > 3 else "investmentflow-market-data"
    sys.exit(fetch_holding(code, p, b))
