#!/usr/bin/env node
/**
 * Guards against accidental holdings-data regression (catalog links or on-disk as-of trees).
 */
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import {
  countDedupedAsOfDir,
  countRawAsOfJsonFiles,
  parentPortfolioIds,
  scanExistingAsOfDirs,
} from "./asof-portfolios.mjs";

const AS_OF_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Schemes in catalog with a given as_of in available_as_of. */
export function countCatalogSchemesForDate(catalog, asOf) {
  let n = 0;
  for (const row of Object.values(catalog || {})) {
    const dates = row?.available_as_of;
    if (Array.isArray(dates) && dates.includes(asOf)) n += 1;
  }
  return n;
}

/** Per as-of date: deduped portfolio file counts on disk. */
export function asOfDirCounts(outDir, catalog = null) {
  const root = join(outDir, "portfolios", "asof");
  const counts = new Map();
  if (!existsSync(root)) return counts;
  for (const date of readdirSync(root)) {
    if (!AS_OF_RE.test(date)) continue;
    const dir = join(root, date);
    try {
      if (!statSync(dir).isDirectory()) continue;
    } catch {
      continue;
    }
    counts.set(date, countDedupedAsOfDir(dir, catalog));
  }
  return counts;
}

/** Per as-of date: schemes linked in catalog. */
export function catalogAsOfCounts(catalog) {
  const counts = new Map();
  for (const row of Object.values(catalog || {})) {
    for (const d of row?.available_as_of || []) {
      if (!AS_OF_RE.test(String(d))) continue;
      counts.set(d, (counts.get(d) || 0) + 1);
    }
  }
  return counts;
}

function formatDelta(before, after) {
  return `${before} → ${after} (${after - before >= 0 ? "+" : ""}${after - before})`;
}

/**
 * Fail when catalog or on-disk as-of coverage shrinks vs baseline.
 * @param {{ allowRegression?: boolean, label?: string, syncedDates?: string[], checkCatalogLinks?: boolean }} opts
 */
export function assertNoHoldingsRegression(
  outDir,
  beforeCatalog,
  afterCatalog,
  {
    allowRegression = false,
    label = "sync",
    syncedDates = [],
    checkCatalogLinks = true,
  } = {},
) {
  if (allowRegression) return { ok: true, regressions: [] };

  const synced = new Set(
    (syncedDates || []).map((d) => String(d).trim()).filter((d) => AS_OF_RE.test(d)),
  );

  const beforeDirs = asOfDirCounts(outDir, beforeCatalog);
  const afterDirs = asOfDirCounts(outDir, afterCatalog);
  const beforeCat = catalogAsOfCounts(beforeCatalog);
  const afterCat = catalogAsOfCounts(afterCatalog);

  const regressions = [];
  const allDates = new Set([
    ...beforeDirs.keys(),
    ...beforeCat.keys(),
    ...afterDirs.keys(),
    ...afterCat.keys(),
    ...synced,
  ]);

  for (const date of [...allDates].sort()) {
    const prevFiles = beforeDirs.get(date) || 0;
    const nextFiles = afterDirs.get(date) || 0;
    if (prevFiles > 0 && nextFiles < prevFiles) {
      regressions.push(
        `${date}: portfolio files ${formatDelta(prevFiles, nextFiles)}`,
      );
    }

    // Catalog link checks only for dates being written in this sync.
    // Fortnightly sync rebuilds available_as_of from on-disk files and often
    // drops stale phantom FN stamps — compare portfolio files, not link counts.
    if (checkCatalogLinks && synced.has(date)) {
      const prevSchemes = beforeCat.get(date) || 0;
      const nextSchemes = afterCat.get(date) || 0;
      if (prevSchemes > 0 && nextSchemes < prevSchemes) {
        regressions.push(
          `${date}: catalog scheme links ${formatDelta(prevSchemes, nextSchemes)}`,
        );
      }
    }
  }

  if (regressions.length) {
    const msg =
      `Holdings data regression blocked (${label}):\n` +
      regressions.map((r) => `  - ${r}`).join("\n") +
      "\nRe-run with --allow-regression only if the drop is intentional.";
    throw new Error(msg);
  }
  return { ok: true, regressions: [] };
}

/** Load catalog JSON from repo checkout if present. */
export function loadRepoCatalog(outDir) {
  const p = join(outDir, "catalog/amfi-lookup.json");
  if (!existsSync(p)) return {};
  try {
    return JSON.parse(readFileSync(p, "utf8"));
  } catch {
    return {};
  }
}

/** Load published filings index if present. */
export function loadRepoFilings(outDir) {
  const p = join(outDir, "catalog/filings.json");
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, "utf8"));
  } catch {
    return null;
  }
}

/**
 * Block accidental shrink of catalog/filings.json (UI/API date picker).
 * @param {{ allowRegression?: boolean, label?: string }} opts
 */
export function assertFilingsIndexNotShrunk(beforeDoc, afterDoc, { allowRegression = false, label = "filings" } = {}) {
  if (allowRegression) return { ok: true };
  const before = new Set((beforeDoc?.filings || []).map((r) => String(r.as_of)));
  const after = new Set((afterDoc?.filings || []).map((r) => String(r.as_of)));
  const dropped = [...before].filter((d) => !after.has(d)).sort();
  if (!dropped.length) return { ok: true };
  const msg =
    `Filings index regression blocked (${label}): dropped as-of row(s): ${dropped.join(", ")}.\n` +
    "Re-run with --allow-regression only if those slices were intentionally removed.";
  throw new Error(msg);
}

