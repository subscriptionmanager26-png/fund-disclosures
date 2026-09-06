/**
 * Disclosure folder keys under data/disclosures/{cadence}/{key}/.
 *
 * YYYY-MM is a calendar-month shorthand; storage uses an as-of date so
 * fortnightly mid-month (15th) and month-end (31st) never share a folder.
 */
const AS_OF_RE = /^\d{4}-\d{2}-\d{2}$/;
const PERIOD_RE = /^(\d{4})-(\d{2})$/;

/**
 * @param {string} period YYYY-MM or YYYY-MM-DD
 */
export function parsePeriodInput(period) {
  if (AS_OF_RE.test(period)) {
    const [y, m, d] = period.split("-").map(Number);
    if (m < 1 || m > 12 || d < 1 || d > 31) {
      throw new Error(`Invalid date in "${period}"`);
    }
    return {
      input: period,
      storageKey: period,
      year: y,
      month: m,
      day: d,
      isFullDate: true,
    };
  }
  const m = PERIOD_RE.exec(period);
  if (!m) {
    throw new Error(`Invalid period "${period}"; expected YYYY-MM or YYYY-MM-DD`);
  }
  const year = Number(m[1]);
  const month = Number(m[2]);
  if (month < 1 || month > 12) {
    throw new Error(`Invalid month in "${period}"`);
  }
  const monthEndDay = new Date(year, month, 0).getDate();
  return {
    input: period,
    storageKey: null,
    year,
    month,
    day: null,
    monthEndDay,
    isFullDate: false,
  };
}

/**
 * All folder keys for a fetch/parse period argument.
 * Fortnightly YYYY-MM expands to both mid-month (15) and month-end so daily
 * jobs never miss debt packs that only appear on the 31st (e.g. new launches).
 * @param {ReturnType<typeof parsePeriodInput>} parsed
 * @param {'monthly'|'fortnightly'} cadence
 * @returns {string[]}
 */
export function disclosureStorageKeys(parsed, cadence) {
  if (parsed.isFullDate) return [parsed.storageKey];
  const end = `${parsed.input}-${String(parsed.monthEndDay).padStart(2, "0")}`;
  if (cadence === "fortnightly") {
    return [`${parsed.input}-15`, end];
  }
  return [end];
}

/**
 * Primary folder name (first of {@link disclosureStorageKeys}).
 * Prefer {@link disclosureStorageKeys} when both FN slices matter.
 * @param {ReturnType<typeof parsePeriodInput>} parsed
 * @param {'monthly'|'fortnightly'} cadence
 */
export function disclosureStorageKey(parsed, cadence) {
  return disclosureStorageKeys(parsed, cadence)[0];
}

/**
 * Parsed / disclosure dirs to scan when resolving a calendar as-of date.
 * @param {string} asOf YYYY-MM-DD
 * @param {'monthly'|'fortnightly'} cadence
 */
export function disclosurePeriodCandidates(asOf, cadence) {
  const keys = [];
  if (AS_OF_RE.test(asOf)) keys.push(asOf);
  const ym = asOf.slice(0, 7);
  if (PERIOD_RE.test(ym) && !keys.includes(ym)) keys.push(ym);
  return keys;
}

export { AS_OF_RE, PERIOD_RE };
