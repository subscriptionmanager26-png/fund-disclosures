import test from "node:test";
import assert from "node:assert/strict";
import {
  datesMatchAsOf,
  extractDisclosureDates,
  fileMatchesAsOfStrict,
} from "./asofFileFilter.js";
import { parsePeriod, periodMatchers } from "./period.js";
import { isPeriodPortfolioFile } from "./portfolioFilter.js";

test("Nippon abbreviated month and two-digit year", () => {
  const href = "NIMF-FORTNIGHTLY-PORTFOLIO-15-Aug-26.xls";
  const [d] = extractDisclosureDates("Debt Schemes Portfolio as on 15th Aug 2026", href);
  assert.equal(d.year, 2026);
  assert.equal(d.month, 8);
  assert.equal(d.day, 15);
  assert.equal(
    fileMatchesAsOfStrict({ filename: href, url: href }, "2026-08-15"),
    true,
  );
});

test("Edelweiss glued day-month-year (31Aug2026) maps to month-end slice", () => {
  const name = "EDEL_Fortnightly_Disclosure_31Aug2026_04092026104330.xlsx";
  const [d] = extractDisclosureDates(name);
  assert.equal(d.year, 2026);
  assert.equal(d.month, 8);
  assert.equal(d.day, 31);
  assert.equal(datesMatchAsOf(name, "2026-08-31"), true);
  assert.equal(datesMatchAsOf(name, "2026-08-15"), false);
  assert.equal(fileMatchesAsOfStrict({ filename: name }, "2026-08-31"), true);
  assert.equal(fileMatchesAsOfStrict({ filename: name }, "2026-08-15"), false);
});

test("Edelweiss glued 15Aug2026 maps to mid-month slice", () => {
  const name = "EDEL_Fortnightly_Disclosure_15Aug2026_20082026095502.xlsx";
  assert.equal(datesMatchAsOf(name, "2026-08-15"), true);
  assert.equal(datesMatchAsOf(name, "2026-08-31"), false);
});

test("Franklin 14-Aug belongs on the 15th slice, not month-end", () => {
  const file = {
    filename: "Fortnightly-Portfolio-ISIN-14-Aug-2026.xlsx",
    url: "/en-in/fortnight-portfolio-debt-schemes/Fortnightly-Portfolio-ISIN-14-Aug-2026.xlsx",
  };
  assert.equal(fileMatchesAsOfStrict(file, "2026-08-15"), true);
  assert.equal(fileMatchesAsOfStrict(file, "2026-08-31"), false);
  assert.equal(datesMatchAsOf(file.filename, "2026-08-15"), true);
});

test("15-July full month name still matches mid-month", () => {
  const file = { filename: "Fortnightly-Portfolio-ISIN-15-July-2026.xlsx" };
  assert.equal(fileMatchesAsOfStrict(file, "2026-07-15"), true);
  assert.equal(fileMatchesAsOfStrict(file, "2026-07-31"), false);
});

test("Choice OV_15 Aug and Helios 15th-august match mid-month", () => {
  assert.equal(
    fileMatchesAsOfStrict(
      { filename: "Choice-Mutual-Fund_Fortnightly-Portfolio_OV_15-Aug-2026.xlsx" },
      "2026-08-15",
    ),
    true,
  );
  assert.equal(
    fileMatchesAsOfStrict(
      {
        filename:
          "helios-overnight-fund-fortnightly-portfolio-as-on-15th-august-2026.xls",
      },
      "2026-08-15",
    ),
    true,
  );
});

test("HTML period filter keeps Helios 14th/15th August overnight", () => {
  const p = parsePeriod("2026-08");
  const matchers = periodMatchers(p);
  const url =
    "https://www.heliosmf.in/wp-content/uploads/helios-overnight-fund-fortnightly-portfolio-as-on-15th-august-2026.xls";
  assert.equal(isPeriodPortfolioFile(url, "", matchers, "fortnightly", p), true);
  const fourteen =
    "https://www.heliosmf.in/wp-content/uploads/helios-overnight-fund-fortnightly-portfolio-as-on-14th-august-2026.xls";
  assert.equal(
    isPeriodPortfolioFile(fourteen, "", matchers, "fortnightly", p),
    true,
  );
});

test("spreadsheet with a month is enough; no portfolio/fortnightly tag required", () => {
  const p = parsePeriod("2026-08");
  const matchers = periodMatchers(p);
  assert.equal(
    isPeriodPortfolioFile(
      "https://www.heliosmf.in/wp-content/uploads/Helios-OV-15th-August-2026.xls",
      "",
      matchers,
      "fortnightly",
      p,
    ),
    true,
  );
  assert.equal(
    fileMatchesAsOfStrict({ filename: "Helios-OV-August-2026.xls" }, "2026-08-15"),
    true,
  );
  assert.equal(
    isPeriodPortfolioFile(
      "https://example.com/aaum-august-2026.xlsx",
      "",
      matchers,
      "fortnightly",
      p,
    ),
    false,
  );
});
