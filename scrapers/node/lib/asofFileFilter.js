/** Match disclosure filenames/URLs to a calendar as-of day (mid-month vs month-end). */

import { AS_OF_RE } from "./disclosureDates.js";
import {
  classifyDisclosureFile,
  filterFilesWithReport,
} from "./portfolioFilter.js";

export {
  AS_OF_RE,
  datesMatchAsOf,
  extractDisclosureDates,
  canonicalFortnightlySlice,
  targetSlice,
} from "./disclosureDates.js";

export { filterFilesWithReport, classifyDisclosureFile };

export function parseStorageKeyDay(storageKey) {
  const m = AS_OF_RE.exec(String(storageKey || ""));
  if (!m) return null;
  return Number(m[3]);
}

function periodFromAsOf(asOf) {
  const m = AS_OF_RE.exec(String(asOf || ""));
  if (!m) return null;
  return { year: Number(m[1]), month: Number(m[2]) };
}

/** True when as-of is the last calendar day of its month. */
export function isMonthEndAsOf(asOf) {
  const m = AS_OF_RE.exec(String(asOf || ""));
  if (!m) return false;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return day === lastDay;
}

export function fileMatchesAsOfStrict(file, asOf) {
  if (!asOf || !AS_OF_RE.test(asOf)) return true;
  return classifyDisclosureFile(file, {
    type: "fortnightly",
    period: periodFromAsOf(asOf),
    storageKey: asOf,
  }).keep;
}

export function fileMatchesStorageKey(file, storageKey, cadence = "fortnightly") {
  if (!storageKey || !AS_OF_RE.test(storageKey)) return true;
  return classifyDisclosureFile(file, {
    type: cadence,
    period: periodFromAsOf(storageKey),
    storageKey,
  }).keep;
}

export function filterFilesForStorageKey(files, storageKey, cadence) {
  return filterFilesWithReport(files, {
    type: cadence,
    period: periodFromAsOf(storageKey),
    storageKey,
  }).kept;
}
