#!/usr/bin/env node
/**
 * Collect portfolios for a calendar as-of date from data/parsed/{cadence}/{period}/.
 *
 * Canonical layout (local + GitHub):
 *   data/parsed/{cadence}/{YYYY-MM-DD}/{amc}/{fund}/portfolio.json   ← preferred
 *   portfolios/asof/{YYYY-MM-DD}/{portfolio_id}.json                 ← CDN
 *
 * Legacy period folders (YYYY-MM) are still scanned as fallbacks.
 */
import { existsSync, readdirSync, readFileSync, statSync, unlinkSync, copyFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { disclosurePeriodCandidates } from "../../scrapers/node/lib/disclosurePeriod.js";

const AS_OF_RE = /^\d{4}-\d{2}-\d{2}$/;
const PERIOD_RE = /^\d{4}-\d{2}$/;

export function normalizeAsOf(raw) {
  const s = String(raw || "").trim();
  if (AS_OF_RE.test(s)) return s;
  if (PERIOD_RE.test(s)) {
    // YYYY-MM → month-end (convenience)
    const [y, m] = s.split("-").map(Number);
    const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
    return `${s}-${String(last).padStart(2, "0")}`;
  }
  return "";
}

export function sourcePeriodFromAsOf(asOf) {
  return String(asOf || "").slice(0, 7);
}

/** True when as-of is the last calendar day of its month. */
export function isMonthEndAsOf(asOf) {
  const s = normalizeAsOf(asOf);
  if (!AS_OF_RE.test(s)) return false;
  const [y, m, d] = s.split("-").map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return d === last;
}

function walkPortfolioFiles(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    let st;
    try {
      st = statSync(p);
    } catch {
      continue;
    }
    if (st.isDirectory()) walkPortfolioFiles(p, out);
    else if (name === "portfolio.json") out.push(p);
  }
  return out;
}

/** Resolve parent portfolio book id (share-class → shared holdings file). */
export function resolvePortfolioId(meta, catalogLookup) {
  const amfi = String(meta?.amfi_code || meta?.scheme_id || "").trim();
  if (!/^\d{4,8}$/.test(amfi)) return "";
  const row = catalogLookup?.[amfi];
  if (row?.portfolio_id && /^\d{4,8}$/.test(String(row.portfolio_id))) {
    return String(row.portfolio_id);
  }
  if (row?.parent_amfi && /^\d{4,8}$/.test(String(row.parent_amfi))) {
    return String(row.parent_amfi);
  }
  return amfi;
}

/**
 * @returns {Map<string, { portfolio_id: string, local_path: string, meta: object, members: string[] }>}
 */
/**
 * Defense-in-depth: reject source workbooks that clearly belong to another
 * cadence/slice (e.g. 31-Jul monthly equity packs stamped as_of=15).
 * Agents must still LLM-review new Excel files before publish — see docs.
 */
export function sourceFileMatchesAsOfCadence(meta, asOf, cadence) {
  const src = String(meta?.source_file || meta?.source || "");
  if (!src || !AS_OF_RE.test(String(asOf || ""))) return true;
  const day = Number(String(asOf).slice(8, 10));
  const lower = src.toLowerCase();

  if (cadence === "fortnightly" && day === 15) {
    if (
      /(?:^|[^0-9])31[-_./ ]?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)|(?:january|february|march|april|may|june|july|august|september|october|november|december)[-_./ ]*31(?:st)?/i.test(
        src,
      ) &&
      !/\b15\b|15th|15[-_./ ]/.test(lower)
    ) {
      return false;
    }
    if (
      /in_mf_monthly_|monthly-portfolio-|monthly portfolio|equity exposure/i.test(
        lower,
      )
    ) {
      return false;
    }
    // Fortnightly month-end packs (…July312026…) belong under YYYY-MM-31, not -15.
    if (
      /fortnightlyportfolio(?:of)?july31|fortnightly.*31[-_ ]?jul|31st[-_ ]july.*fortnight/i.test(
        lower,
      )
    ) {
      return false;
    }
  }
  return true;
}

