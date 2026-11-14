/**
 * Which disclosure slices the daily job fetches.
 *
 * The daily job used to fetch all six slices of the previous + current month
 * every day (FN-15, FN month-end, monthly × 2 months), including slices whose
 * as-of date is still in the future and slices that closed weeks ago. Most of
 * those fetches return nothing new but still cost a full crawl of ~50 AMC hubs.
 *
 * A slice is fetched from its as-of date until `windowDays` after its SEBI
 * deadline (see completeness.mjs dueDate: monthly +10d, fortnightly +5d, +1d
 * grace). Late filers inside that window are still picked up; after it, the
 * completeness check keeps flagging any AMC that never published. A weekly
 * full sweep (default Sunday UTC) fetches everything to catch revisions.
 */
import { dueDate } from "./completeness.mjs";

export const DEFAULT_WINDOW_DAYS = 7;
export const DEFAULT_SWEEP_WEEKDAY = 0; // Sunday (UTC)

function addDays(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

/** Last date (inclusive) on which a slice is still fetched outside a sweep. */
export function fetchUntil(asOf, windowDays = DEFAULT_WINDOW_DAYS) {
  return addDays(dueDate(asOf), windowDays);
}

export function isSweepDay(today, { weekday = DEFAULT_SWEEP_WEEKDAY, force = false } = {}) {
  if (force) return true;
  if (weekday === null || weekday === undefined || weekday < 0) return false;
  const [y, m, d] = today.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay() === weekday;
}

/**
 * @param {{ type: string, period: string }[]} jobs  candidate fetch jobs (period = as-of YYYY-MM-DD)
 * @param {{ today: string, sweep?: boolean, windowDays?: number }} opts
 * @returns {{ type: string, period: string, skip: null | string, fetch_until: string }[]}
 */
export function planFetchJobs(jobs, { today, sweep = false, windowDays = DEFAULT_WINDOW_DAYS }) {
  return jobs.map((j) => {
    const until = fetchUntil(j.period, windowDays);
    let skip = null;
    // Nothing can be published for an as-of date that has not happened yet.
    if (j.period > today) skip = "future_as_of";
    else if (!sweep && today > until) skip = "window_closed";
    return { ...j, skip, fetch_until: until };
  });
}
