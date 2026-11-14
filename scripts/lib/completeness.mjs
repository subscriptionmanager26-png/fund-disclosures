/**
 * Per-AMC completeness check for published as-of slices.
 *
 * A run used to report "success" while whole AMCs were missing (Oct 2026:
 * monthly 2,092 → 1,950 portfolios, nobody told). This module compares each
 * AMC's published portfolio count for an as-of date with the same slice in the
 * previous month, once the SEBI disclosure deadline has passed:
 *   - monthly portfolio:      within 10 days of month end
 *   - fortnightly (debt):     within 5 days of the fortnight end
 * Before the deadline a missing AMC is "pending", not a failure.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

export const MONTHLY_DUE_DAYS = 10;
export const FORTNIGHTLY_DUE_DAYS = 5;
/** Grace after the SEBI deadline before a gap counts as a failure. */
export const GRACE_DAYS = 1;
/** A drop counts when the AMC lost at least this share and this many books. */
export const DROP_RATIO = 0.25;
export const DROP_MIN = 3;

function lastDay(y, m) {
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

function iso(d) {
  return d.toISOString().slice(0, 10);
}

export function isMonthEnd(asOf) {
  const [y, m, d] = asOf.split("-").map(Number);
  return d === lastDay(y, m);
}

/** Same slice one month earlier: …-15 → previous …-15; month-end → previous month-end. */
export function previousComparableAsOf(asOf) {
  const [y, m] = asOf.split("-").map(Number);
  const prev = new Date(Date.UTC(y, m - 2, 1));
  const py = prev.getUTCFullYear();
  const pm = prev.getUTCMonth() + 1;
  const day = isMonthEnd(asOf) ? lastDay(py, pm) : 15;
  return `${py}-${String(pm).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/** Date after which a missing AMC is a failure (deadline + grace). */
export function dueDate(asOf) {
  const [y, m, d] = asOf.split("-").map(Number);
  // Month-end dates carry the monthly book, which has the later deadline.
  const days = (isMonthEnd(asOf) ? MONTHLY_DUE_DAYS : FORTNIGHTLY_DUE_DAYS) + GRACE_DAYS;
  return iso(new Date(Date.UTC(y, m - 1, d + days)));
}

function normName(s) {
  return String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

/** { normalized AMC name → amc_id } from registry/amcs.json rows. */
export function amcNameIndex(registryAmcs = []) {
  const idx = {};
  for (const a of registryAmcs) {
    for (const n of [a.name, a.amc_name]) if (n) idx[normName(n)] = a.id;
  }
  return idx;
}

/**
 * AMC of a published portfolio. Published JSON follows the public contract,
 * which has no amc_id: it carries scheme.amc_name (registry "name", set by
 * enrich). meta.amc_id is honoured if a future contract adds it.
 */
export function amcOfPayload(payload, nameIndex = {}) {
  const id = payload?.meta?.amc_id || payload?.amc_id;
  if (id) return String(id);
  const name = payload?.scheme?.amc_name || payload?.meta?.amc_name;
  if (!name) return "unknown";
  return nameIndex[normName(name)] || `name:${normName(name)}`;
}

/** { amc_id: count } for portfolios/asof/{date}/*.json in the holdings checkout. */
export function amcCountsForDate(holdingsDir, asOf, nameIndex = {}) {
  const dir = join(holdingsDir, "portfolios", "asof", asOf);
  const counts = {};
  if (!existsSync(dir)) return counts;
  for (const name of readdirSync(dir)) {
    if (!name.endsWith(".json")) continue;
    let amc = "unknown";
    try {
      const payload = JSON.parse(readFileSync(join(dir, name), "utf8"));
      amc = amcOfPayload(payload, nameIndex);
    } catch {
      amc = "unreadable";
    }
    counts[amc] = (counts[amc] || 0) + 1;
  }
  return counts;
}

/**
 * Compare two count maps.
 * @returns {{ missing: object[], dropped: object[] }}
 */
export function compareCounts(prev, now) {
  const missing = [];
  const dropped = [];
  for (const [amc, before] of Object.entries(prev)) {
    if (amc === "unknown" || amc === "unreadable") continue;
    const after = now[amc] || 0;
    if (after === 0) missing.push({ amc, before, after });
    else if (before - after >= DROP_MIN && after < before * (1 - DROP_RATIO)) {
      dropped.push({ amc, before, after });
    }
  }
  const byLoss = (a, b) => b.before - b.after - (a.before - a.after);
  return { missing: missing.sort(byLoss), dropped: dropped.sort(byLoss) };
}

/**
 * Per-AMC fetch diagnosis for one as-of date across all fetch probes
 * (fortnightly + monthly jobs share month-end dates).
 * @param {object[]} probes fetch-period probe JSONs
 * @param {object[]} quarantineReports resolve_quarantine.py outputs
 */
export function fetchDiagnosis(amc, asOf, probes, quarantineReports) {
  const jobs = [];
  for (const p of probes) {
    if (!p || p.storageKey !== asOf) continue;
    const r = (p.results || []).find((x) => x.id === amc);
    if (!r) {
      jobs.push({ type: p.type, status: "not_in_registry_for_type" });
      continue;
    }
    const reasons = {};
    for (const row of r.rejected || []) reasons[row.reason] = (reasons[row.reason] || 0) + 1;
    jobs.push({
      type: p.type,
      status: r.status,
      ...(r.error ? { error: r.error } : {}),
      listed: r.listedCount ?? null,
      kept: r.fileCount ?? 0,
      downloaded: r.downloadOk ?? 0,
      download_failed: r.downloadFailed ?? 0,
      rejected_by_reason: reasons,
      quarantined: r.quarantineCount ?? 0,
    });
  }
  const quarantine = [];
  for (const q of quarantineReports) {
    if (!q || q.storageKey !== asOf) continue;
    for (const row of q.amcs?.[amc] || []) {
      quarantine.push({ type: q.type, file: row.file, verdict: row.verdict, reason: row.reason, as_of_found: row.as_of_found });
    }
  }
  return { jobs, quarantine };
}

/**
 * Build the completeness section of the cloud report.
 * @param {{ holdingsDir: string, asOfDates: string[], today: string, probes?: object[], quarantineReports?: object[] }} args
 */
export function buildCompleteness({
  holdingsDir,
  asOfDates,
  today,
  probes = [],
  quarantineReports = [],
  registryAmcs = [],
}) {
  const nameIndex = amcNameIndex(registryAmcs);
  const slices = [];
  let failures = 0;
  for (const asOf of [...new Set(asOfDates)].sort()) {
    const prevAsOf = previousComparableAsOf(asOf);
    const due = dueDate(asOf);
    const prev = amcCountsForDate(holdingsDir, prevAsOf, nameIndex);
    const now = amcCountsForDate(holdingsDir, asOf, nameIndex);
    const { missing, dropped } = compareCounts(prev, now);
    const overdue = today > due;
    const withDiag = (rows) =>
      rows.map((r) => ({ ...r, diagnosis: fetchDiagnosis(r.amc, asOf, probes, quarantineReports) }));
    const unattributed = (prev.unknown || 0) + (now.unknown || 0);
    const slice = {
      as_of: asOf,
      compared_with: prevAsOf,
      due_after: due,
      status: overdue ? (missing.length || dropped.length ? "incomplete" : "complete") : "pending",
      total_before: Object.values(prev).reduce((a, b) => a + b, 0),
      total_now: Object.values(now).reduce((a, b) => a + b, 0),
      missing: withDiag(missing),
      dropped: withDiag(dropped),
      // Portfolios with no AMC field at all — if this is large the per-AMC check is blind.
      unattributed,
    };
    if (slice.status === "incomplete") failures += missing.length + dropped.length;
    slices.push(slice);
  }
  return { checked_on: today, failures, slices };
}

/** One-line-per-AMC text for the run summary / Slack. */
export function formatCompleteness(c) {
  const lines = [];
  for (const s of c.slices) {
    lines.push(
      `${s.as_of} [${s.status}] ${s.total_now} portfolios (vs ${s.total_before} on ${s.compared_with}; due ${s.due_after})`,
    );
    if (s.status === "pending") {
      if (s.missing.length) lines.push(`  not yet published (before deadline): ${s.missing.map((m) => m.amc).join(", ")}`);
      continue;
    }
    for (const [label, rows] of [["MISSING", s.missing], ["DROPPED", s.dropped]]) {
      for (const r of rows) {
        const why = r.diagnosis.jobs
          .map((j) => `${j.type}:${j.status}${j.error ? ` (${String(j.error).slice(0, 80)})` : ""}${j.quarantined ? ` q=${j.quarantined}` : ""}`)
          .join(" ");
        lines.push(`  ${label} ${r.amc}: ${r.before} → ${r.after}  ${why}`);
      }
    }
  }
  return lines.join("\n");
}