export function collectAsOfPortfolios({
  root,
  cadence,
  sourcePeriod,
  asOf,
  catalogLookup = null,
}) {
  const periodKeys = disclosurePeriodCandidates(asOf, cadence);
  if (sourcePeriod && !periodKeys.includes(sourcePeriod)) {
    periodKeys.push(sourcePeriod);
  }
  const byId = new Map();

  for (const periodKey of periodKeys) {
    const base = join(root, "data", "parsed", cadence, periodKey);
    if (!existsSync(base)) continue;

    for (const abs of walkPortfolioFiles(base)) {
      let payload;
      try {
        payload = JSON.parse(readFileSync(abs, "utf8"));
      } catch {
        continue;
      }
      const meta = payload?.meta || {};
      const fileAsOf = String(meta.as_of || meta.as_of || "").slice(0, 10);
      if (fileAsOf !== asOf) continue;
      if (!sourceFileMatchesAsOfCadence(meta, asOf, cadence)) continue;
      const id = resolvePortfolioId(meta, catalogLookup);
      if (!/^\d{4,8}$/.test(id)) continue;

      const rel = abs.startsWith(root) ? abs.slice(root.length + 1) : abs;
      let entry = byId.get(id);
      if (!entry) {
        entry = {
          portfolio_id: id,
          local_path: rel,
          meta,
          members: [],
          payload,
        };
        byId.set(id, entry);
      } else {
        // Prefer richer books
        const prevN = (entry.payload?.holdings || []).length;
        const nextN = (payload.holdings || []).length;
        if (nextN >= prevN) {
          entry.local_path = rel;
          entry.meta = meta;
          entry.payload = payload;
        }
      }
    }
  }

  if (!byId.size) return byId;

  // Attach sibling share-classes from catalog when available
  if (catalogLookup) {
    for (const row of Object.values(catalogLookup)) {
      const amfi = String(row.amfi_code || "").trim();
      const pid = String(
        row.portfolio_id || row.parent_amfi || row.amfi_code || "",
      ).trim();
      if (!/^\d{4,8}$/.test(pid)) continue;
      const entry = byId.get(pid);
      if (!entry) continue;
      if (amfi && !entry.members.includes(amfi)) entry.members.push(amfi);
    }
  }

  for (const entry of byId.values()) {
    if (!entry.members.length) entry.members.push(entry.portfolio_id);
    entry.members.sort();
  }
  return byId;
}

export function mergeFilings(existing, next) {
  const byKey = new Map();
  for (const f of [...(existing?.filings || []), next]) {
    if (!f?.as_of) continue;
    byKey.set(`${f.as_of}::${f.cadence || ""}`, f);
  }
  const filings = [...byKey.values()].sort((a, b) =>
    String(b.as_of).localeCompare(String(a.as_of)),
  );
  return {
    generated_at: new Date().toISOString(),
    filings,
  };
}

/** Parent portfolio ids used to ignore stale child-keyed asof files. */
export function parentPortfolioIds(catalog) {
  const ids = new Set();
  for (const row of Object.values(catalog || {})) {
    const pid = String(row?.portfolio_id || "").trim();
    if (/^\d{4,8}$/.test(pid)) ids.add(pid);
  }
  return ids;
}

export function portfolioAsofKey(asOf, portfolioId) {
  return `portfolios/asof/${asOf}/${portfolioId}.json`;
}

