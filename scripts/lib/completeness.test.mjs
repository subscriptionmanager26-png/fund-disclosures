import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  buildCompleteness,
  compareCounts,
  dueDate,
  formatCompleteness,
  previousComparableAsOf,
} from "./completeness.mjs";

test("previous comparable slice", () => {
  assert.equal(previousComparableAsOf("2026-10-31"), "2026-09-30");
  assert.equal(previousComparableAsOf("2026-10-15"), "2026-09-15");
  assert.equal(previousComparableAsOf("2026-03-31"), "2026-02-28");
  assert.equal(previousComparableAsOf("2027-01-15"), "2026-12-15");
});

test("SEBI deadline + 1 day grace", () => {
  assert.equal(dueDate("2026-10-31"), "2026-11-11"); // monthly: 10 days
  assert.equal(dueDate("2026-10-15"), "2026-10-21"); // fortnightly: 5 days
});

test("compareCounts flags missing AMCs and large drops only", () => {
  const { missing, dropped } = compareCounts(
    { choice: 12, shriram: 20, sbi: 100, hdfc: 90, tiny: 2 },
    { sbi: 99, hdfc: 50, tiny: 1 },
  );
  assert.deepEqual(missing.map((m) => m.amc), ["shriram", "choice"]);
  assert.deepEqual(dropped.map((d) => d.amc), ["hdfc"]); // sbi -1 and tiny -1 are noise
});

function writeAsOf(dir, asOf, amcCounts) {
  const d = join(dir, "portfolios", "asof", asOf);
  mkdirSync(d, { recursive: true });
  let n = 0;
  for (const [amc, count] of Object.entries(amcCounts)) {
    for (let i = 0; i < count; i++) {
      writeFileSync(join(d, `${100000 + n++}.json`), JSON.stringify({ meta: { amc_id: amc }, holdings: [] }));
    }
  }
}

test("buildCompleteness: Oct-2026 shape → incomplete with named AMCs and diagnosis", () => {
  const dir = mkdtempSync(join(tmpdir(), "completeness-"));
  try {
    writeAsOf(dir, "2026-09-30", { "choice-mutual-fund": 12, "sbi-mutual-fund": 40, "whiteoak-capital-mutual-fund": 18 });
    writeAsOf(dir, "2026-10-31", { "sbi-mutual-fund": 40 });
    writeAsOf(dir, "2026-11-15", {});
    writeAsOf(dir, "2026-10-15", {});
    const probes = [
      {
        type: "monthly",
        storageKey: "2026-10-31",
        results: [
          { id: "choice-mutual-fund", status: "needs_review", listedCount: 14, fileCount: 0, quarantineCount: 14, rejected: [{ reason: "undated_no_month" }] },
          { id: "whiteoak-capital-mutual-fund", status: "error", error: "whiteoak-capital-mutual-fund listFiles timed out after 180000ms" },
        ],
      },
    ];
    const c = buildCompleteness({
      holdingsDir: dir,
      asOfDates: ["2026-10-31", "2026-11-15"],
      today: "2026-11-13",
      probes,
    });
    const oct = c.slices.find((s) => s.as_of === "2026-10-31");
    assert.equal(oct.status, "incomplete");
    assert.equal(oct.total_before, 70);
    assert.equal(oct.total_now, 40);
    assert.deepEqual(oct.missing.map((m) => m.amc).sort(), ["choice-mutual-fund", "whiteoak-capital-mutual-fund"]);
    const wo = oct.missing.find((m) => m.amc === "whiteoak-capital-mutual-fund");
    assert.match(wo.diagnosis.jobs[0].error, /timed out/);
    // Not yet due → pending, not a failure.
    assert.equal(c.slices.find((s) => s.as_of === "2026-11-15").status, "pending");
    assert.equal(c.failures, 2);
    const text = formatCompleteness(c);
    assert.match(text, /MISSING whiteoak-capital-mutual-fund: 18 → 0 {2}monthly:error \(whiteoak/);
    assert.match(text, /MISSING choice-mutual-fund: 12 → 0 {2}monthly:needs_review q=14/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("buildCompleteness: before deadline nothing fails", () => {
  const dir = mkdtempSync(join(tmpdir(), "completeness-"));
  try {
    writeAsOf(dir, "2026-09-30", { a: 10 });
    writeAsOf(dir, "2026-10-31", {});
    const c = buildCompleteness({ holdingsDir: dir, asOfDates: ["2026-10-31"], today: "2026-11-05" });
    assert.equal(c.slices[0].status, "pending");
    assert.equal(c.failures, 0);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("published contract shape (scheme.amc_name, no amc_id) is attributed via the registry", () => {
  const dir = mkdtempSync(join(tmpdir(), "completeness-"));
  try {
    const write = (asOf, rows) => {
      const d = join(dir, "portfolios", "asof", asOf);
      mkdirSync(d, { recursive: true });
      rows.forEach(([amcName, n], k) => {
        for (let i = 0; i < n; i++) {
          writeFileSync(
            join(d, `${200000 + k * 1000 + i}.json`),
            JSON.stringify({ scheme: { amc_name: amcName }, meta: { as_of: asOf }, holdings: [] }),
          );
        }
      });
    };
    write("2026-09-30", [["Choice Mutual Fund", 12], ["SBI Mutual Fund", 40]]);
    write("2026-10-31", [["SBI Mutual Fund", 40]]);
    const c = buildCompleteness({
      holdingsDir: dir,
      asOfDates: ["2026-10-31"],
      today: "2026-11-14",
      registryAmcs: [
        { id: "choice-mutual-fund", name: "Choice Mutual Fund" },
        { id: "sbi-mutual-fund", name: "SBI Mutual Fund" },
      ],
    });
    assert.equal(c.slices[0].status, "incomplete");
    assert.deepEqual(c.slices[0].missing.map((m) => m.amc), ["choice-mutual-fund"]);
    assert.equal(c.slices[0].unattributed, 0);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
