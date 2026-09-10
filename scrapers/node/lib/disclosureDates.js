/**
 * Shared AMC disclosure date rules (Node).
 * Keep in lockstep with scrapers/python/lib/disclosure_date.py
 *
 * Handles Aug/August, Sept/Sep, 15th, 14-Aug-2026, 15-Aug-26, ISO dates,
 * glued Kotak names (August312026), and path quirks (as-on-August-31,-2026).
 * Fortnightly filings on days 13–16 map to the 15th slice; late-month days
 * map to month-end.
 */
export const AS_OF_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

const MONTH_NUM = {
  january: 1,
  jan: 1,
  february: 2,
  feb: 2,
  march: 3,
  mar: 3,
  april: 4,
  apr: 4,
  may: 5,
  june: 6,
  jun: 6,
  july: 7,
  jul: 7,
  august: 8,
  aug: 8,
  september: 9,
  sept: 9,
  sep: 9,
  october: 10,
  oct: 10,
  november: 11,
  nov: 11,
  december: 12,
  dec: 12,
};
const MONTH_TOKEN =
  "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?";
const MID_DAY_MIN = 13;
const MID_DAY_MAX = 16;
const END_NEAR_LAST = 3;
const END_DAY_MIN = 27;

function expandYear(n) {
  const y = Number(n);
  return y < 100 ? 2000 + y : y;
}

