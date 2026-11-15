import {
  blobMatchesYearMonth,
  datesMatchAsOf,
  extractDayPreciseDates,
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
 * We still drop obvious non-portfolio packs (AAUM, complaints, PRC matrices,
 * half-yearly / Reg 59A, …) and files dated to a different calendar month.
 * Mid vs month-end is applied when an as-of day is known. Undated filenames
 * are rejected once a concrete storage as-of is set (avoids stamping junk
 * into the target slice).
 */

const FILE_EXT = /\.(xlsx|xls|csv|zip|xlsm)(?:\?|#|$)/i;

const EXCLUDE =
  /aaum|aauum|\baum\b|average[\s_-]?assets|assets[\s_-]?under[\s_-]?management|complaint|proxy|voting|tracking[\s_-]?error|risk[\s_-]?param|portfolio[\s_-]?overlap|overlap|transaction[\s_-]?report|investor[\s_-]?complaint|\bir_|\bsebi\b|product[\s_-]?dashboard|scheme[\s_-]?dashboard|dashboard|constituent|fund[\s_-]?performance|quarterly[\s_-]?aum|disclosure[\s_-]?of[\s_-]?aum|top\s*\d+\s*holdings(?:\s+by\s+issuer)?|holdings\s+by\s+issuer|prc[\s_-]?matrix|\bprc\b|potential[\s_-]?risk[\s_-]?class|hyportfolio|half[\s_-]?year(?:ly)?|unaudited[\s_-]?financial|scheme[\s_-]?financial|reg[\s_-]?59a|\b59a\b|misselling|commission[\s_-]?disclosure|scheme[\s_-]?summary|\bssd[\s_-]?\d|mis[\s_-]?report/i;

/** Kotak (and peers) publish full monthly books as "Consolidated SEBI Portfolio". */
const CONSOLIDATED_SEBI_PORTFOLIO =
  /consolidated[\s_-]*sebi[\s_-]*portfolio|consolidatedsebiportfolio/i;

function isExcludedNonPortfolio(blob) {
  if (CONSOLIDATED_SEBI_PORTFOLIO.test(blob)) return false;
  return EXCLUDE.test(blob);
}

const MONTH_WORD =
  /(?<![a-z])(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?![a-z])/i;

/** Label for quarantine reports: "month_without_year:october" when a month name has no year. */
function yearlessMonthDetail(name) {
  const m = MONTH_WORD.exec(String(name || "").toLowerCase().replace(/[_\-.]+/g, " "));
  return m ? `month_without_year:${m[1]}` : "";
}

/**
 * Names worth downloading for a content-date check when the AMC already kept
 * files for this slice. ("scheme"/"fund" matched every Unifi performance sheet:
 * 40 wasted downloads per job in the 2026-11-14 run.) When the AMC kept
 * nothing, every undated candidate is still checked.
 */
const PORTFOLIO_WORD = /portfolio|portolio|holding|fortnight|monthly/i;
const NOT_PORTFOLIO_WORD = /performance|tracking|commission|comparative|factsheet|riskometer/i;
export const PORTFOLIO_HINT = {
  test: (name) => PORTFOLIO_WORD.test(name) && !NOT_PORTFOLIO_WORD.test(name),
};

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

/** Cadence guards use the download name only — not parent URL folders (e.g. Shriram CDN paths). */
function cadenceLabel(file) {
  const { url, filename } = fileBlob(file);
  return baseName(url) || filename;
}

/**
 * @param {{ url?: string, filename?: string, text?: string }} file
 * @param {{ type?: string, period?: { year: number, month: number } | null, storageKey?: string | null }} opts
 * @returns {{ keep: boolean, reason: string, detail?: string }}
 */
export function classifyDisclosureFile(file, opts = {}) {
  const {
    type = "monthly",
    period = null,
    storageKey = null,
    trustAdapterPeriod = false,
  } = opts;
  const { url, filename, blob } = fileBlob(file);

  if (!FILE_EXT.test(url) && !FILE_EXT.test(filename)) {
    return { keep: false, reason: "not_spreadsheet" };
  }

  if (isExcludedNonPortfolio(blob)) {
    return { keep: false, reason: "excluded_non_portfolio" };
  }

  // Cadence guards use the download name only (not CDN folders such as
  // Shriram's ".../Monthly--Fortnightly--Weekly-Portfolio.../"). Monthly jobs
  // always reject fortnightly filenames, even when the adapter scoped the
  // period. Fortnightly jobs may trust the adapter when filenames are CMS
  // hashes (e.g. Sundaram monthlyportfolio_* on Catid=Fortnightly).
  const cadence = cadenceLabel(file);
  if (type === "monthly" && /fortnight(?:ly)?/i.test(cadence)) {
    return { keep: false, reason: "wrong_cadence", detail: "fortnightly_in_monthly" };
  }
  if (
    !trustAdapterPeriod &&
    type === "fortnightly" &&
    /monthly/i.test(cadence) &&
    !/fortnight/i.test(cadence)
  ) {
    return { keep: false, reason: "wrong_cadence", detail: "monthly_in_fortnightly" };
  }

  const base = baseName(url) || filename;
  if (/weekly/i.test(base) && !/monthly|fortnight/i.test(base)) {
    return { keep: false, reason: "weekly_not_portfolio" };
  }

  // python_ref / API adapters already scoped rows to YYYY-MM; filenames may be
  // scheme slugs (Invesco), upload timestamps (LIC), or CMS hashes (Jio).
  if (trustAdapterPeriod) {
    return { keep: true, reason: "adapter_period" };
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
    // Fortnightly slices differ only by day (15th vs month-end). A name that
    // only says the month ("…Fortnightly_Portfolio_Oct-26.xlsx") gets a
    // synthesized month-end day, which put LIC's 15-Oct book into the 31-Oct
    // slice. Let the in-workbook as-of decide (resolve_quarantine.py).
    if (type === "fortnightly" && period) {
      const precise = extractDayPreciseDates(blob).filter(
        (d) => d.year === period.year && d.month === period.month,
      );
      if (!precise.length && blobMatchesYearMonth(blob, period.year, period.month)) {
        return {
          keep: false,
          reason: "undated_no_month",
          detail: "month_only_no_day",
          quarantine: true,
        };
      }
    }
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
    // Month+year in the name with no day → keep for that period's fetches.
    if (period && blobMatchesYearMonth(blob, period.year, period.month)) {
      return { keep: true, reason: "month_in_name" };
    }
    // Undated / unparseable names are NOT stamped with the job as-of (caused
    // Sep-2026 false positives from 2019 half-yearlies and PRC matrices).
    // They are not silently dropped either: fetch-period downloads them to
    // data/quarantine/ and resolve_quarantine.py promotes a file only when the
    // as-of date printed inside the workbook matches this slice.
    return {
      keep: false,
      reason: "undated_no_month",
      detail: dateDetail || yearlessMonthDetail(base),
      quarantine: true,
    };
  }

  if (period && blobMatchesYearMonth(blob, period.year, period.month)) {
    return { keep: true, reason: "month_in_name" };
  }
  // Without a concrete as-of day, still allow undated spreadsheets on hubs
  // that already filtered by period in the adapter (list-only / API).
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
 * @returns {{ kept: object[], rejected: object[], quarantined: object[] }}
 *   quarantined: original file objects whose date could not be read from the
 *   name (also listed in `rejected` with quarantine: true).
 */
export function filterFilesWithReport(files, opts) {
  const kept = [];
  const rejected = [];
  const quarantined = [];
  for (const file of files || []) {
    const verdict = classifyDisclosureFile(file, opts);
    if (verdict.keep) kept.push(file);
    else {
      rejected.push({
        url: file.url || "",
        filename: file.filename || file.text || "",
        reason: verdict.reason,
        detail: verdict.detail || "",
        ...(verdict.quarantine ? { quarantine: true } : {}),
      });
      if (verdict.quarantine) quarantined.push(file);
    }
  }
  return { kept, rejected, quarantined };
}

export { FILE_EXT };
