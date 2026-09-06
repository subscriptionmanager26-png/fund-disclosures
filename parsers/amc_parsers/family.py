"""Family-based parser used for all AMCs via amc_parser_families.json."""
from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from .common import (
    SchemePortfolio,
    assert_zip_readable,
    disclosure_type_from_path,
    extract_as_of,
    extract_scheme_name_cams,
    extract_title_scheme,
    load_sheets,
    parse_holdings_table,
    period_from_path,
    safe_name,
)

SKIP_SHEET = re.compile(
    r"(?i)^(index|contents|cover|notes?|disclaimer|summary|risk.?o.?meter|"
    r"annexure|instruction|read.?me|legend|glossary|overview|derivatives?|"
    r"top\s*\d+\s*(stocks?|securities|sectors?|groups?))$"
)
SKIP_SHEET_CONTAINS = re.compile(
    r"(?i)risk.?o.?meter|disclaimer|notes?\s*to|important\s+information|sebi\s+circular|"
    r"exposure\s+to\s+top\s+\d+|top\s+\d+\s+(stocks?|securities|sectors?|groups?)"
)
MEGA_NAME = re.compile(
    r"(?i)all[-_\s]?schemes|consolidated|combined[-_\s]?portfolio|"
    r"debt-schemes-fortnightly-portfolio---as-on"
)
# Marketing / summary packs that are not SEBI scheme portfolios
# (Zerodha Top 10, Shriram issuer/sector/top-holdings sheets, etc.)
SKIP_FILE_RE = re.compile(
    r"(?i)top\s*\d+\s*holdings(?:\s+by\s+issuer)?|holdings\s+by\s+issuer|"
    r"issuer[-_\s]+sector[-_\s]+and[-_\s]+top[-_\s]+holdings|"
    r"portfolio[-_\s]?disclosure[-_\s]?issuer"
)


def _is_junk_sheet(name: str) -> bool:
    n = (name or "").strip()
    if not n or SKIP_SHEET.match(n):
        return True
    if SKIP_SHEET_CONTAINS.search(n) and not re.search(r"(?i)portfolio|holding|isin", n):
        return True
    return False


# AMC tickers like IFCF / RARBF / IBSESXF — not human sheet titles ("Flexi Cap").
_SHORTCODE_TAB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{1,19}$")


_GENERIC_SHEET_RE = re.compile(r"(?i)^sheet\d*$")


def _looks_like_shortcode(label: str | None) -> bool:
    s = (label or "").strip()
    if not s or " " in s or not _SHORTCODE_TAB_RE.fullmatch(s):
        return False
    # Excel defaults (Navi etc.) are not AMC tickers
    if _GENERIC_SHEET_RE.fullmatch(s):
        return False
    # Reject plain English words used as tabs (Liquid, Overnight, …)
    if s.isalpha() and not s.isupper() and len(s) >= 6:
        return False
    return True


_SHEET_LABEL_HINT_RE = re.compile(
    r"(?i)\b(fund|etf|fof|scheme|hybrid|cap|bond|duration|liquid|overnight|"
    r"arbitrage|equity|debt|gilt|gold|silver|momentum|allocation|savings|"
    r"market|children|retirement|innovation|advantage|cycle|consumption|"
    r"focused|value|multicap|midcap|largecap|flexi|aggressive|balanced|"
    r"dynamic|corporate|money|short|low|active|opportunities)\b"
)


def _looks_like_sheet_label(label: str | None) -> bool:
    """Sheet tabs used as shortcodes (Union: ``Aggressive Hybrid``, ``Largecap``)."""
    s = (label or "").strip()
    if not s or _GENERIC_SHEET_RE.fullmatch(s):
        return False
    if len(s) < 4 or len(s) > 80:
        return False
    if re.search(r"(?i)registration|portfolio\s+statement|registered\s+office", s):
        return False
    # Multi-word fund labels
    if " " in s:
        if _SHEET_LABEL_HINT_RE.search(s):
            return True
        return 2 <= len(s.split()) <= 6
    # Single-word Title Case tabs (Union Largecap / Midcap) — not ALLCAPS tickers
    if s.isalpha() and not s.isupper() and _SHEET_LABEL_HINT_RE.search(s):
        return True
    return False


