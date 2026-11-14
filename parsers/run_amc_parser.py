#!/usr/bin/env python3
"""Run AMC-wise portfolio parsers.

Examples:
  .venv/bin/python3 parsers/run_amc_parser.py --all-fixtures
  .venv/bin/python3 parsers/run_amc_parser.py --amc=nj-mutual-fund --fixtures
  .venv/bin/python3 parsers/run_amc_parser.py --amc=sbi-mutual-fund --type=monthly --period=latest --limit=2
  .venv/bin/python3 parsers/run_amc_parser.py --amc=mirae-asset-mutual-fund --type=monthly --period=2026-07
  .venv/bin/python3 parsers/run_amc_parser.py --amc=mirae-asset-mutual-fund --type=monthly --period=2026-07 --force
  .venv/bin/python3 parsers/run_amc_parser.py --list
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))  # compat shim
sys.path.insert(0, str(ROOT / "parsers"))  # canonical parsers win
sys.path.insert(0, str(ROOT / "scrapers" / "python" / "lib"))

from asof_filter import (  # noqa: E402
    canonical_as_of_for_folder,
    filter_paths_for_storage_key,
)

from amc_parsers.common import (  # noqa: E402
    write_amc_schemes_index,
    write_scheme_portfolio,
)
from amc_parsers.disclosure_period import (  # noqa: E402
    disclosure_search_keys,
)
from amc_parsers.parse_progress import (  # noqa: E402
    filter_paths_for_resume,
    load_existing_portfolios,
)
from amc_parsers.registry import get_parser, list_amcs  # noqa: E402

_FIXTURES_REG = ROOT / "registry" / "parser_fixtures.json"
_FIXTURES_OLD = ROOT / "data" / "sources" / "parser_fixtures.json"
FIXTURES = _FIXTURES_REG if _FIXTURES_REG.exists() else _FIXTURES_OLD


def load_fixtures() -> dict:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def iter_amc_files(amc_id: str, disclosure_type: str, period: str) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for key in disclosure_search_keys(period, disclosure_type):
        d = ROOT / "data" / "disclosures" / disclosure_type / key / amc_id
        if not d.is_dir():
            continue
        batch: list[Path] = []
        for p in sorted(d.iterdir()):
            if not p.is_file():
                continue
            if p.suffix.lower() not in {".xlsx", ".xls", ".xlsm", ".xlsb", ".zip"}:
                continue
            if p.name in seen:
                continue
            seen.add(p.name)
            batch.append(p)
        if key == period and len(key) == 10 and key[4] == "-" and key[7] == "-":
            batch = filter_paths_for_storage_key(batch, key, disclosure_type)
        files.extend(batch)
    return files


def _default_out_base(amc_id: str, disclosure_type: str, period: str) -> Path:
    return ROOT / "data" / "parsed" / disclosure_type / period / amc_id


def _infer_out_context(paths: list[Path], amc_id: str) -> tuple[str, str, Path]:
    """Best-effort disclosure_type / period / out dir from input paths."""
    if not paths:
        return "monthly", "unknown", _default_out_base(amc_id, "monthly", "unknown")
    parts = paths[0].parts
    dtype, period = "monthly", "unknown"
    for i, p in enumerate(parts):
        if p in {"monthly", "fortnightly"} and i + 1 < len(parts):
            dtype = p
            period = parts[i + 1]
            break
    return dtype, period, _default_out_base(amc_id, dtype, period)


def _call_parser(parser, path: Path, *, workbook_limit: int | None):
    kwargs = {}
    if workbook_limit is not None:
        try:
            sig = inspect.signature(parser)
            if "workbook_limit" in sig.parameters:
                kwargs["workbook_limit"] = workbook_limit
        except (TypeError, ValueError):
            # functools.partial — dig into keywords / wrapped
            if getattr(parser, "keywords", None) is not None:
                kwargs["workbook_limit"] = workbook_limit
    try:
        return parser(path, **kwargs)
    except TypeError:
        return parser(path)


def _flush_schemes_index(
    amc_id: str,
    portfolios: list,
    *,
    out_base: Path | None = None,
) -> None:
    """Persist schemes.json for whatever portfolios we have so far.

    Portfolios are written per file; historically the index was only written at
    the end of parse_paths. A kill/timeout mid-AMC (common for Mirae’s ~100
    single-scheme workbooks) left portfolio.json folders with no schemes.json,
    and enrich skipped the whole AMC. Flush after each file + in finally.
    """
    if not portfolios:
        return
    by_key: dict[tuple[str, str], list] = {}
    for p in portfolios:
        by_key.setdefault((p.disclosure_type, p.period), []).append(p)
    for (dtype, period), items in by_key.items():
        dest_root = out_base or (ROOT / "data" / "parsed" / dtype / period / amc_id)
        write_amc_schemes_index(dest_root, items)


def parse_paths(
    amc_id: str,
    paths: list[Path],
    *,
    out_base: Path | None = None,
    workbook_limit: int | None = None,
    resume: bool = True,
) -> dict:
    parser = get_parser(amc_id)
    dtype, period, inferred_out = _infer_out_context(paths, amc_id)
    dest_root = out_base or inferred_out

    skipped: list[str] = []
    to_parse = list(paths)
    existing: list = []
    if resume and paths:
        to_parse, done = filter_paths_for_resume(paths, dest_root)
        skipped = [p.name for p in done]
        if done:
            existing = load_existing_portfolios(
                dest_root,
                amc_id=amc_id,
                disclosure_type=dtype,
                period=period,
                only_sources={p.name for p in done},
            )

    all_portfolios = list(existing)
    errors = []
    parsed_files = 0
    try:
        for path in to_parse:
            try:
                portfolios = _call_parser(parser, path, workbook_limit=workbook_limit)
            except Exception as e:
                errors.append({"file": path.name, "error": str(e)})
                continue
            parsed_files += 1
            all_portfolios.extend(portfolios)
            for p in portfolios:
                folder_period = period if period and len(period) == 10 else p.period
                p.as_of = canonical_as_of_for_folder(
                    p.as_of, folder_period, p.disclosure_type
                )
                write_root = out_base or (
                    ROOT / "data" / "parsed" / p.disclosure_type / p.period / p.amc_id
                )
                try:
                    write_scheme_portfolio(write_root, p)
                except Exception as e:
                    errors.append(
                        {
                            "file": path.name,
                            "scheme": getattr(p, "shortcode", None) or getattr(p, "scheme_name", None),
                            "error": f"write_scheme_portfolio: {e}",
                        }
                    )
            # Keep schemes.json in sync after every file so a mid-run interrupt
            # cannot orphan portfolio folders without an enrichable index.
            _flush_schemes_index(amc_id, all_portfolios, out_base=out_base)
    finally:
        _flush_schemes_index(amc_id, all_portfolios, out_base=out_base)

    return {
        "amc_id": amc_id,
        "files": len(paths),
        "parsed_files": parsed_files,
        "skipped_resume": len(skipped),
        "skipped_files": skipped[:20],
        "schemes": len(all_portfolios),
        "holdings": sum(len(p.holdings) for p in all_portfolios),
        "errors": errors,
        "schemes_detail": [
            {
                "scheme": p.scheme_name,
                "shortcode": p.shortcode,
                "as_of": p.as_of,
                "holdings": len(p.holdings),
                "source_file": p.source_file,
                "type": p.disclosure_type,
                "period": p.period,
            }
            for p in all_portfolios
        ],
    }


def _empty_report(amc_id: str, *, errors: list | None = None) -> dict:
    return {
        "amc_id": amc_id,
        "files": 0,
        "parsed_files": 0,
        "skipped_resume": 0,
        "skipped_files": [],
        "schemes": 0,
        "holdings": 0,
        "errors": errors or [],
        "schemes_detail": [],
    }


def _parse_amc_job(job: tuple) -> dict:
    """One AMC for --all (top-level so ProcessPoolExecutor can pickle it)."""
    amc_id, dtype, period, limit, resume = job
    paths = iter_amc_files(amc_id, dtype, period)
    if limit:
        paths = paths[:limit]
    if not paths:
        return _empty_report(amc_id)
    return parse_paths(amc_id, paths, resume=resume)


def _amc_input_bytes(amc_id: str, dtype: str, period: str) -> int:
    try:
        return sum(p.stat().st_size for p in iter_amc_files(amc_id, dtype, period))
    except Exception:
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--amc", help="AMC id (e.g. nj-mutual-fund)")
    ap.add_argument("--all", action="store_true", help="Parse every registered AMC for --type/--period")
    ap.add_argument("--type", choices=["monthly", "fortnightly"], help="Disclosure cadence")
    ap.add_argument("--period", default="latest", help="YYYY-MM, YYYY-MM-DD, or latest")
    ap.add_argument("--limit", type=int, default=0, help="Max files to parse (0=all)")
    ap.add_argument("--file", action="append", default=[], help="Explicit file path(s)")
    ap.add_argument("--fixtures", action="store_true", help="Parse fixture files for --amc")
    ap.add_argument("--all-fixtures", action="store_true", help="Parse fixtures for all AMCs")
    ap.add_argument("--list", action="store_true", help="List registered AMC parsers")
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-parse every file (disable resume). Default resumes complete files.",
    )
    ap.add_argument(
        "--no-resume",
        action="store_true",
        help=argparse.SUPPRESS,  # alias for --force
    )
    ap.add_argument(
        "--only",
        default="",
        help="With --all: comma-separated AMC ids to parse (others skipped). "
        "The daily cloud job passes only AMCs whose source files changed.",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("PARSE_WORKERS", "1") or 1),
        help="With --all: parse this many AMCs in parallel processes (default 1, or $PARSE_WORKERS). "
        "Safe because each AMC writes only under data/parsed/{type}/{period}/{amc}/.",
    )
    ap.add_argument(
        "--summary-only",
        action="store_true",
        help="Print compact per-AMC summary (used with --all-fixtures)",
    )
    args = ap.parse_args()
    resume = not (args.force or args.no_resume)

    if args.list:
        amcs = list_amcs()
        print(json.dumps({"count": len(amcs), "amcs": amcs}, indent=2))
        return 0

    reports = []
    if args.all_fixtures or args.fixtures:
        fixtures = load_fixtures()
        amc_ids = list(fixtures.keys()) if args.all_fixtures else [args.amc]
        if not args.all_fixtures and not args.amc:
            raise SystemExit("--fixtures requires --amc")
        for amc_id in amc_ids:
            if amc_id not in fixtures:
                raise SystemExit(f"No fixtures for {amc_id}")
            paths = []
            for cadence, entries in fixtures[amc_id].items():
                if cadence == "notes" or not isinstance(entries, list):
                    continue
                for rel in entries:
                    p = ROOT / rel
                    if not p.exists():
                        print(f"missing fixture: {p}", file=sys.stderr)
                        continue
                    paths.append(p)
            # Fixture mode: always fresh parse; limit zip/mega expansion
            reports.append(
                parse_paths(amc_id, paths, workbook_limit=2, resume=False)
            )
    else:
        if args.all:
            if not args.type:
                raise SystemExit("--type required with --all")
            only = {s.strip() for s in args.only.split(",") if s.strip()}
            amc_ids = [a for a in list_amcs() if not only or a in only]
            if only - set(list_amcs()):
                print(f"--only: no parser for {sorted(only - set(list_amcs()))}", file=sys.stderr)
            jobs = [(a, args.type, args.period, args.limit, resume) for a in amc_ids]
            workers = max(1, min(args.workers, len(jobs) or 1))
            if workers == 1:
                reports.extend(_parse_amc_job(j) for j in jobs)
            else:
                from concurrent.futures import ProcessPoolExecutor

                # Largest AMCs first so one big pack does not finish last alone.
                order = sorted(
                    range(len(jobs)),
                    key=lambda i: -_amc_input_bytes(jobs[i][0], args.type, args.period),
                )
                results: dict[int, dict] = {}
                with ProcessPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(_parse_amc_job, jobs[i]): i for i in order}
                    for fut, i in futures.items():
                        try:
                            results[i] = fut.result()
                        except Exception as e:  # worker crash: report, keep others
                            results[i] = _empty_report(jobs[i][0], errors=[{"error": f"worker: {e}"}])
                reports.extend(results[i] for i in range(len(jobs)))
        elif not args.amc:
            raise SystemExit("Need --amc or --all (or --all-fixtures / --list)")
        else:
            if args.file:
                paths = [Path(f) for f in args.file]
            else:
                if not args.type:
                    raise SystemExit("--type required unless --fixtures/--file")
                paths = iter_amc_files(args.amc, args.type, args.period)
                if args.limit:
                    paths = paths[: args.limit]
            reports.append(parse_paths(args.amc, paths, resume=resume))

    if args.summary_only:
        summary = []
        for r in reports:
            summary.append(
                {
                    "amc_id": r["amc_id"],
                    "files": r["files"],
                    "parsed_files": r.get("parsed_files", r["files"]),
                    "skipped_resume": r.get("skipped_resume", 0),
                    "schemes": r["schemes"],
                    "holdings": r["holdings"],
                    "errors": len(r["errors"]),
                    "error_samples": r["errors"][:2],
                }
            )
        ok = sum(1 for s in summary if s["schemes"] > 0 and s["errors"] == 0)
        weak = sum(1 for s in summary if s["schemes"] == 0 or s["errors"])
        print(
            json.dumps(
                {
                    "amcs": len(summary),
                    "ok": ok,
                    "weak_or_empty": weak,
                    "total_schemes": sum(s["schemes"] for s in summary),
                    "total_holdings": sum(s["holdings"] for s in summary),
                    "rows": summary,
                },
                indent=2,
            )
        )
    else:
        print(json.dumps(reports if len(reports) > 1 else reports[0], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
