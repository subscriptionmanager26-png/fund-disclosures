#!/usr/bin/env python3
"""Download rejected disclosure files and inspect workbook content for false rejects."""
from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
PROBES = ROOT / "data" / "probes"
OUT = ROOT / "data" / "probes" / "rejected-files-audit.json"

AS_OF_RE = re.compile(
    r"(?i)(?:as\s+on|as\s+of|portfolio\s+as\s+on|month\s+ended|fortnight(?:ly)?\s+ended)"
    r"[\s:,-]*"
    r"(\d{1,2})[\s./-]+([A-Za-z]{3,9}|\d{1,2})[\s./-]+(\d{2,4})"
)
MONTH_IN_NAME = re.compile(
    r"(?i)(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)"
)
PORTFOLIO_HINT = re.compile(
    r"(?i)\b(portfolio|holdings?|%?\s*nav|market\s*value|isin|quantity|issuer)\b"
)
NON_PORTFOLIO_HINT = re.compile(
    r"(?i)\b(aaum|complaint|prc\s*matrix|half[\s-]?year|reg[\s-]?59|dashboard|overlap)\b"
)
FORTNIGHTLY_HINT = re.compile(r"(?i)fortnight")
MONTHLY_HINT = re.compile(r"(?i)\bmonthly\b")


def ssl_ctx():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def load_rejections() -> list[dict]:
    rows: list[dict] = []
    for fp in sorted(PROBES.glob("fetch-rejections-*.json")):
        data = json.loads(fp.read_text())
        job_type = data.get("type", "")
        storage_key = data.get("storageKey", "")
        for r in data.get("rejected", []):
            rows.append(
                {
                    **r,
                    "job_type": job_type,
                    "job_storage_key": storage_key,
                    "probe_file": fp.name,
                }
            )
    return rows


def dedupe(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        key = r.get("url") or r.get("filename") or ""
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def download(url: str, dest: Path, timeout: int = 120) -> tuple[bool, str]:
    if url.startswith("file://"):
        src = Path(unquote(urlparse(url).path))
        if not src.is_file():
            return False, "missing_local"
        dest.write_bytes(src.read_bytes())
        return True, "local"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as resp:
            body = resp.read()
        if len(body) < 200:
            return False, "too_small"
        dest.write_bytes(body)
        return True, "ok"
    except Exception as e:
        return False, str(e)[:200]


def read_workbook_snippet(path: Path, max_rows: int = 40) -> str:
    ext = path.suffix.lower()
    chunks: list[str] = []
    try:
        if ext in {".xlsx", ".xlsm"}:
            from openpyxl import load_workbook

            wb = load_workbook(path, read_only=True, data_only=True)
            for sheet in wb.worksheets[:3]:
                chunks.append(f"[sheet:{sheet.title}]")
                for i, row in enumerate(sheet.iter_rows(values_only=True)):
                    if i >= max_rows:
                        break
                    cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                    if cells:
                        chunks.append(" | ".join(cells[:12]))
            wb.close()
        elif ext == ".xls":
            import xlrd

            book = xlrd.open_workbook(path)
            for si in range(min(3, book.nsheets)):
                sh = book.sheet_by_index(si)
                chunks.append(f"[sheet:{sh.name}]")
                for ri in range(min(max_rows, sh.nrows)):
                    cells = [
                        str(sh.cell_value(ri, ci)).strip()
                        for ci in range(min(12, sh.ncols))
                        if str(sh.cell_value(ri, ci)).strip()
                    ]
                    if cells:
                        chunks.append(" | ".join(cells))
        elif ext == ".zip":
            import zipfile

            with zipfile.ZipFile(path) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith((".xlsx", ".xls"))]
                chunks.append(f"[zip:{len(names)} spreadsheets]")
                if names:
                    inner = ROOT / ".tmp" / "rej-audit" / path.stem
                    inner.mkdir(parents=True, exist_ok=True)
                    inner_path = inner / Path(names[0]).name
                    inner_path.write_bytes(zf.read(names[0]))
                    chunks.append(read_workbook_snippet(inner_path, max_rows=25))
        else:
            return path.read_bytes()[:500].decode("utf-8", errors="ignore")
    except Exception as e:
        return f"READ_ERROR: {e}"
    return "\n".join(chunks)


def infer_content_signals(text: str, filename: str) -> dict:
    blob = f"{filename}\n{text}"
    as_ofs = AS_OF_RE.findall(blob)
    return {
        "looks_like_portfolio": bool(PORTFOLIO_HINT.search(blob)),
        "looks_non_portfolio": bool(NON_PORTFOLIO_HINT.search(blob)),
        "fortnightly_in_content": bool(FORTNIGHTLY_HINT.search(blob)),
        "monthly_in_content": bool(MONTHLY_HINT.search(blob)),
        "month_tokens_in_name": MONTH_IN_NAME.findall(filename),
        "as_of_snippets": ["/".join(m) for m in as_ofs[:5]],
    }


