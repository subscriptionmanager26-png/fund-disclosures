#!/usr/bin/env python3
"""Amazon S3 Exporter for Fund Disclosures.

Uploads raw spreadsheets and contract-shaped Parquet files to AWS S3.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import boto3
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]

MARKET_VALUE_UNIT = "INR_LAKH"
PCT_NAV_UNIT = "percent"

EMPTY_VALUES = {
    "", "-", "--", "—", "na", "n/a", "n.a.", "nil", "null", "none", ".", "nan", "^", "#", "@", "$",
    "% to nav", "% to n.a.v"
}

CASH_RE = re.compile(
    r"treps?|tri[\s-]?party|reverse\s+repos?|\bcblo\b|clearing\s+corporation|\bccil\b|"
    r"amc\s+repo\s+clearing|net\s+current\s+assets?|\bnca\b|net\s+receivables?|net\s+payables?|"
    r"receivable\s*/\s*\(?\s*payable|payables?\s*/\s*\(?\s*receivable|cash\s+margin|margin\s+money|"
    r"cash\s*/\s*bank|cash\s+and\s+other|call,\s*cash|^\s*cash\s*$|^\s*cash\s*/|\brepos?\b|"
    r"^\s*trp[_-]|^\s*rep\d+",
    re.IGNORECASE,
)
NOT_CASH_RE = re.compile(
    r"interest\s+rate\s+swaps?|\birs\b|(?<![A-Za-z])ois(?![A-Za-z])|t[\s-]?bill|treasury\s+bill|"
    r"commercial\s+paper|certificate\s+of\s+deposit|\bdebenture\b|\bncd\b",
    re.IGNORECASE,
)
DERIV_RE = re.compile(
    r"\bfutures?\b|\boptions?\b|\bderivatives?\b|covered\s+call|interest\s+rate\s+swaps?|\birs\b|"
    r"(?<![A-Za-z])ois(?![A-Za-z])|\bswaps?\b",
    re.IGNORECASE,
)
COMMODITY_RE = re.compile(
    r"^\s*\(?\s*(?:[a-z]\)\s*)?gold(?:\s+\d{3}\s+purity)?\s*\)?\s*$|"
    r"^\s*\(?\s*(?:[a-z]\)\s*)?silver\s*\)?\s*$|physical\s+gold|physical\s+silver|gold\s+bar|"
    r"silver\s+bar|gold\s+\d{3}\s+purity",
    re.IGNORECASE,
)
NOT_COMMODITY_RE = re.compile(r"sovereign\s+gold|gold\s+bond", re.IGNORECASE)
FUND_RE = re.compile(
    r"mutual\s+fund|units?\s+of\s+(?:an?\s+)?(?:alternative|aif)|exchange\s+traded\s+fund|\betf\b|"
    r"fund\s+of\s+funds|overseas\s+etfs?|international\s+selection\s+fund",
    re.IGNORECASE,
)
MONEY_MARKET_RE = re.compile(
    r"treasury\s+bills?|t[\s-]?bills?|commercial\s+paper|certificate\s+of\s+deposits?",
    re.IGNORECASE,
)
DEBT_RE = re.compile(
    r"government\s+securit|g[\s-]?sec|\bsdl\b|state\s+development|non[\s-]?convertible|\bbonds?\b|"
    r"\bdebenture|\bncd\b|\bfrn\b|securitised|corporate\s+debt|debt\s+instrument|zero\s+coupon|"
    r"floating\s+rate|perpetual|tier\s+[-i1]|\bgoi\b",
    re.IGNORECASE,
)
EQUITY_RE = re.compile(
    r"equity|listed\s*/?\s*awaiting|shares?\b|stock\s+exchange|preference|warrant|rights?\s+entitlement|"
    r"real\s+estate\s+investment|infrastructure\s+investment\s+trust|\breits?\b|\binvits?\b|overseas\s+securit",
    re.IGNORECASE,
)
RATING_RE = re.compile(
    r"^(?:(?:crisil|icra|care|india\s*ratings?|fitch|brickwork|acute|infomerics)[\s/\-]*)?"
    r"(sovereign|unrated|not\s*applicable|a1\+?|a2\+?|a3\+?|a4\+?|aaa|aa[+\-]?|a[+\-]?|bbb[+\-]?|bb[+\-]?|b[+\-]?|ccc|d)"
    r"(?:\s*/\s*[a-z0-9+\-]+)?$",
    re.IGNORECASE,
)
COUPON_NAME_RE = re.compile(r"\d+(?:\.\d+)?\s*%")


def text_or_null(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return None if not s or s.lower() in EMPTY_VALUES else s


def parse_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    s = str(value).strip()
    if not s or s.lower() in EMPTY_VALUES:
        return None
    s = re.sub(r"[%$,₹]", "", s)
    s = re.sub(r"\bRs\.?\b", "", s, flags=re.IGNORECASE).replace(",", "").strip()
    if not s or s.lower() in EMPTY_VALUES:
        return None
    try:
        n = float(s)
        return round(n, 6) if math.isfinite(n) else None
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", s)
        if m:
            try:
                n = float(m.group(0))
                return round(n, 6) if math.isfinite(n) else None
            except ValueError:
                return None
    return None


def looks_rating(value: Any) -> bool:
    s = text_or_null(value)
    return bool(RATING_RE.match(s)) if s else False


def classify_holding_type(h: Dict[str, Any]) -> str:
    name = str(h.get("instrument") or "")
    section = str(h.get("section") or "")
    industry = f"{h.get('industry') or ''} {h.get('industry_rating') or ''}"
    isin = str(h.get("isin") or "").strip().upper()
    blob = f"{name} {section} {industry}"

    if DERIV_RE.search(name) or DERIV_RE.search(section) or DERIV_RE.search(industry):
        return "derivative"
    if CASH_RE.search(name) or (CASH_RE.search(section) and not isin and not NOT_CASH_RE.search(name)):
        if not NOT_CASH_RE.search(name):
            return "cash"
    if isin.startswith("INF") or FUND_RE.search(section) or FUND_RE.search(name) or re.match(r"^LU[A-Z0-9]{10}$", isin):
        return "fund_unit"
    if (COMMODITY_RE.search(name) or re.match(r"^(?:[a-z]\)\s*)?(gold|silver)\b", section, re.IGNORECASE)) and not NOT_COMMODITY_RE.search(name):
        return "commodity"
    if MONEY_MARKET_RE.search(section) or MONEY_MARKET_RE.search(name):
        return "money_market"
    if COUPON_NAME_RE.search(name) or DEBT_RE.search(name) or isin.startswith(("IN0", "IN3")) or (DEBT_RE.search(section) and not EQUITY_RE.search(section)):
        return "debt"
    if re.search(r"physical\s+commodit|commodities\s+exchange", section, re.IGNORECASE) or re.search(r"commodity", name, re.IGNORECASE) or re.search(r"gold.*bar|silver.*bar|1\s*kg", name, re.IGNORECASE):
        return "commodity"
    if EQUITY_RE.search(section) or EQUITY_RE.search(name) or isin.startswith("INE") or re.match(r"^(US|GB|KY|TW|KR|JP|HK|MU|IE|CA|AU|CH|DE|FR|NL|BM|SG)", isin) or re.search(r"unlisted|privately\s+placed", section, re.IGNORECASE) or re.search(r"\b(?:ltd|limited|plc|inc|corp|holdings)\b", name, re.IGNORECASE):
        return "equity"
    if not isin and EQUITY_RE.search(blob):
        return "equity"
    return "other"


def resolve_industry_and_rating(h: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    industry = text_or_null(h.get("industry"))
    combined = text_or_null(h.get("industry_rating"))
    rating = text_or_null(h.get("rating"))

    if rating and industry:
        r = combined if (looks_rating(combined) and combined.lower() != industry.lower()) else rating
        return industry, r
    if rating:
        ind = industry or (None if looks_rating(combined) else combined)
        return ind, rating
    if combined and looks_rating(combined) and not industry:
        return None, combined
    if combined and not industry:
        return (None if looks_rating(combined) else combined), (combined if looks_rating(combined) else None)
    if combined and industry and combined.lower() != industry.lower():
        return industry, (combined if looks_rating(combined) else None)
    return industry, None


def shape_holding_row(h: Dict[str, Any]) -> Dict[str, Any]:
    industry, rating = resolve_industry_and_rating(h)
    qty = parse_number(h.get("quantity"))
    return {
        "holding_type": classify_holding_type(h),
        "instrument": text_or_null(h.get("instrument")) or "",
        "isin": text_or_null(h.get("isin")),
        "section": text_or_null(h.get("section")),
        "industry": industry,
        "rating": rating,
        "coupon": parse_number(h.get("coupon")),
        "maturity_date": text_or_null(h.get("maturity_date")),
        "quantity": int(qty) if qty is not None and qty.is_integer() else qty,
        "market_value": parse_number(h.get("market_value")),
        "pct_nav": parse_number(h.get("pct_nav")),
        "ytm": parse_number(h.get("ytm")),
        "ytc": parse_number(h.get("ytc")),
        "instrument_yield": parse_number(h.get("instrument_yield")),
        "listed_status": text_or_null(h.get("listed_status")),
        "underlying": text_or_null(h.get("underlying")),
        "position_side": text_or_null(h.get("position_side")),
    }


def compute_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with filepath.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def export_aws(bucket_name: str, period: str, cadence: str = "monthly", amc_filter: Optional[str] = None):
    s3 = boto3.client("s3")
    print(f"\n=== [AWS S3 Export] Period={period}, Bucket=s3://{bucket_name}/ ===")

    # 1. Export Raw Files
    raw_base = ROOT / "data" / "disclosures" / cadence
    raw_count = 0
    if raw_base.exists():
        for date_dir in [d for d in raw_base.iterdir() if d.is_dir() and period in d.name]:
            as_of = date_dir.name
            for amc_dir in date_dir.iterdir():
                if not amc_dir.is_dir() or (amc_filter and amc_dir.name != amc_filter):
                    continue
                for file_path in amc_dir.iterdir():
                    if not file_path.is_file() or file_path.name.startswith("."):
                        continue
                    sha256 = compute_sha256(file_path)
                    s3_key = f"fund_holdings/raw/{cadence}/{as_of}/{amc_dir.name}/{file_path.name}"
                    
                    s3.upload_file(
                        str(file_path),
                        bucket_name,
                        s3_key,
                        ExtraArgs={
                            "Metadata": {
                                "amc_id": amc_dir.name,
                                "as_of": as_of,
                                "cadence": cadence,
                                "filename": file_path.name,
                                "sha256": sha256,
                                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                            }
                        }
                    )
                    raw_count += 1
                    print(f"  [AWS S3 Raw] Uploaded: s3://{bucket_name}/{s3_key}")

    # 2. Export Normalized Parquet Files
    parsed_base = ROOT / "data" / "parsed" / cadence
    norm_count = 0
    total_holdings = 0
    if parsed_base.exists():
        for date_dir in [d for d in parsed_base.iterdir() if d.is_dir() and period in d.name]:
            for amc_dir in date_dir.iterdir():
                if not amc_dir.is_dir() or (amc_filter and amc_dir.name != amc_filter):
                    continue
                for scheme_dir in amc_dir.iterdir():
                    portfolio_file = scheme_dir / "portfolio.json"
                    if not portfolio_file.is_file():
                        continue
                    data = json.loads(portfolio_file.read_text(encoding="utf-8"))
                    meta = data.get("meta", {})
                    raw_holdings = data.get("holdings", [])
                    amfi_code = str(meta.get("amfi_code") or "").strip()
                    as_of = meta.get("as_of") or date_dir.name

                    if not amfi_code or not raw_holdings:
                        continue

                    shaped_holdings = [shape_holding_row(h) for h in raw_holdings]
                    df = pd.DataFrame(shaped_holdings)
                    df["amfi_code"] = amfi_code
                    df["amfi_name"] = meta.get("amfi_name") or meta.get("scheme_name")
                    df["amc_id"] = meta.get("amc_id") or amc_dir.name
                    df["as_of"] = as_of
                    df["source_file"] = meta.get("source_file")

                    buffer = io.BytesIO()
                    df.to_parquet(buffer, index=False, engine="pyarrow", compression="snappy")
                    buffer.seek(0)

                    s3_key = f"fund_holdings/normalized/as_of={as_of}/{amfi_code}.parquet"
                    s3.put_object(
                        Bucket=bucket_name,
                        Key=s3_key,
                        Body=buffer.getvalue(),
                        ContentType="application/octet-stream",
                        Metadata={
                            "amfi_code": amfi_code,
                            "amc_id": amc_dir.name,
                            "as_of": as_of,
                            "row_count": str(len(df)),
                            "exported_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    norm_count += 1
                    total_holdings += len(df)
                    print(f"  [AWS S3 Parquet] Uploaded: s3://{bucket_name}/{s3_key} ({len(df)} rows)")

    print(f"\n[AWS Result] Raw Files: {raw_count}, Parquet Schemes: {norm_count}, Total Holdings: {total_holdings}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Export to AWS S3")
    ap.add_argument("--bucket", required=True, help="Target S3 bucket name")
    ap.add_argument("--period", required=True, help="Period YYYY-MM or YYYY-MM-DD")
    ap.add_argument("--cadence", default="monthly", choices=["monthly", "fortnightly"])
    ap.add_argument("--amc", help="Optional AMC filter")
    args = ap.parse_args()

    export_aws(args.bucket, args.period, args.cadence, args.amc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
