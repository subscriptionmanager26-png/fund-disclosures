#!/usr/bin/env python3
"""Build a scheme-first catalog: AMC/parent are filters, AMFI plan is the row.

Holdings live on the disclosure/parent fund. Every mapped child plan inherits
that book via data/sources/disclosure_to_amfi_global_mapping.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "holdings-browser" / "public" / "catalog.json"
LOOKUP_OUT = ROOT / "holdings-browser" / "api" / "amfi-lookup.json"
MAP_PATH = ROOT / "data" / "sources" / "disclosure_to_amfi_global_mapping.json"
SHORT_PATH = ROOT / "registry" / "disclosure_shortcode_map.json"
ALIAS_PATH = ROOT / "registry" / "amfi_holdings_aliases.json"

DATE_TAIL = re.compile(
    r"(?i)[_\s\-]+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\s*\d{1,2},?\s*\d{4}\s*$"
)
ISIN_RE = re.compile(r"^INF[A-Z0-9]{9}$")
NUMERIC_NAV_RE = re.compile(r"^\d+(?:\.\d+)?$")


def norm(s: str) -> str:
    t = re.sub(r"\s+", " ", (s or "").lower()).strip()
    t = t.replace("&", " and ")
    return t


def name_key(s: str) -> str:
    t = re.sub(r"\(.*?\)", " ", s or "", flags=re.S)
    return norm(t)


def compact(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s or "").upper()


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
        head = re.match(r"^([A-Za-z0-9]+)[_\s]", raw.strip())
        if head:
            candidates.append(head.group(1))
        for c in candidates:
            if c and c not in seen:
                seen.add(c)
                out.append(c)
    return out


def slim_holdings(rec: dict) -> dict:
    return {
        "as_of": rec.get("as_of"),
        "holding_count": rec.get("holding_count"),
        "shortcode": rec.get("map_shortcode") or rec.get("shortcode"),
        "local_path": rec.get("local_path"),
        "b2_key": rec.get("b2_key"),
        "source_file": rec.get("source_file"),
    }


def validate_lookup_nav_fields(lookup: dict[str, dict]) -> None:
    errors: list[str] = []
    for code, row in lookup.items():
        nav = row.get("nav")
        isin = row.get("isin")
        if nav and ISIN_RE.match(str(nav).upper()):
            errors.append(f"{code}: nav={nav}")
        if isin and not ISIN_RE.match(str(isin).upper()):
            low = str(isin).lower()
            if "plan" in low or "growth" in low or "idcw" in low:
                errors.append(f"{code}: isin={isin}")
        if nav and not NUMERIC_NAV_RE.match(str(nav).replace(",", "")) and not ISIN_RE.match(
            str(nav).upper()
        ):
            errors.append(f"{code}: nav={nav}")
    if errors:
        sample = "\n".join(f"  - {e}" for e in errors[:10])
        raise SystemExit(
            f"Catalog NAV/ISIN sanity check failed ({len(errors)} schemes). "
            f"Re-run `npm run amfi:asof` after fixing NAV history parsing.\n{sample}"
        )


def main() -> int:
    # Prefer the largest dated AMFI snapshot (partial regenerations can leave a
    # smaller newer file that would shrink the catalog).
    asof_candidates = sorted(ROOT.glob("data/amfi/schemes_asof_*.json"))
    schemes_path = ROOT / "data/amfi/schemes_asof_2026-07-31.json"
    funds_path = ROOT / "data/amfi/funds_asof_2026-07-31.json"
    best_n = -1
    for sp in asof_candidates:
        try:
            n = len(json.loads(sp.read_text()))
        except Exception:
            continue
        if n > best_n:
            best_n = n
            schemes_path = sp
            funds_path = ROOT / "data/amfi" / sp.name.replace("schemes_asof_", "funds_asof_")
    if not funds_path.exists():
        funds_path = ROOT / "data/amfi/funds_asof_2026-07-31.json"
        schemes_path = ROOT / "data/amfi/schemes_asof_2026-07-31.json"
    funds = json.loads(funds_path.read_text())
    schemes = json.loads(schemes_path.read_text())
    print(f"catalog AMFI snapshot: {funds_path.name} ({len(funds)} funds) + {schemes_path.name} ({len(schemes)} schemes)")
    amcs_reg = json.loads((ROOT / "registry/amcs.json").read_text())["amcs"]
    manifest = json.loads((ROOT / "data/parsed/b2_holdings_manifest.json").read_text())
    global_map = json.loads(MAP_PATH.read_text())
    short_map = json.loads(SHORT_PATH.read_text())

    name_to_id = {}
    id_to_name = {}
    for a in amcs_reg:
        name_to_id[norm(a.get("name") or "")] = a["id"]
        if a.get("amc_name"):
            name_to_id.setdefault(norm(a["amc_name"]), a["id"])
        id_to_name[a["id"]] = a.get("name")
    for h in manifest["schemes"]:
        if h.get("amc_id") and h.get("amc_name"):
            name_to_id.setdefault(norm(h["amc_name"]), h["amc_id"])
            id_to_name.setdefault(h["amc_id"], h["amc_name"])

    by_code = {str(s["amfi_code"]): s for s in schemes}

    # disclosure mapping: shortcode / name / plan code → plan codes + parent
    by_short: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    by_plan: dict[str, dict] = {}
    for row in global_map.get("mappings") or []:
        if not row.get("mapped"):
            continue
        amc_id = name_to_id.get(norm(row.get("amc_name") or ""))
        plans = [str(c) for c in (row.get("fund_amfi_plan_codes") or []) if c]
        canonical = str(row.get("catalog_canonical_amfi_code") or "").strip()
        payload = {
            "amc_id": amc_id,
            "amc_name": row.get("amc_name"),
            "parent_name": row.get("parent_fund_name") or row.get("disclosure_fund_name"),
            "parent_amfi": canonical or (plans[0] if plans else None),
            "plan_codes": plans,
            "map_shortcode": row.get("disclosure_fund_shortname"),
        }
        shorts = list(row.get("disclosure_fund_shortnames") or [])
        if row.get("disclosure_fund_shortname"):
            shorts.append(row["disclosure_fund_shortname"])
        for sc in shorts:
            for key in (sc, compact(sc)):
                if amc_id and key:
                    by_short.setdefault(f"{amc_id}::{key}", payload)
                    by_short.setdefault(f"{amc_id}::{key.casefold()}", payload)
        for nm in (row.get("disclosure_fund_name"), row.get("parent_fund_name")):
            nk = name_key(nm or "")
            if amc_id and nk:
                by_name.setdefault(f"{amc_id}::{nk}", payload)
        for c in plans + ([canonical] if canonical else []):
            by_plan.setdefault(c, payload)

    for row in short_map.get("entries") or []:
        amc_id = (row.get("amc_id") or "").strip()
        raw = (row.get("shortcode") or "").strip()
        amfi = str(row.get("canonical_amfi_code") or "").strip()
        if not amc_id or not raw or not amfi:
            continue
        mapped = by_plan.get(amfi) or {
            "amc_id": amc_id,
            "amc_name": id_to_name.get(amc_id),
            "parent_name": row.get("amfi_base_name") or row.get("disclosure_label"),
            "parent_amfi": amfi,
            "plan_codes": [amfi],
            "map_shortcode": raw,
        }
        for key in (raw, raw.casefold(), compact(raw)):
            if key:
                by_short.setdefault(f"{amc_id}::{key}", mapped)

    holdings_recs = []
    for h in manifest["schemes"]:
        amc_id = h.get("amc_id")
        rec = {
            "amc_id": amc_id,
            "amc_name": h.get("amc_name") or id_to_name.get(amc_id or ""),
            "scheme_id": str(h.get("scheme_id") or ""),
            "amfi_code": str(h.get("amfi_code") or "").strip() or None,
            "parent_name": h.get("amfi_name") or h.get("scheme_name"),
            "shortcode": h.get("shortcode"),
            "as_of": h.get("as_of"),
            "holding_count": h.get("holding_count"),
            "local_path": h.get("local_path"),
            "b2_key": h.get("b2_key"),
            "source_file": h.get("source_file"),
            "scheme_name": h.get("scheme_name"),
            "folder": h.get("folder"),
        }
        mapped = None
        if rec["amfi_code"]:
            mapped = by_plan.get(rec["amfi_code"])
        if not mapped and amc_id:
            for label in peel_labels(rec.get("shortcode"), rec.get("folder"), rec.get("scheme_id")):
                mapped = (
                    by_short.get(f"{amc_id}::{label}")
                    or by_short.get(f"{amc_id}::{label.casefold()}")
                    or by_short.get(f"{amc_id}::{compact(label)}")
                )
                if mapped:
                    break
            if not mapped:
                mapped = by_name.get(f"{amc_id}::{name_key(rec.get('scheme_name') or '')}")
                if not mapped:
                    mapped = by_name.get(f"{amc_id}::{name_key(rec.get('parent_name') or '')}")
        rec["mapped"] = mapped
        if mapped:
            rec["map_shortcode"] = mapped.get("map_shortcode")
            rec["parent_name"] = mapped.get("parent_name") or rec["parent_name"]
        holdings_recs.append(rec)

    holdings_by_plan: dict[str, dict] = {}
    used = set()
    for rec in holdings_recs:
        mapped = rec.get("mapped")
        codes = []
        if mapped:
            codes = list(mapped.get("plan_codes") or [])
            if mapped.get("parent_amfi"):
                codes.append(mapped["parent_amfi"])
        if rec.get("amfi_code"):
            codes.append(rec["amfi_code"])
        attached = False
        for c in dict.fromkeys(codes):
            holdings_by_plan.setdefault(c, rec)
            attached = True
        if attached:
            used.add(id(rec))

    # Orphan AMFI share-class "funds" (Institutional / Div. / Cash) inherit
    # the parent portfolio when AMFI lists them as separate base funds.
    if ALIAS_PATH.exists():
        for orphan, parent in (
            json.loads(ALIAS_PATH.read_text(encoding="utf-8")).get("aliases") or {}
        ).items():
            orphan_s, parent_s = str(orphan), str(parent)
            if parent_s in holdings_by_plan and orphan_s not in holdings_by_plan:
                holdings_by_plan[orphan_s] = holdings_by_plan[parent_s]

    parent_of_plan: dict[str, dict] = {}
    for f in funds:
        amc_name = f["amc_name"]
        amc_id = name_to_id.get(norm(amc_name))
        codes = [str(c) for c in (f.get("amfi_codes") or [])]
        pid = str(f.get("canonical_amfi_code") or (codes[0] if codes else ""))
        hold = None
        for c in [pid] + codes:
            if c in holdings_by_plan:
                hold = holdings_by_plan[c]
                amc_id = amc_id or hold.get("amc_id")
                break
        info = {
            "amc_id": amc_id,
            "amc_name": (hold or {}).get("amc_name") or amc_name,
            "parent_name": f["base_name"],
            "parent_amfi": pid,
            "holdings": hold,
        }
        for c in codes:
            parent_of_plan[c] = info
        if pid:
            parent_of_plan.setdefault(pid, info)

    for rec in holdings_recs:
        mapped = rec.get("mapped") or {}
        for c in mapped.get("plan_codes") or []:
            parent_of_plan.setdefault(
                c,
                {
                    "amc_id": rec.get("amc_id") or mapped.get("amc_id"),
                    "amc_name": rec.get("amc_name") or mapped.get("amc_name"),
                    "parent_name": mapped.get("parent_name") or rec.get("parent_name"),
                    "parent_amfi": mapped.get("parent_amfi"),
                    "holdings": rec,
                },
            )

    rows = []
    seen_scheme = set()
    for s in schemes:
        code = str(s["amfi_code"])
        info = parent_of_plan.get(code) or {}
        hold = info.get("holdings") or holdings_by_plan.get(code)
        amc_name = info.get("amc_name") or s.get("amc_name")
        amc_id = info.get("amc_id") or name_to_id.get(norm(amc_name or ""))
        row = {
            "amfi_code": code,
            "name": s.get("name") or code,
            "amc_id": amc_id,
            "amc_name": amc_name,
            "parent_name": info.get("parent_name") or s.get("name"),
            "parent_amfi": info.get("parent_amfi"),
            "nav": s.get("nav"),
            "nav_date": s.get("nav_date"),
            "isin": s.get("isin_growth_or_payout"),
            "category": s.get("category"),
            "has_holdings": bool(hold),
        }
        if hold:
            row["holdings"] = slim_holdings(hold)
        rows.append(row)
        seen_scheme.add(code)

    for rec in holdings_recs:
        if id(rec) in used:
            continue
        key = rec.get("scheme_id") or rec.get("b2_key")
        if not key or key in seen_scheme:
            continue
        seen_scheme.add(key)
        rows.append(
            {
                "amfi_code": rec.get("amfi_code"),
                "name": rec.get("scheme_name") or rec.get("parent_name") or key,
                "amc_id": rec.get("amc_id"),
                "amc_name": rec.get("amc_name"),
                "parent_name": rec.get("parent_name"),
                "parent_amfi": rec.get("amfi_code"),
                "nav": None,
                "nav_date": None,
                "isin": None,
                "category": None,
                "has_holdings": True,
                "holdings": slim_holdings(rec),
            }
        )

    amc_rows = {}
    for r in rows:
        aid = r.get("amc_id") or norm(r.get("amc_name") or "unknown")
        row = amc_rows.setdefault(
            aid,
            {
                "id": r.get("amc_id") or aid,
                "name": r.get("amc_name") or aid,
                "scheme_count": 0,
                "with_holdings": 0,
            },
        )
        row["scheme_count"] += 1
        if r.get("has_holdings"):
            row["with_holdings"] += 1
        if r.get("amc_name"):
            row["name"] = r["amc_name"]

    catalog = {
        "generated_from": {
            "funds": "data/amfi/funds_asof_2026-07-31.json",
            "schemes": "data/amfi/schemes_asof_2026-07-31.json",
            "holdings": "data/parsed/b2_holdings_manifest.json",
            "mapping": "data/sources/disclosure_to_amfi_global_mapping.json",
        },
        "amcs": sorted(amc_rows.values(), key=lambda x: x["name"].lower()),
        "schemes": rows,
    }
    lookup = {}
    for r in rows:
        code = str(r.get("amfi_code") or "").strip()
        if not code:
            continue
        hold = r.get("holdings") or {}
        lookup[code] = {
            "amfi_code": code,
            "name": r.get("name"),
            "amc_id": r.get("amc_id"),
            "amc_name": r.get("amc_name"),
            "parent_name": r.get("parent_name"),
            "parent_amfi": r.get("parent_amfi"),
            "portfolio_id": r.get("parent_amfi") if r.get("has_holdings") and r.get("parent_amfi") else None,
            "nav": r.get("nav"),
            "nav_date": r.get("nav_date"),
            "isin": r.get("isin"),
            "category": r.get("category"),
            "has_holdings": bool(r.get("has_holdings") and hold.get("b2_key")),
            "as_of": hold.get("as_of"),
            "holding_count": hold.get("holding_count"),
            "shortcode": hold.get("shortcode"),
            "b2_key": hold.get("b2_key"),
            "source_file": hold.get("source_file"),
            "local_path": hold.get("local_path"),
        }

    validate_lookup_nav_fields(lookup)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(catalog, ensure_ascii=False) + "\n", encoding="utf-8")
    LOOKUP_OUT.parent.mkdir(parents=True, exist_ok=True)
    LOOKUP_OUT.write_text(json.dumps(lookup, ensure_ascii=False) + "\n", encoding="utf-8")
    hit_153738 = lookup.get("153738") or {}
    print(
        json.dumps(
            {
                "amcs": len(catalog["amcs"]),
                "schemes": len(rows),
                "with_holdings": sum(1 for r in rows if r["has_holdings"]),
                "lookup": len(lookup),
                "153738": {
                    "name": hit_153738.get("name"),
                    "has_holdings": hit_153738.get("has_holdings"),
                    "shortcode": hit_153738.get("shortcode"),
                    "b2_key": hit_153738.get("b2_key"),
                },
                "out": str(OUT.relative_to(ROOT)),
                "lookup_out": str(LOOKUP_OUT.relative_to(ROOT)),
                "mb": round(OUT.stat().st_size / 1e6, 2),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
