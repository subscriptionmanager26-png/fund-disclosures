import test from "node:test";
import assert from "node:assert/strict";
import {
  parsePeriodInput,
  disclosureStorageKey,
  disclosureStorageKeys,
} from "./disclosurePeriod.js";

test("fortnightly YYYY-MM expands to mid-month and month-end", () => {
  const p = parsePeriodInput("2026-08");
  assert.deepEqual(disclosureStorageKeys(p, "fortnightly"), [
    "2026-08-15",
    "2026-08-31",
  ]);
  assert.equal(disclosureStorageKey(p, "fortnightly"), "2026-08-15");
});

test("fortnightly February YYYY-MM uses month-end day 28/29", () => {
  const p = parsePeriodInput("2026-02");
  assert.deepEqual(disclosureStorageKeys(p, "fortnightly"), [
    "2026-02-15",
    "2026-02-28",
  ]);
});

test("monthly YYYY-MM is month-end only", () => {
  const p = parsePeriodInput("2026-08");
  assert.deepEqual(disclosureStorageKeys(p, "monthly"), ["2026-08-31"]);
});

test("explicit YYYY-MM-DD stays a single key", () => {
  const p = parsePeriodInput("2026-08-31");
  assert.deepEqual(disclosureStorageKeys(p, "fortnightly"), ["2026-08-31"]);
  assert.deepEqual(disclosureStorageKeys(p, "monthly"), ["2026-08-31"]);
});