export function attachAvailableAsOf(
  catalog,
  asOfDatesByPortfolio,
  { cdnUrlFn, outDir = null } = {},
) {
  const out = { ...catalog };
  for (const [code, row] of Object.entries(out)) {
    if (!row || typeof row !== "object") continue;
    const pid = String(
      row.portfolio_id || row.parent_amfi || row.amfi_code || "",
    ).trim();
    const merged = new Set(pid ? asOfDatesByPortfolio.get(pid) || [] : []);

    // Keep published as-of links when the portfolio file still exists on disk.
    for (const d of row.available_as_of || []) {
      const day = String(d).trim();
      if (!AS_OF_RE.test(day) || !pid) continue;
      if (outDir) {
        const path = join(outDir, portfolioAsofKey(day, pid));
        if (existsSync(path)) merged.add(day);
      } else {
        merged.add(day);
      }
    }

    if (merged.size) {
      const available = [...merged].sort().reverse();
      const latest = available[0] || null;
      const portfolio_key =
        latest && pid ? portfolioAsofKey(latest, pid) : row.portfolio_key ?? null;
      out[code] = {
        ...row,
        portfolio_id: row.portfolio_id || pid,
        available_as_of: available,
        latest_as_of: latest,
        portfolio_key,
        portfolio_url:
          portfolio_key && cdnUrlFn
            ? cdnUrlFn(portfolio_key)
            : row.portfolio_url ?? null,
      };
    } else if ("available_as_of" in row || "latest_as_of" in row) {
      const { available_as_of: _a, latest_as_of: _l, ...rest } = row;
      out[code] = rest;
    }
  }
  return out;
}

/**
 * Scan on-disk asof trees. When catalog is provided, ignore child-AMFI duplicate
 * filenames (legacy sync artefact).
 */
export function scanExistingAsOfDirs(outDir, catalog = null) {
  /** @type {Map<string, Set<string>>} portfolio_id → as_of dates */
  const map = new Map();
  const parentIds = catalog ? parentPortfolioIds(catalog) : null;
  const asofRoot = join(outDir, "portfolios", "asof");
  if (!existsSync(asofRoot)) return map;
  for (const date of readdirSync(asofRoot)) {
    if (!AS_OF_RE.test(date)) continue;
    const dir = join(asofRoot, date);
    let st;
    try {
      st = statSync(dir);
    } catch {
      continue;
    }
    if (!st.isDirectory()) continue;
    for (const name of readdirSync(dir)) {
      if (!name.endsWith(".json")) continue;
      const id = name.replace(/\.json$/, "");
      if (!/^\d{4,8}$/.test(id)) continue;
      if (parentIds?.size && !parentIds.has(id)) continue;
      if (!map.has(id)) map.set(id, new Set());
      map.get(id).add(date);
    }
  }
  return map;
}

/** Count deduped parent portfolio files in one asof directory. */
export function countDedupedAsOfDir(asOfDir, catalog) {
  if (!existsSync(asOfDir)) return 0;
  const parentIds = parentPortfolioIds(catalog);
  if (!parentIds.size) {
    return readdirSync(asOfDir).filter((n) => n.endsWith(".json")).length;
  }
  let count = 0;
  for (const name of readdirSync(asOfDir)) {
    if (!name.endsWith(".json")) continue;
    const id = name.replace(/\.json$/, "");
    if (parentIds.has(id)) count += 1;
  }
  return count;
}

/**
 * Remove stale asof JSON not in keepIds (and always drop child-AMFI duplicate keys).
 * When mergeExisting is true, keep portfolios already on disk that are not in keepIds
 * (used for fortnightly month-end sync before monthly overwrites with full universe).
 */
export function pruneOrphanAsOfPortfolios(
  outDir,
  asOf,
  keepIds,
  catalog = null,
  { mergeExisting = false } = {},
) {
  const dir = join(outDir, "portfolios", "asof", asOf);
  if (!existsSync(dir)) return 0;
  const keep = new Set([...keepIds].map((id) => `${id}.json`));
  const keepIdSet = new Set([...keepIds].map((id) => String(id)));
  const parentIds = catalog ? parentPortfolioIds(catalog) : null;
  let removed = 0;
  for (const name of readdirSync(dir)) {
    if (!name.endsWith(".json")) continue;
    const id = name.replace(/\.json$/, "");
    // Never prune ids we just synced — new schemes may be absent from a
    // stale amfi-lookup and would otherwise look like child-AMFI duplicates.
    if (keepIdSet.has(id)) continue;
    const isChildDuplicate = parentIds?.size && !parentIds.has(id);
    const isOrphan = !mergeExisting && !keep.has(name);
    if (isOrphan || isChildDuplicate) {
      unlinkSync(join(dir, name));
      removed += 1;
    }
  }
  return removed;
}

