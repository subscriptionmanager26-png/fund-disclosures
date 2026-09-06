import {
  blobMatchesYearMonth,
  datesMatchAsOf,
  extractDisclosureDates,
  extractYearMonth,
} from "./disclosureDates.js";

/**
 * Disclosure-page spreadsheet filter.
 *
 * These pages rarely have many Excel files. Any spreadsheet is likely a
 * portfolio pack. A month in the name makes that even more likely. We do
 * not require words like "fortnightly", "overnight", or "portfolio".
 *
 * We still drop obvious non-portfolio packs (AAUM, complaints, …) and files
 * dated to a different calendar month. Mid vs month-end is applied later
 * when an as-of day is known.
 */

const FILE_EXT = /\.(xlsx|xls|csv|zip|xlsm)(?:\?|#|$)/i;

const EXCLUDE =
  /aaum|aauum|\baum\b|complaint|proxy|voting|tracking[\s_-]?error|risk[\s_-]?param|portfolio[\s_-]?overlap|overlap|transaction[\s_-]?report|investor[\s_-]?complaint|\bir_|\bsebi\b|product[\s_-]?dashboard|scheme[\s_-]?dashboard|dashboard|constituent|fund[\s_-]?performance|quarterly[\s_-]?aum|disclosure[\s_-]?of[\s_-]?aum|top\s*\d+\s*holdings(?:\s+by\s+issuer)?|holdings\s+by\s+issuer/i;

function fileBlob(file) {
  const url = String(file?.url || "");
  const filename = String(file?.filename || file?.text || "");
  return { url, filename, blob: `${url} ${filename}` };
}

function baseName(url) {
  try {
    return decodeURIComponent(String(url).split(/[?#]/)[0]).split("/").pop() || "";
  } catch {
    return String(url).split("/").pop() || "";
  }
}

/**
 * @param {{ url?: string, filename?: string, text?: string }} file
 * @param {{ type?: string, period?: { year: number, month: number } | null, storageKey?: string | null }} opts
 * @returns {{ keep: boolean, reason: string, detail?: string }}
 */
export function classifyDisclosureFile(file, opts = {}) {
  const { type = "monthly", period = null, storageKey = null } = opts;
  const { url, filename, blob } = fileBlob(file);

  if (!FILE_EXT.test(url) && !FILE_EXT.test(filename)) {
    return { keep: false, reason: "not_spreadsheet" };
  }

  if (EXCLUDE.test(blob)) {
    return { keep: false, reason: "excluded_non_portfolio" };
  }

  const base = baseName(url) || filename;
  if (/weekly/i.test(base) && !/monthly|fortnight/i.test(base)) {
    return { keep: false, reason: "weekly_not_portfolio" };
  }

  const dates = extractDisclosureDates(blob);
  const dateDetail = dates
    .map((d) => `${d.year}-${String(d.month).padStart(2, "0")}-${String(d.day).padStart(2, "0")}`)
    .join(",");

  if (period) {
    const monthHit = blobMatchesYearMonth(blob, period.year, period.month);
    if (monthHit === false) {
      return {
        keep: false,
        reason: "wrong_month",
        detail: dateDetail || JSON.stringify(extractYearMonth(blob)),
      };
    }
  }

  if (storageKey && /^\d{4}-\d{2}-\d{2}$/.test(storageKey)) {
    if (datesMatchAsOf(blob, storageKey)) {
      return { keep: true, reason: "as_of_slice", detail: dateDetail };
    }
    if (dates.length) {
      return {
        keep: false,
        reason: "wrong_as_of_slice",
        detail: dateDetail,
      };
    }
    // Month in the name (or no date at all) on a disclosure page → keep.
    if (period && blobMatchesYearMonth(blob, period.year, period.month)) {
      return { keep: true, reason: "month_in_name" };
    }
    return { keep: true, reason: "spreadsheet_on_disclosure_page" };
  }

  if (period && blobMatchesYearMonth(blob, period.year, period.month)) {
    return { keep: true, reason: "month_in_name" };
  }
  return { keep: true, reason: "spreadsheet_on_disclosure_page" };
}

/**
 * Adapter-level month gate. Does not require "portfolio" / "fortnightly" tags.
 * Slice (15 vs 31) is applied later in fetch-period.
 */
export function isPeriodPortfolioFile(url, text, matchers, type = "monthly", period = null) {
  const result = classifyDisclosureFile(
    { url, filename: text, text },
    { type, period, storageKey: null },
  );
  return result.keep;
}

/**
 * @returns {{ kept: object[], rejected: object[] }}
 */
export function filterFilesWithReport(files, opts) {
  const kept = [];
  const rejected = [];
  for (const file of files || []) {
    const verdict = classifyDisclosureFile(file, opts);
    if (verdict.keep) kept.push(file);
    else {
      rejected.push({
        url: file.url || "",
        filename: file.filename || file.text || "",
        reason: verdict.reason,
        detail: verdict.detail || "",
      });
    }
  }
  return { kept, rejected };
}

export { FILE_EXT };
