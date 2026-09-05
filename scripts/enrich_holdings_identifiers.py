#!/usr/bin/env python3
"""Attach scheme identifiers to parsed portfolios and write a B2 upload manifest.

Looks up AMFI parent codes from registry/disclosure_shortcode_map.json.
Keeps the newest as_of per (amc_id, identity) across the given parsed roots.

By default refuses to enrich when disclosure files are missing from schemes.json
(see scripts/check_parse_completeness.py). Use --allow-incomplete to override.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "parsers"))

from amc_parsers.disclosure_period import discover_period_dirs  # noqa: E402
from amc_parsers.parse_progress import check_period_completeness  # noqa: E402

MAP_PATH = ROOT / "registry" / "disclosure_shortcode_map.json"
AMC_PATH = ROOT / "registry" / "amcs.json"
OUT_MANIFEST = ROOT / "data" / "parsed" / "b2_holdings_manifest.json"
DISC_ROOT = ROOT / "data" / "disclosures"
PARSED_ROOT = ROOT / "data" / "parsed"

JUNK_FOLDER = re.compile(
    r"(?i)^(common notes|contents|cover|notes|disclaimer|risk.?o.?meter)$"
)


def normalize_shortcode(label: str | None) -> str | None:
    s = re.sub(r"[^A-Za-z0-9]", "", (label or "").strip())
    return s.upper() if s else None


def load_amc_names() -> dict[str, dict]:
    data = json.loads(AMC_PATH.read_text(encoding="utf-8"))
    out = {}
    for row in data.get("amcs") or []:
        out[row["id"]] = {
            "amc_name": row.get("name") or row.get("amc_name"),
            "amfi_mf_id": row.get("amfi_mf_id"),
        }
    return out


def name_key(s: str | None) -> str:
    if not s:
        return ""
    t = re.sub(r"\(.*?\)", " ", s, flags=re.S)
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _register_map_keys(
    out: dict[str, dict],
    by_name: dict[str, dict],
    amc_id: str,
    payload: dict,
    *labels: str | None,
) -> None:
    for label in labels:
        if not label:
            continue
        label = label.strip()
        if not label:
            continue
        keys = {label, label.casefold()}
        compact = normalize_shortcode(label)
        if compact:
            keys.add(compact)
        for k in keys:
            out.setdefault(f"{amc_id}::{k}", payload)
        nk = name_key(label)
        if nk:
            by_name.setdefault(f"{amc_id}::{nk}", payload)


def load_shortcode_map() -> tuple[dict[str, dict], dict[str, dict]]:
    data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for row in data.get("entries") or []:
        amc_id = (row.get("amc_id") or "").strip()
        raw = (row.get("shortcode") or "").strip()
        amfi = str(row.get("canonical_amfi_code") or "").strip()
        if not amc_id or not raw or not amfi:
            continue
        payload = {
            "amfi_code": amfi,
            "amfi_name": row.get("amfi_base_name") or row.get("disclosure_label"),
            "map_shortcode": raw,
            "confidence": row.get("confidence"),
        }
        _register_map_keys(out, by_name, amc_id, payload, raw)
        for alias in row.get("aliases") or []:
            _register_map_keys(out, by_name, amc_id, payload, alias)
        _register_map_keys(
            out,
            by_name,
            amc_id,
            payload,
            row.get("disclosure_label"),
            row.get("amfi_base_name"),
        )
    return out, by_name


DATE_TAIL = re.compile(
    r"(?i)[_\s\-]+(?:"
    r"(?:\d{1,2}(?:st|nd|rd|th)?\s+)?"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+\d{1,2}(?:st|nd|rd|th)?)?,?\s*\d{4}"
    r")\s*$"
)


def peel_labels(*labels: str | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in labels:
        if not raw:
            continue
        candidates = [raw.strip()]
        peeled = DATE_TAIL.sub("", raw).strip(" _-,")
        if peeled:
            candidates.append(peeled)
        for c in candidates:
            if c and c not in seen:
                seen.add(c)
                out.append(c)
    return out


def resolve_amfi(
    amap: dict[str, dict],
    by_name: dict[str, dict],
    amc_id: str,
    *labels: str | None,
) -> dict | None:
    for label in peel_labels(*labels):
        for key in (label, label.casefold(), normalize_shortcode(label)):
            if not key:
                continue
            hit = amap.get(f"{amc_id}::{key}")
            if hit:
                return hit
        nk = name_key(label)
        if nk:
            hit = by_name.get(f"{amc_id}::{nk}")
            if hit:
                return hit
    return None


def safe_slug(s: str) -> str:
    out = re.sub(r"[^\w.\-() ]+", "_", (s or "").strip())
    out = re.sub(r"\s+", " ", out).strip(" ._")
    return out[:180] or "unknown"


def iter_schemes(parsed_root: Path):
    if not parsed_root.is_dir():
        return
    for amc_dir in sorted(p for p in parsed_root.iterdir() if p.is_dir()):
        idx = amc_dir / "schemes.json"
        if not idx.exists():
            continue
        items = json.loads(idx.read_text(encoding="utf-8"))
        for s in items:
            folder = s.get("folder") or safe_slug(s.get("shortcode") or s.get("scheme") or "")
            pj = amc_dir / folder / "portfolio.json"
            if not pj.exists():
                continue
            yield amc_dir.name, s, pj


def _period_roots() -> list[tuple[str, str, Path]]:
    """(disclosure_type, period, parsed_root) tuples enrich scans."""
    out: list[tuple[str, str, Path]] = []
    for dtype in ("monthly", "fortnightly"):
        for period in discover_period_dirs(PARSED_ROOT, dtype):
            parsed_root = PARSED_ROOT / dtype / period
            if parsed_root.is_dir():
                out.append((dtype, period, parsed_root))
    latest = PARSED_ROOT / "monthly" / "latest"
    if latest.is_dir():
        out.append(("monthly", "latest", latest))
    fortnightly_latest = PARSED_ROOT / "fortnightly" / "latest"
    if fortnightly_latest.is_dir():
        out.append(("fortnightly", "latest", fortnightly_latest))
    return out


def _assert_parse_complete(*, allow_incomplete: bool) -> int:
    """Exit 1 (unless allow_incomplete) when any scanned period has parse gaps."""
    failures = []
    for dtype, period, parsed_root in _period_roots():
        if not parsed_root.exists():
            continue
        disc_period = DISC_ROOT / dtype / period
        if not disc_period.is_dir():
            continue
        report = check_period_completeness(
            disclosure_type=dtype,
            period=period,
            disc_root=DISC_ROOT,
            parsed_root=PARSED_ROOT,
        )
        if report["complete"]:
            continue
        failures.append(
            {
                "disclosure_type": dtype,
                "period": period,
                "incomplete_amcs": report["incomplete_amcs"],
                "incomplete": report["incomplete"][:10],
            }
        )
    if not failures:
        return 0
    print(
        json.dumps(
            {
                "error": "parse_incomplete",
                "hint": (
                    "Re-run parsers/run_amc_parser.py for the AMC(s) "
                    "(resume is on by default), then enrich again. "
                    "Override with --allow-incomplete."
                ),
                "failures": failures,
            },
            indent=2,
        ),
        file=sys.stderr,
    )
    return 0 if allow_incomplete else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Enrich even when disclosure files are missing from schemes.json",
    )
    ap.add_argument(
        "--skip-completeness-check",
        action="store_true",
        help="Do not run parse completeness gate at all",
    )
    args = ap.parse_args()

    if not args.skip_completeness_check:
        rc = _assert_parse_complete(allow_incomplete=args.allow_incomplete)
        if rc != 0:
            return rc

    # Refuse enrich if pinned shortcode/alias locks drifted (HDINCF, SILVRFOF, …).
    lock_cp = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "assert_holdings_mapping_locks.py")],
        cwd=ROOT,
    )
    if lock_cp.returncode != 0:
        return lock_cp.returncode

    roots = [parsed_root for _, _, parsed_root in _period_roots()]
    amap, by_name = load_shortcode_map()
    amcs = load_amc_names()
    best: dict[tuple[str, str], dict] = {}

    for parsed_root in roots:
        if not parsed_root.exists():
            continue
        parts = parsed_root.parts
        dtype = "monthly" if "monthly" in parts else "fortnightly"
        period = parsed_root.name
        for amc_id, s, pj in iter_schemes(parsed_root):
            folder = s.get("folder") or ""
            if JUNK_FOLDER.match(folder) or JUNK_FOLDER.match(s.get("shortcode") or ""):
                continue
            payload = json.loads(pj.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                continue
            meta = payload.get("meta") or {}
            as_of = meta.get("as_of") or s.get("as_of")
            shortcode = meta.get("shortcode") or s.get("shortcode")
            scheme_name = meta.get("scheme_name") or s.get("scheme")
            identity = (
                normalize_shortcode(shortcode)
                or (scheme_name or "").casefold().strip()
                or folder.casefold()
            )
            key = (amc_id, identity)
            amfi = resolve_amfi(
                amap, by_name, amc_id, shortcode, scheme_name, folder, s.get("scheme")
            )
            amc_info = amcs.get(amc_id) or {}
            scheme_id = (amfi or {}).get("amfi_code") or shortcode or folder
            ident = {
                "scheme_id": str(scheme_id),
                "amc_id": amc_id,
                "amc_name": amc_info.get("amc_name"),
                "amfi_mf_id": amc_info.get("amfi_mf_id"),
                "amfi_code": (amfi or {}).get("amfi_code"),
                "amfi_name": (amfi or {}).get("amfi_name"),
                "shortcode": shortcode,
                "scheme_name": scheme_name,
                "as_of": as_of,
                "disclosure_type": meta.get("disclosure_type") or dtype,
                "period": meta.get("period") or period,
                "source_file": meta.get("source_file") or s.get("source_file"),
                "sheet_name": meta.get("sheet_name") or s.get("sheet_name"),
                "folder": folder,
                "holding_count": len(payload.get("holdings") or []),
                "map_confidence": (amfi or {}).get("confidence"),
            }
            meta.update({k: v for k, v in ident.items() if v is not None})
            payload["meta"] = meta
            # Always stamp this period's file so historical as-of sync can resolve
            # AMFI ids even when a newer book wins the "best" contest.
            pj.parent.mkdir(parents=True, exist_ok=True)
            pj.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            prev = best.get(key)
            if prev:
                prev_as = prev.get("as_of") or ""
                new_as = as_of or ""
                if new_as < prev_as:
                    continue
                if new_as == prev_as and prev.get("disclosure_type") == "monthly" and dtype != "monthly":
                    continue
                if new_as == prev_as and prev.get("period") != "latest" and period == "latest":
                    continue
            best[key] = {
                **ident,
                "local_path": str(pj.relative_to(ROOT)),
                "b2_key": (
                    f"fund-disclosures/holdings/latest/{amc_id}/{safe_slug(str(scheme_id))}/portfolio.json"
                ),
                "payload": payload,
            }

    rows = []
    mapped = 0
    for rec in best.values():
        payload = rec.pop("payload")
        local = ROOT / rec["local_path"]
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if rec.get("amfi_code"):
            mapped += 1
        rows.append(rec)

    rows.sort(key=lambda r: (r["amc_id"], r.get("scheme_name") or ""))
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scheme_count": len(rows),
        "with_amfi_code": mapped,
        "without_amfi_code": len(rows) - mapped,
        "b2_prefix": "fund-disclosures/holdings/latest/",
        "schemes": rows,
    }
    OUT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    OUT_MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "schemes": len(rows),
                "with_amfi_code": mapped,
                "without_amfi_code": len(rows) - mapped,
                "manifest": str(OUT_MANIFEST.relative_to(ROOT)),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