export function lastDayOfMonth(year, month) {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function validDate(year, month, day) {
  if (!year || month < 1 || month > 12 || day < 1 || day > 31) return null;
  if (year < 1990 || year > 2100) return null;
  if (day > lastDayOfMonth(year, month)) return null;
  return { year, month, day };
}

export function dateKey(d) {
  return `${d.year}-${String(d.month).padStart(2, "0")}-${String(d.day).padStart(2, "0")}`;
}

function monthYearTokenOk(sep, yearRaw, blob, end) {
  if (String(yearRaw).length === 4) return true;
  const yy = Number(yearRaw);
  if (yy > 31) return true;
  // Underscore + 01–31 is almost always a day (Abakkus Jul_31_<hash>).
  if (String(sep).includes("_")) return false;
  // Hyphen day before a real year: as-on-August-31,-2026
  const rest = String(blob).slice(end, end + 10);
  if (/^,\s*-?\s*20\d{2}/.test(rest) || /^[-/.]20\d{2}/.test(rest)) return false;
  return true;
}

/** Unique calendar dates from labels, titles, and filenames. */
export function extractDisclosureDates(...parts) {
  const blob = parts.filter(Boolean).join(" ");
  if (!blob) return [];
  const found = new Map();
  const add = (d) => {
    if (d) found.set(dateKey(d), d);
  };

  for (const m of blob.matchAll(/(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)/g)) {
    add(validDate(Number(m[1]), Number(m[2]), Number(m[3])));
  }

  const dmyName = new RegExp(
    `(?<!\\d)(\\d{1,2})(?:st|nd|rd|th)?[-_\\s./]+(${MONTH_TOKEN})[-_\\s./]+(\\d{2}|\\d{4})(?!\\d)`,
    "gi",
  );
  for (const m of blob.matchAll(dmyName)) {
    const mon = MONTH_NUM[m[2].toLowerCase()];
    if (mon) add(validDate(expandYear(m[3]), mon, Number(m[1])));
  }

  const mdyName = new RegExp(
    `(?<![A-Za-z])(${MONTH_TOKEN})[-_\\s./]+(\\d{1,2})(?:st|nd|rd|th)?(?:[-_\\s./]+|,\\s*-?\\s*)(\\d{2}|\\d{4})(?!\\d)`,
    "gi",
  );
  for (const m of blob.matchAll(mdyName)) {
    const mon = MONTH_NUM[m[1].toLowerCase()];
    if (mon) add(validDate(expandYear(m[3]), mon, Number(m[2])));
  }

  // Kotak: FortnightlyPortfolioAugust312026.xlsx / July152026
  // Allow CamelCase boundary (…PortfolioAugust31…) as well as a normal word break.
  const mdyGlued = new RegExp(
    `(?:(?<![A-Za-z])|(?<=[a-z]))(${MONTH_TOKEN})(\\d{1,2})(20\\d{2})(?!\\d)`,
    "gi",
  );
  for (const m of blob.matchAll(mdyGlued)) {
    const mon = MONTH_NUM[m[1].toLowerCase()];
    if (mon) add(validDate(Number(m[3]), mon, Number(m[2])));
  }

  // Edelweiss: EDEL_Fortnightly_Disclosure_31Aug2026_….xlsx / 15Jul2026
  const dmyGlued = new RegExp(
    `(?<!\\d)(\\d{1,2})(${MONTH_TOKEN})(20\\d{2})(?!\\d)`,
    "gi",
  );
  for (const m of blob.matchAll(dmyGlued)) {
    const mon = MONTH_NUM[m[2].toLowerCase()];
    if (mon) add(validDate(Number(m[3]), mon, Number(m[1])));
  }

  for (const m of blob.matchAll(/(?<!\d)(\d{1,2})[-_/.](\d{1,2})[-_/.](\d{2}|\d{4})(?!\d)/g)) {
    const a = Number(m[1]);
    const b = Number(m[2]);
    const y = expandYear(m[3]);
    if (a > 12 && b >= 1 && b <= 12) add(validDate(y, b, a));
    else if (b > 12 && a >= 1 && a <= 12) add(validDate(y, a, b));
    else if (a >= 1 && a <= 12 && b >= 1 && b <= 12) add(validDate(y, b, a));
  }

  for (const m of blob.matchAll(/(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)/g)) {
    add(validDate(Number(m[3]), Number(m[2]), Number(m[1])));
  }

  return [...found.values()].sort((x, y) => dateKey(x).localeCompare(dateKey(y)));
}

/** All calendar months found (full dates + month-year tokens), in order. */
export function extractAllYearMonths(...parts) {
  const blob = parts.filter(Boolean).join(" ");
  if (!blob) return [];
  const found = [];
  const seen = new Set();
  const add = (year, month) => {
    if (!year || month < 1 || month > 12 || year < 1990 || year > 2100) return;
    const key = `${year}-${month}`;
    if (seen.has(key)) return;
    seen.add(key);
    found.push({ year, month });
  };

  for (const d of extractDisclosureDates(blob)) {
    add(d.year, d.month);
  }

  const monthOf = new RegExp(`month\\s+of\\s+(${MONTH_TOKEN})\\s+(20\\d{2}|\\d{2})`, "gi");
  for (const m of blob.matchAll(monthOf)) {
    const month = MONTH_NUM[m[1].toLowerCase()];
    if (month) add(expandYear(m[2]), month);
  }

  const monthYear = new RegExp(
    `(?<![A-Za-z])(${MONTH_TOKEN})([-_\\s./]+)(20\\d{2}|\\d{2})(?!\\d)`,
    "gi",
  );
  for (const m of blob.matchAll(monthYear)) {
    const month = MONTH_NUM[m[1].toLowerCase()];
    if (!month || !monthYearTokenOk(m[2], m[3], blob, m.index + m[0].length)) continue;
    add(expandYear(m[3]), month);
  }

  // Mirae: sml250_aug2026.xlsx / largecap_aug2026.xlsx
  const monthYearGlued = new RegExp(
    `(?<![A-Za-z])(${MONTH_TOKEN})(20\\d{2})(?!\\d)`,
    "gi",
  );
  for (const m of blob.matchAll(monthYearGlued)) {
    const month = MONTH_NUM[m[1].toLowerCase()];
    if (month) add(Number(m[2]), month);
  }

  return found;
}

export function extractYearMonth(...parts) {
  const yms = extractAllYearMonths(...parts);
  if (!yms.length) return null;
  // Prefer the last token — scheme maturity often precedes disclosure as-of.
  return yms[yms.length - 1];
}

export function canonicalFortnightlySlice(year, month, day) {
  const last = lastDayOfMonth(year, month);
  if (day >= MID_DAY_MIN && day <= MID_DAY_MAX) {
    return `${year}-${String(month).padStart(2, "0")}-15`;
  }
  if (day >= last - END_NEAR_LAST || day >= END_DAY_MIN) {
    return `${year}-${String(month).padStart(2, "0")}-${String(last).padStart(2, "0")}`;
  }
  return null;
}

export function targetSlice(asOf) {
  const m = AS_OF_RE.exec(String(asOf || ""));
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  const last = lastDayOfMonth(year, month);
  if (day <= 16) return `${year}-${String(month).padStart(2, "0")}-15`;
  return `${year}-${String(month).padStart(2, "0")}-${String(last).padStart(2, "0")}`;
}

export function datesMatchAsOf(blob, asOf) {
  const want = targetSlice(asOf);
  if (!want) return true;
  const ym = AS_OF_RE.exec(asOf);
  const year = Number(ym[1]);
  const month = Number(ym[2]);
  for (const d of extractDisclosureDates(blob)) {
    if (d.year !== year || d.month !== month) continue;
    if (canonicalFortnightlySlice(d.year, d.month, d.day) === want) return true;
  }
  return false;
}

/** True when extracted dates/month fall in YYYY-MM. Null if nothing parsed. */
export function blobMatchesYearMonth(blob, year, month) {
  const yms = extractAllYearMonths(blob);
  if (!yms.length) return null;
  return yms.some((d) => d.year === year && d.month === month);
}