/** Infer disclosure cadence from calendar as-of (one CDN folder per date). */
export function cadenceForAsOf(asOf) {
  const s = normalizeAsOf(asOf);
  if (!AS_OF_RE.test(s)) return "fortnightly";
  const day = Number(s.slice(8, 10));
  if (day === 15) return "fortnightly";
  if (isMonthEndAsOf(s)) return "monthly";
  return day <= 15 ? "fortnightly" : "monthly";
}

/** Rebuild catalog/filings.json rows from on-disk as-of dirs (deduped counts). */
export function buildFilingsFromAsOfDirs(outDir, catalog) {
  const asofRoot = join(outDir, "portfolios", "asof");
  const filings = [];
  if (!existsSync(asofRoot)) {
    return {
      generated_at: new Date().toISOString(),
      filings,
    };
  }
  for (const date of readdirSync(asofRoot)) {
    if (!AS_OF_RE.test(date)) continue;
    const dir = join(asofRoot, date);
    let st;
    try {
      st = statSync(dir);
    } catch {
      continue;
    }
    if (!st.isDirectory()) continue;
    const count = countDedupedAsOfDir(dir, catalog);
    if (count <= 0) continue;
    filings.push({
      as_of: date,
      cadence: cadenceForAsOf(date),
      portfolio_count: count,
    });
  }
  filings.sort((a, b) => String(b.as_of).localeCompare(String(a.as_of)));
  return {
    generated_at: new Date().toISOString(),
    filings,
  };
}

/**
 * Copy newest as-of book per portfolio_id → portfolios/latest/{id}.json
 * for backward-compatible CDN/API consumers that still hit /latest/.
 */
export function mirrorLatestPortfolios(outDir, catalog = null) {
  const asofRoot = join(outDir, "portfolios", "asof");
  const latestDir = join(outDir, "portfolios", "latest");
  mkdirSync(latestDir, { recursive: true });
  const map = scanExistingAsOfDirs(outDir, catalog);
  let mirrored = 0;
  for (const [pid, dates] of map) {
    const latest = [...dates].sort().reverse()[0];
    if (!latest) continue;
    const src = join(asofRoot, latest, `${pid}.json`);
    const dest = join(latestDir, `${pid}.json`);
    if (!existsSync(src)) continue;
    copyFileSync(src, dest);
    mirrored += 1;
  }
  return mirrored;
}

/**
 * Fail fast when catalog points at as-of files that are missing on disk.
 * @returns {{ ok: boolean, missing: Array<{ portfolio_id: string, as_of: string, sample_amfi?: string }> }}
 */
export function assertCatalogPortfolioCoverage(outDir, catalog) {
  const asofRoot = join(outDir, "portfolios", "asof");
  /** @type {Map<string, string>} */
  const sampleAmfi = new Map();
  for (const [code, row] of Object.entries(catalog || {})) {
    if (!row?.has_holdings || !row?.portfolio_id) continue;
    const pid = String(row.portfolio_id);
    if (!sampleAmfi.has(pid)) sampleAmfi.set(pid, code);
  }

  const missing = [];
  const seen = new Set();
  for (const row of Object.values(catalog || {})) {
    if (!row?.has_holdings || !row?.portfolio_id) continue;
    const pid = String(row.portfolio_id);
    if (!/^\d{4,8}$/.test(pid) || seen.has(pid)) continue;
    seen.add(pid);
    const latest = String(row.latest_as_of || "").trim();
    const fallback = Array.isArray(row.available_as_of)
      ? [...row.available_as_of].sort().reverse()[0]
      : "";
    const asOf = latest || fallback;
    if (!asOf) {
      missing.push({ portfolio_id: pid, as_of: "", sample_amfi: sampleAmfi.get(pid) });
      continue;
    }
    const path = join(asofRoot, asOf, `${pid}.json`);
    if (!existsSync(path)) {
      missing.push({ portfolio_id: pid, as_of: asOf, sample_amfi: sampleAmfi.get(pid) });
    }
  }
  return { ok: missing.length === 0, missing };
}
