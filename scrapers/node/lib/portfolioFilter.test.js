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