def _shortcode_from_cell(value: str | None) -> str | None:
    """Whole-cell shortcode, or a leading ticker in titles like ``IB01-Groww …``."""
    s = (value or "").strip()
    if not s:
        return None
    if _looks_like_shortcode(s):
        return s.upper()
    m = re.match(r"^([A-Za-z0-9][A-Za-z0-9_\-]{1,19})\s*[-–—:]\s+\S", s)
    if m and _looks_like_shortcode(m.group(1)):
        return m.group(1).upper()
    m = re.match(r"^(IB\d+)\b", s, re.I)
    if m:
        return m.group(1).upper()
    return None


def _scheme_shortcode(sheet_name: str, rows: list[list[str]]) -> str | None:
    """Resolve scheme shortcode from sheet tab and header cells.

    Priority:
      1. Tab that already looks like an AMC ticker (``IFCF``, ``RLF``)
      2. Ticker in A1/B1 (Invesco: tab ``Flexi Cap``, A1 ``IFCF``)
      3. Human sheet label (Union: ``Aggressive Hybrid``) when no cell ticker
    """
    tab = (sheet_name or "").strip()
    if tab and not _GENERIC_SHEET_RE.fullmatch(tab) and _looks_like_shortcode(tab):
        return tab.upper()

    cell_code = None
    if rows and rows[0]:
        a1 = rows[0][0] if len(rows[0]) > 0 else None
        b1 = rows[0][1] if len(rows[0]) > 1 else None
        for cand in (_shortcode_from_cell(a1), _shortcode_from_cell(b1)):
            if cand:
                cell_code = cand
                break
    # Prefer explicit A1/B1 ticker over a human-readable sheet label.
    if cell_code:
        return cell_code

    if tab and not _GENERIC_SHEET_RE.fullmatch(tab) and _looks_like_sheet_label(tab):
        return tab
    return None


def _dedupe_by_shortcode(portfolios: list[SchemePortfolio]) -> list[SchemePortfolio]:
    """Keep one portfolio per shortcode, preferring the sheet with more holdings.

    Invesco monthly books ship a label sheet (full holdings, A1=ticker) plus a
    thin ticker-named sheet; after shortcode fix both resolve to the same code.
    """
    best: dict[str, SchemePortfolio] = {}
    passthrough: list[SchemePortfolio] = []
    for p in portfolios:
        key = (p.shortcode or "").strip().upper() or None
        if not key:
            passthrough.append(p)
            continue
        prev = best.get(key)
        if prev is None or len(p.holdings) > len(prev.holdings):
            best[key] = p
    return passthrough + list(best.values())


def _expand_zip(path: Path, dest: Path) -> list[Path]:
    assert_zip_readable(path)
    out: list[Path] = []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if name.startswith(".") or name.startswith("__"):
                continue
            if not re.search(r"\.(xlsx|xls|xlsm|xlsb)$", name, re.I):
                continue
            target = dest / safe_name(name)
            # avoid collisions
            if target.exists():
                target = dest / f"{safe_name(Path(name).stem)}_{len(out)}{Path(name).suffix.lower()}"
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            out.append(target)
    return out