def assess(row: dict, signals: dict, downloaded: bool, dl_note: str) -> dict:
    reason = row.get("reason", "")
    job_type = row.get("job_type", "")
    storage = row.get("job_storage_key", "")
    filename = row.get("filename", "")

    verdict = "correct_reject"
    notes: list[str] = []

    if not downloaded:
        verdict = "unverified"
        notes.append(f"download_failed:{dl_note}")
        return {"verdict": verdict, "notes": notes, "signals": signals}

    if signals.get("looks_non_portfolio"):
        notes.append("content_matches_exclusion_pattern")
    elif not signals.get("looks_like_portfolio"):
        notes.append("no_portfolio_keywords_in_content")

    if reason == "excluded_non_portfolio":
        if signals.get("looks_like_portfolio") and not signals.get("looks_non_portfolio"):
            verdict = "possible_false_reject"
            notes.append("looks_like_portfolio_despite_exclusion")
    elif reason == "wrong_cadence":
        pass  # filename guard — content secondary
    elif reason == "wrong_as_of_slice":
        # If content as-of matches storage key, filter may have mis-parsed filename
        detail = row.get("detail", "")
        if storage and detail and detail != storage:
            notes.append(f"filter_saw_{detail}_wanted_{storage}")
    elif reason == "wrong_month":
        pass
    elif reason == "undated_no_month":
        # API-scoped adapters: undated hash names may still be valid for the job month
        if job_type == "monthly" and signals.get("looks_like_portfolio"):
            if not signals.get("looks_non_portfolio"):
                # Only flag if job month plausibly matches content month token
                job_ym = storage[:7] if storage else ""
                month_map = {
                    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
                    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
                }
                tokens = [t.lower()[:3] for t in signals.get("month_tokens_in_name", [])]
                if any(month_map.get(t[:3], "") == job_ym[5:7] for t in tokens if t):
                    verdict = "possible_false_reject"
                    notes.append("undated_but_month_token_matches_job")
                elif signals.get("as_of_snippets"):
                    notes.append(f"as_of_in_sheet:{signals['as_of_snippets'][:2]}")
    elif reason == "undated_no_month" and job_type == "fortnightly":
        if signals.get("looks_like_portfolio") and signals.get("as_of_snippets"):
            notes.append("fortnightly_undated_but_dated_inside")

    # Explicit fortnightly file on monthly job
    if job_type == "monthly" and FORTNIGHTLY_HINT.search(filename):
        verdict = "correct_reject"
        notes.append("fortnightly_filename_on_monthly_job")

    return {"verdict": verdict, "notes": notes, "signals": signals}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="Max unique files to inspect (0=all)")
    ap.add_argument("--only-verdict", default="", help="Filter output verdict")
    args = ap.parse_args()

    rows = dedupe(load_rejections())
    if not rows:
        print("No rejection probes found under data/probes/fetch-rejections-*.json", file=sys.stderr)
        return 1

    if args.limit:
        rows = rows[: args.limit]

    cache = ROOT / ".tmp" / "rej-audit" / "files"
    cache.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    by_verdict: dict[str, int] = defaultdict(int)
    by_reason: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for i, row in enumerate(rows, 1):
        url = row.get("url", "")
        filename = row.get("filename") or Path(unquote(urlparse(url).path)).name or f"file_{i}"
        safe = re.sub(r"[^\w.\-]+", "_", filename)[:180]
        dest = cache / safe
        ok, dl_note = download(url, dest) if url else (False, "no_url")
        snippet = read_workbook_snippet(dest) if ok else ""
        signals = infer_content_signals(snippet, filename)
        assessment = assess(row, signals, ok, dl_note)

        rec = {
            "amc_id": row.get("amc_id"),
            "filename": filename,
            "url": url,
            "reason": row.get("reason"),
            "detail": row.get("detail"),
            "job_type": row.get("job_type"),
            "job_storage_key": row.get("job_storage_key"),
            "downloaded": ok,
            "download_note": dl_note,
            **assessment,
        }
        results.append(rec)
        by_verdict[assessment["verdict"]] += 1
        by_reason[row.get("reason", "?")][assessment["verdict"]] += 1
        if (i % 25 == 0) or i == len(rows):
            print(f"  inspected {i}/{len(rows)} …", flush=True)

    summary = {
        "total_unique_rejected": len(results),
        "by_verdict": dict(by_verdict),
        "by_reason": {k: dict(v) for k, v in by_reason.items()},
        "possible_false_rejects": [
            r for r in results if r["verdict"] == "possible_false_reject"
        ],
        "unverified": [r for r in results if r["verdict"] == "unverified"],
    }
    OUT.write_text(json.dumps({"summary": summary, "results": results}, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
