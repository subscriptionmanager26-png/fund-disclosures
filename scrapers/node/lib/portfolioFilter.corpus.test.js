/**
 * Regression corpus: every filename the 2026-11-13 production run rejected.
 *
 * The recurring monthly failure was real portfolios dropped because their
 * names had no parseable date (hash names, "October" without a year, "oct15",
 * "31 Oct, 2026"). Rule: a filename may be rejected only for a definite reason
 * (other month, other slice, non-portfolio, wrong cadence); anything undated
 * must be quarantined for the content-date check, never silently dropped.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { classifyDisclosureFile } from "./portfolioFilter.js";
import { isHtmlMasqueradingAsSpreadsheet } from "./download.js";

const here = dirname(fileURLToPath(import.meta.url));
const corpus = readFileSync(join(here, "fixtures/rejected-filenames-2026-11-13.txt"), "utf8")
  .split("\n")
  .filter(Boolean);
const oct = { year: 2026, month: 10 };
const JOBS = [
  ["fortnightly", "2026-10-15"],
  ["fortnightly", "2026-10-31"],
  ["monthly", "2026-10-31"],
];
const DEFINITE = new Set([
  "wrong_month",
  "wrong_as_of_slice",
  "excluded_non_portfolio",
  "wrong_cadence",
  "weekly_not_portfolio",
  "not_spreadsheet",
]);

test("corpus: no rejection is silent — undated files are quarantined", () => {
  assert.ok(corpus.length > 800);
  const silent = [];
  for (const name of corpus) {
    for (const [type, storageKey] of JOBS) {
      const v = classifyDisclosureFile({ filename: name }, { type, period: oct, storageKey });
      if (v.keep) continue;
      if (v.quarantine) continue;
      if (!DEFINITE.has(v.reason)) silent.push(`${type} ${storageKey} ${v.reason} ${name}`);
    }
  }
  assert.deepEqual(silent, []);
});

const cases = [
  // [label, file, type, storageKey, trustAdapterPeriod, expect]
  [
    "Shriram monthly under a CDN folder named …Fortnightly…",
    {
      filename: "Monthly-Portfolio-Shriram-Mutual-Fund-October-2026.xls",
      url: "https://cdn.shriramamc.in/uploads/Statutory-disclosure/Monthly--Fortnightly--Weekly-Portfolio-of-Scheme(s)/Monthly-Portfolio-for-the-Financial-Year/2026-2027/Monthly-Portfolio-Shriram-Mutual-Fund-October-2026.xls",
    },
    "monthly", "2026-10-31", false, "keep",
  ],
  [
    "comma date '31 Oct, 2026'",
    { filename: "Fortnightly Portfolio - 31 Oct, 2026.xlsx" },
    "fortnightly", "2026-10-31", false, "keep",
  ],
  [
    "Choice monthly 'October' without year (adapter is month-scoped)",
    { filename: "1794038260-Choice_Mutual_Fund_Monthly_Portfolio_Report_October_Nifty-Next-50.xlsx" },
    "monthly", "2026-10-31", true, "keep",
  ],
  [
    "Choice monthly without trust flag → quarantine, not drop",
    { filename: "1794038260-Choice_Mutual_Fund_Monthly_Portfolio_Report_October_Nifty-Next-50.xlsx" },
    "monthly", "2026-10-31", false, "quarantine",
  ],
  [
    "DSP 'oct15' without year → quarantine",
    { filename: "dsp-isin-portfolio-all-debt-oct15.xlsx" },
    "fortnightly", "2026-10-15", false, "quarantine",
  ],
  [
    "White Oak hashed per-scheme name (adapter month-scoped)",
    { filename: "White_Oak_Capital_Liquid_Fund_9087a08a2c.xlsx" },
    "monthly", "2026-10-31", true, "keep",
  ],
  [
    "Capitalmind hashed name on HTML hub → quarantine",
    { filename: "Capitalmind_Arbitrage_Fund_997720eb48.xlsx" },
    "monthly", "2026-10-31", false, "quarantine",
  ],
  [
    "previous-year file is still a definite wrong_month",
    { filename: "Fortnightly Portfolio- Oct 31, 2024.xlsx" },
    "fortnightly", "2026-10-31", false, "wrong_month",
  ],
  [
    "fortnightly pack never enters monthly, even when trusted",
    { filename: "Fortnightly_Portfolio_31_10_2026_b6be8ac722.xlsx" },
    "monthly", "2026-10-31", true, "wrong_cadence",
  ],
];

for (const [label, file, type, storageKey, trustAdapterPeriod, expect] of cases) {
  test(`case: ${label}`, () => {
    const v = classifyDisclosureFile(file, { type, period: oct, storageKey, trustAdapterPeriod });
    const got = v.keep ? "keep" : v.quarantine ? "quarantine" : v.reason;
    assert.equal(got, expect, JSON.stringify(v));
  });
}

test("download guard: WAF 'Request Rejected' page saved as .xlsx is not a spreadsheet", () => {
  const html = Buffer.from("<html>\n<head>\n<title>Request Rejected</title></head><body>The requested URL was rejected.</body></html>");
  assert.equal(isHtmlMasqueradingAsSpreadsheet("SBI_MONTHLY_PORTFOLIO_-_OCTOBER_2026.xlsx", html), true);
  assert.equal(isHtmlMasqueradingAsSpreadsheet("page.html", html), false);
  const zipMagic = Buffer.from([0x50, 0x4b, 0x03, 0x04, 0, 0, 0, 0]);
  assert.equal(isHtmlMasqueradingAsSpreadsheet("real.xlsx", zipMagic), false);
});
