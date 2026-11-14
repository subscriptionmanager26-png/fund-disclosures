import test from "node:test";
import assert from "node:assert/strict";
import { fetchUntil, isSweepDay, planFetchJobs } from "./fetch-window.mjs";

const jobs = (yms) =>
  yms.flatMap((ym) => {
    const [y, m] = ym.split("-").map(Number);
    const end = `${ym}-${String(new Date(Date.UTC(y, m, 0)).getUTCDate()).padStart(2, "0")}`;
    return [
      { type: "fortnightly", period: `${ym}-15` },
      { type: "fortnightly", period: end },
      { type: "monthly", period: end },
    ];
  });

const active = (plan) => plan.filter((j) => !j.skip).map((j) => `${j.type} ${j.period}`);

test("fetch window = SEBI deadline + grace + 7 days", () => {
  assert.equal(fetchUntil("2026-10-31"), "2026-11-18"); // monthly due 11-11
  assert.equal(fetchUntil("2026-10-15"), "2026-10-28"); // fortnightly due 10-21
});

test("2026-11-14: only the October month-end slices are fetched (was 5 jobs)", () => {
  const plan = planFetchJobs(jobs(["2026-10", "2026-11"]), { today: "2026-11-14" });
  // Month-end dates share the monthly deadline (completeness.dueDate), so FN-31 stays open too.
  assert.deepEqual(active(plan), ["fortnightly 2026-10-31", "monthly 2026-10-31"]);
  const reasons = Object.fromEntries(plan.map((j) => [`${j.type} ${j.period}`, j.skip]));
  assert.equal(reasons["fortnightly 2026-11-15"], "future_as_of");
  assert.equal(reasons["monthly 2026-11-30"], "future_as_of");
  assert.equal(reasons["fortnightly 2026-10-15"], "window_closed");
});

test("early month: previous month-end slices are fetched", () => {
  const plan = planFetchJobs(jobs(["2026-10", "2026-11"]), { today: "2026-11-03" });
  assert.deepEqual(active(plan), ["fortnightly 2026-10-31", "monthly 2026-10-31"]);
});

test("weekly sweep fetches every past slice (catches late revisions)", () => {
  const plan = planFetchJobs(jobs(["2026-10", "2026-11"]), { today: "2026-11-15", sweep: true });
  assert.deepEqual(active(plan), [
    "fortnightly 2026-10-15",
    "fortnightly 2026-10-31",
    "monthly 2026-10-31",
    "fortnightly 2026-11-15",
  ]);
});

test("sweep day detection (UTC weekday)", () => {
  assert.equal(isSweepDay("2026-11-15"), true); // Sunday
  assert.equal(isSweepDay("2026-11-14"), false);
  assert.equal(isSweepDay("2026-11-14", { force: true }), true);
  assert.equal(isSweepDay("2026-11-15", { weekday: -1 }), false);
});

test("November 2026: 151 fetch jobs under the old rule, 62 under the new one (Sunday sweeps included)", () => {
  let oldJobs = 0;
  let newJobs = 0;
  for (let d = 1; d <= 30; d++) {
    const today = `2026-11-${String(d).padStart(2, "0")}`;
    const all = jobs(["2026-10", "2026-11"]);
    oldJobs += all.filter((j) => !(j.type === "monthly" && j.period > today)).length;
    newJobs += active(planFetchJobs(all, { today, sweep: isSweepDay(today) })).length;
  }
  assert.equal(oldJobs, 151);
  assert.equal(newJobs, 62);
});