/** As-of dates that have at least one portfolio JSON on disk. */
export function listAsOfDirsWithPortfolios(outDir) {
  const root = join(outDir, "portfolios", "asof");
  const dates = [];
  if (!existsSync(root)) return dates;
  for (const date of readdirSync(root)) {
    if (!AS_OF_RE.test(date)) continue;
    const dir = join(root, date);
    try {
      if (!statSync(dir).isDirectory()) continue;
    } catch {
      continue;
    }
    if (countRawAsOfJsonFiles(dir) > 0) dates.push(date);
  }
  return dates.sort();
}

/** Top-level as-of folders tracked in git HEAD (when outDir is a clone). */
export function listGitTrackedAsOfDates(outDir) {
  if (!existsSync(join(outDir, ".git"))) return [];
  const res = spawnSync(
    "git",
    ["-C", outDir, "ls-tree", "--name-only", "HEAD:portfolios/asof"],
    { encoding: "utf8" },
  );
  if (res.status !== 0) return [];
  return (res.stdout || "")
    .split("\n")
    .map((s) => s.trim())
    .filter((d) => AS_OF_RE.test(d))
    .sort();
}

/**
 * Materialize every portfolios/asof/* tree from HEAD before rebuilding filings.json.
 * Prevents a sparse/partial working tree from writing a truncated filings index.
 */
export function ensureFullPortfoliosAsOfCheckout(outDir) {
  if (!existsSync(join(outDir, ".git"))) return { ok: true, skipped: true };
  spawnSync("git", ["-C", outDir, "sparse-checkout", "disable"], {
    stdio: "pipe",
  });
  const co = spawnSync(
    "git",
    ["-C", outDir, "checkout", "HEAD", "--", "portfolios/asof"],
    { encoding: "utf8", stdio: "pipe" },
  );
  if (co.status !== 0) {
    console.warn(
      "Warning: could not fully checkout portfolios/asof:",
      (co.stderr || co.stdout || "").slice(0, 240),
    );
  }
  return { ok: co.status === 0 };
}

/**
 * Every on-disk as-of folder with portfolios must appear in catalog/filings.json.
 * OpenFin / holdings-browser read this file — not a scan of portfolios/asof/.
 */
export function assertFilingsCoverOnDisk(
  outDir,
  filingsDoc,
  { allowRegression = false, label = "filings" } = {},
) {
  if (allowRegression) return { ok: true };
  const indexed = new Set((filingsDoc?.filings || []).map((r) => String(r.as_of)));
  const onDisk = listAsOfDirsWithPortfolios(outDir);
  const missing = onDisk.filter((d) => !indexed.has(d));
  if (missing.length) {
    throw new Error(
      `Filings index missing on-disk as-of slice(s) (${label}): ${missing.join(", ")}. ` +
        "Run npm run holdings:refresh-filings -- --push after ensureFullPortfoliosAsOfCheckout.",
    );
  }
  const gitDates = listGitTrackedAsOfDates(outDir);
  const gitOnlyMissing = gitDates.filter((d) => {
    if (indexed.has(d)) return false;
    const dir = join(outDir, "portfolios", "asof", d);
    return existsSync(dir) && countRawAsOfJsonFiles(dir) > 0;
  });
  if (gitOnlyMissing.length) {
    throw new Error(
      `Filings index incomplete vs git as-of tree (${label}): ${gitOnlyMissing.join(", ")}`,
    );
  }
  return {
    ok: true,
    on_disk_slices: onDisk.length,
    index_slices: indexed.size,
    git_slices: gitDates.length,
  };
}

/**
 * Merge available_as_of / latest_as_of from an existing published catalog into a
 * freshly built lookup, keeping only dates whose portfolio files still exist.
 */
export function mergeCatalogAsOfFromRepo(outDir, freshCatalog, repoCatalog) {
  const asOfMap = scanExistingAsOfDirs(outDir, freshCatalog);
  const out = { ...freshCatalog };

  for (const [code, row] of Object.entries(out)) {
    if (!row || typeof row !== "object") continue;
    const pid = String(
      row.portfolio_id || row.parent_amfi || row.amfi_code || "",
    ).trim();
    if (!/^\d{4,8}$/.test(pid)) continue;

    const merged = new Set(asOfMap.get(pid) || []);
    const prev = repoCatalog?.[code];
    for (const d of prev?.available_as_of || []) {
      const day = String(d).trim();
      if (!AS_OF_RE.test(day)) continue;
      const path = join(outDir, "portfolios/asof", day, `${pid}.json`);
      if (existsSync(path)) merged.add(day);
    }

    if (!merged.size) continue;
    const available = [...merged].sort().reverse();
    const latest = available[0] || null;
    out[code] = {
      ...row,
      portfolio_id: row.portfolio_id || pid,
      available_as_of: available,
      latest_as_of: latest,
    };
  }
  return out;
}
