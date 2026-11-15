"""Tests for content-date quarantine resolution (scrapers/python/resolve_quarantine.py)."""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
openpyxl = pytest.importorskip("openpyxl")

import resolve_quarantine as rq  # noqa: E402


def make_book(path: Path, banner: str | None, *, holdings_table: bool = True, title: str = "") -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Portfolio"
    if title:
        ws.append([title])
    ws.append(["XYZ Mutual Fund"])
    if banner:
        ws.append([banner])
    ws.append([])
    if holdings_table:
        ws.append(["Name of the Instrument", "ISIN", "Industry", "Quantity", "Market value (Rs. in Lakhs)", "% to Net Assets"])
        ws.append(["HDFC Bank Ltd", "INE040A01034", "Banks", "1000", "1700.5", "4.2"])
    else:
        ws.append(["Scheme", "1 Year", "3 Years", "Since inception"])
        ws.append(["Flexi Cap", "12.1", "15.3", "14.0"])
    wb.save(path)
    return path


def quarantine(root: Path, typ: str, key: str, amc: str) -> Path:
    d = root / "data" / "quarantine" / typ / key / amc
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_slice_for():
    assert rq.slice_for("2026-10-15") == "2026-10-15"
    assert rq.slice_for("2026-10-14") == "2026-10-15"
    assert rq.slice_for("2026-10-31") == "2026-10-31"
    assert rq.slice_for("2026-10-30") == "2026-10-31"
    assert rq.slice_for("2026-10-07") is None


def test_monthly_hash_named_book_is_promoted(tmp_path):
    q = quarantine(tmp_path, "monthly", "2026-10-31", "capitalmind-mutual-fund")
    make_book(q / "Capitalmind_Arbitrage_Fund_997720eb48.xlsx", "Monthly Portfolio Statement as on October 31, 2026")
    rep = rq.resolve(tmp_path, "monthly", "2026-10-31")
    row = rep["amcs"]["capitalmind-mutual-fund"][0]
    assert row["verdict"] == "promote", row
    assert (tmp_path / row["promoted_to"]).exists()
    assert not (q / "Capitalmind_Arbitrage_Fund_997720eb48.xlsx").exists()


def test_old_month_stays_quarantined(tmp_path):
    q = quarantine(tmp_path, "monthly", "2026-10-31", "unifi-mutual-fund")
    make_book(q / "Unifi-Dynamic-Asset-Allocation-Fund.xlsx", "Portfolio as on 30-Sep-2026")
    rep = rq.resolve(tmp_path, "monthly", "2026-10-31")
    row = rep["amcs"]["unifi-mutual-fund"][0]
    assert (row["verdict"], row["reason"]) == ("keep_quarantined", "as_of_other_period")
    assert row["as_of_found"] == ["2026-09-30"]


def test_fortnightly_mid_month_promoted(tmp_path):
    q = quarantine(tmp_path, "fortnightly", "2026-10-15", "dsp-mutual-fund")
    make_book(q / "dsp-isin-portfolio-all-debt-oct15.xlsx", "Portfolio as on 15-Oct-2026")
    rep = rq.resolve(tmp_path, "fortnightly", "2026-10-15")
    assert rep["amcs"]["dsp-mutual-fund"][0]["verdict"] == "promote"


def test_month_end_monthly_pack_not_promoted_into_fortnightly(tmp_path):
    q = quarantine(tmp_path, "fortnightly", "2026-10-31", "x-mutual-fund")
    make_book(q / "a1b2c3.xlsx", "Portfolio Statement as on 31-Oct-2026")
    rep = rq.resolve(tmp_path, "fortnightly", "2026-10-31")
    assert rep["amcs"]["x-mutual-fund"][0]["reason"] == "month_end_without_fortnight_marker"


def test_month_end_fortnightly_with_marker_promoted(tmp_path):
    q = quarantine(tmp_path, "fortnightly", "2026-10-31", "x-mutual-fund")
    make_book(q / "a1b2c3.xlsx", "Portfolio as on 31-Oct-2026", title="Fortnightly Portfolio Statement")
    rep = rq.resolve(tmp_path, "fortnightly", "2026-10-31")
    assert rep["amcs"]["x-mutual-fund"][0]["verdict"] == "promote"


def test_non_portfolio_sheet_not_promoted(tmp_path):
    q = quarantine(tmp_path, "monthly", "2026-10-31", "unifi-mutual-fund")
    make_book(q / "Scheme_Performance_Flexicap_Oct26.xlsx", "Performance as on 31-Oct-2026", holdings_table=False)
    rep = rq.resolve(tmp_path, "monthly", "2026-10-31")
    assert rep["amcs"]["unifi-mutual-fund"][0]["reason"] == "not_a_portfolio_workbook"


def test_zip_pack_promoted(tmp_path):
    q = quarantine(tmp_path, "monthly", "2026-10-31", "unifi-mutual-fund")
    inner = make_book(tmp_path / "inner.xlsx", "Portfolio as on 31/10/2026")
    with zipfile.ZipFile(q / "month_end_portfolio_disclosure.zip", "w") as zf:
        zf.write(inner, "Unifi Liquid Fund.xlsx")
    rep = rq.resolve(tmp_path, "monthly", "2026-10-31")
    assert rep["amcs"]["unifi-mutual-fund"][0]["verdict"] == "promote"


def test_html_block_page_is_unreadable_not_promoted(tmp_path):
    q = quarantine(tmp_path, "monthly", "2026-10-31", "sbi-mutual-fund")
    (q / "SBI_MONTHLY_PORTFOLIO.xlsx").write_text("<html><head><title>Request Rejected</title></head></html>")
    rep = rq.resolve(tmp_path, "monthly", "2026-10-31")
    assert rep["amcs"]["sbi-mutual-fund"][0]["reason"] == "unreadable"


def test_cli_writes_probe(tmp_path, monkeypatch):
    q = quarantine(tmp_path, "monthly", "2026-10-31", "a")
    make_book(q / "x.xlsx", "Portfolio as on 31-Oct-2026")
    monkeypatch.setattr(sys, "argv", ["rq", "--type=monthly", "--period=2026-10-31", f"--root={tmp_path}"])
    rq.main()
    probe = json.loads((tmp_path / "data/probes/quarantine-monthly-2026-10-31.json").read_text())
    assert probe["promoted"] == 1


def test_lic_month_only_fortnightly_goes_to_15th_not_month_end(tmp_path):
    """LIC 'Fortnightly_Portfolio_Oct-26.xlsx' is the 15-Oct book."""
    for key, want in (("2026-10-15", "promote"), ("2026-10-31", "keep_quarantined")):
        q = quarantine(tmp_path, "fortnightly", key, "lic-mutual-fund")
        make_book(q / "LIC_MF_Fortnightly_Portfolio_Oct-26.xlsx", "Portfolio as on 15-Oct-2026")
        rep = rq.resolve(tmp_path, "fortnightly", key)
        assert rep["amcs"]["lic-mutual-fund"][0]["verdict"] == want, rep
