import test from "node:test";
import assert from "node:assert/strict";
import { classifyDisclosureFile } from "./portfolioFilter.js";

const sepPeriod = { year: 2026, month: 9 };

test("rejects Shriram 2019 half-yearly HY portfolio from Sep slices", () => {
  const file = {
    filename: "ShriramMF-HYPortfolio-Reg-59A-Sep19.xlsx",
    url: "https://cdn.example/ShriramMF-HYPortfolio-Reg-59A-Sep19.xlsx",
  };
  for (const key of ["2026-09-15", "2026-09-30"]) {
    const v = classifyDisclosureFile(file, {
      type: "fortnightly",
      period: sepPeriod,
      storageKey: key,
    });
    assert.equal(v.keep, false);
    assert.match(v.reason, /excluded_non_portfolio|undated_no_month/);
  }
});

test("rejects Capitalmind PRC matrix (not a portfolio)", () => {
  const file = {
    filename: "PRC_matrix_for_debt_scheme_of_Capitalmind_mutual_fund_07ac55c3bd.xlsx",
    url: "https://capitalmindmf.com/PRC_matrix_for_debt_scheme_of_Capitalmind_mutual_fund_07ac55c3bd.xlsx",
  };
  const v = classifyDisclosureFile(file, {
    type: "fortnightly",
    period: sepPeriod,
    storageKey: "2026-09-15",
  });
  assert.equal(v.keep, false);
  assert.equal(v.reason, "excluded_non_portfolio");
});

test("keeps dated fortnightly portfolio for matching slice", () => {
  const file = {
    filename: "CMLIQ_Fortnightly_Portfolio_Disclosure_July_15_2026.xlsx",
    url: "https://example/CMLIQ_Fortnightly_Portfolio_Disclosure_July_15_2026.xlsx",
  };
  const v = classifyDisclosureFile(file, {
    type: "fortnightly",
    period: { year: 2026, month: 7 },
    storageKey: "2026-07-15",
  });
  assert.equal(v.keep, true);
});

test("rejects undated spreadsheet when as-of storage key is set", () => {
  const file = {
    filename: "Capitalmind_Arbitrage_Fund_997720eb48.xlsx",
    url: "https://example/Capitalmind_Arbitrage_Fund_997720eb48.xlsx",
  };
  const v = classifyDisclosureFile(file, {
    type: "monthly",
    period: sepPeriod,
    storageKey: "2026-09-30",
  });
  assert.equal(v.keep, false);
  assert.equal(v.reason, "undated_no_month");
});

test("trust_adapter_period keeps API-filtered undated per-scheme files", () => {
  const file = {
    filename: "large-cap28cefe07eee8616aaa28ff00007d74af.xlsx",
    url: "https://cdn.example/large-cap28cefe07eee8616aaa28ff00007d74af.xlsx",
  };
  const v = classifyDisclosureFile(file, {
    type: "monthly",
    period: { year: 2026, month: 8 },
    storageKey: "2026-08-31",
    trustAdapterPeriod: true,
  });
  assert.equal(v.keep, true);
  assert.equal(v.reason, "adapter_period");
});

test("keeps Kotak consolidated SEBI monthly (SEBI in path is not regulatory junk)", () => {
  const file = {
    filename: "ConsolidatedSEBIPortfolioJuly2026.xlsx",
    url:
      "https://vatseelabs-s3.kotakmf.com/FAD/Portfolios/Consolidated-SEBI-Portfolio-as-on-July-31,-2026/ConsolidatedSEBIPortfolioJuly2026.xlsx",
  };
  const v = classifyDisclosureFile(file, {
    type: "monthly",
    period: { year: 2026, month: 7 },
    storageKey: "2026-07-31",
  });
  assert.equal(v.keep, true);
  assert.match(v.reason, /as_of_slice|month_in_name/);
});

test("LIC monthly path month beats upload timestamp in filename", () => {
  const file = {
    filename: "LEFE3009-09-2026-09_58_30.xlsx",
    url: "https://www.licmf.com/assets/downloads/portfolio/monthly/2026/8/LEFE3009-09-2026-09_58_30.xlsx",
  };
  const v = classifyDisclosureFile(file, {
    type: "monthly",
    period: { year: 2026, month: 8 },
    storageKey: "2026-08-31",
  });
  assert.equal(v.keep, true);
  assert.match(v.reason, /as_of_slice|month_in_name/);
});