def _workbook_candidates(path: Path, tmp: Path, *, skip_mega: bool, limit: int | None) -> list[Path]:
    if path.suffix.lower() == ".zip":
        files = _expand_zip(path, tmp)
    else:
        files = [path]
    files = [p for p in files if not SKIP_FILE_RE.search(p.name)]
    if skip_mega and len(files) > 1:
        filtered = [p for p in files if not MEGA_NAME.search(p.name)]
        if filtered:
            files = filtered
    files = sorted(files, key=lambda p: p.stat().st_size)
    if limit is not None and limit > 0:
        # diversify: smallest + mid
        if len(files) <= limit:
            return files
        picks = [files[0]]
        mid = files[len(files) // 2]
        if mid not in picks:
            picks.append(mid)
        for p in files:
            if len(picks) >= limit:
                break
            if p not in picks:
                picks.append(p)
        return picks[:limit]
    return files


def parse_file(
    path: Path,
    *,
    amc_id: str,
    family: str = "sebi_title",
    prefer_leading_code: bool = False,
    multi_sheet: bool = True,
    skip_mega: bool = True,
    workbook_limit: int | None = None,
) -> list[SchemePortfolio]:
    """Parse one disclosure file (xlsx/xls/zip) into scheme portfolios."""
    out: list[SchemePortfolio] = []
    dtype = disclosure_type_from_path(path)
    period = period_from_path(path)
    if SKIP_FILE_RE.search(path.name):
        return out
    if skip_mega and path.suffix.lower() != ".zip" and MEGA_NAME.search(path.name):
        # single mega workbook still parseable, but often noisy; keep unless alternatives exist
        pass

    with tempfile.TemporaryDirectory() as td:
        workbooks = _workbook_candidates(
            path, Path(td), skip_mega=skip_mega, limit=workbook_limit
        )
        for wb_path in workbooks:
            try:
                sheets = load_sheets(wb_path)
            except Exception:
                continue
            scheme_sheets = [(n, r) for n, r in sheets if not _is_junk_sheet(n)]
            # Single-scheme workbooks sometimes title their only tab "Index"
            # (e.g. ICICI Prudential Nifty 50 Index Fund.xlsx). That name is
            # normally a TOC skip — keep it when it is the only sheet left.
            if not scheme_sheets:
                index_only = [
                    (n, r)
                    for n, r in sheets
                    if r and re.fullmatch(r"(?i)index", (n or "").strip() or "")
                ]
                if len(sheets) == 1 and index_only:
                    scheme_sheets = index_only
                else:
                    continue
            if not scheme_sheets:
                continue
            # single-sheet packs: use the one sheet; multi: all scheme tabs
            for sheet_name, rows in scheme_sheets:
                if not rows:
                    continue
                if family == "cams":
                    scheme = extract_scheme_name_cams(rows) or extract_title_scheme(rows, sheet_name)
                    if re.search(r"(?i)^name\s+of\s+the\s", scheme or ""):
                        scheme = extract_title_scheme(rows, sheet_name) or wb_path.stem
                    sc = (sheet_name or "").strip()
                    if sc and sc.lower() not in {"sheet", "index", "contents", "cover", "notes"}:
                        shortcode = sc
                    else:
                        m = re.match(r"^([A-Z0-9]+)", wb_path.stem.upper())
                        shortcode = m.group(1) if m else None
                    code_pref = True if prefer_leading_code is None else prefer_leading_code
                    # cams almost always has leading codes
                    code_pref = True
                else:
                    scheme = extract_title_scheme(rows, sheet_name)
                    shortcode = _scheme_shortcode(sheet_name, rows)
                    code_pref = prefer_leading_code

                holdings, _meta = parse_holdings_table(rows, prefer_leading_code=code_pref)
                # skip empty junk tabs
                if not holdings and len(scheme_sheets) > 1:
                    continue
                # Single-scheme packs (e.g. HDFC) often leave only a shortcode on-sheet
                # while the filename has the real fund name — prefer the filename then.
                scheme_final = scheme or sheet_name or wb_path.stem
                stem_name = re.sub(
                    r"(?i)^(?:monthly|fortnightly)\s+",
                    "",
                    wb_path.stem or "",
                ).strip()
                stem_name = re.sub(
                    r"(?i)\s*[-_ ]+\d{1,2}(?:st|nd|rd|th)?\s+"
                    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
                    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
                    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{4}\s*$",
                    "",
                    stem_name,
                ).strip(" -_")
                if (
                    stem_name
                    and len(stem_name) > 8
                    and scheme_final
                    and (
                        scheme_final.upper() == (shortcode or "").upper()
                        or re.fullmatch(r"[A-Z0-9]{3,12}", scheme_final or "")
                    )
                ):
                    scheme_final = stem_name
                out.append(
                    SchemePortfolio(
                        amc_id=amc_id,
                        disclosure_type=dtype,
                        period=period,
                        scheme_name=scheme_final,
                        shortcode=shortcode,
                        as_of=extract_as_of(rows, filename=path.name)
                        or extract_as_of(rows, filename=wb_path.name),
                        source_file=path.name if path.suffix.lower() == ".zip" else wb_path.name,
                        sheet_name=sheet_name,
                        holdings=holdings,
                        notes=[f"from_zip:{wb_path.name}"] if path.suffix.lower() == ".zip" else [],
                    )
                )
                if not multi_sheet:
                    break
            # For zip fixture runs with workbook_limit, don't explode into every sheet of every file
            # unless multi_sheet; already handled per workbook.
    return _dedupe_by_shortcode(out)
