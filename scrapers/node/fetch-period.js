#!/usr/bin/env node
/**
 * Repeatable disclosure fetch for a calendar period.
 *
 * Raw files land in date-keyed folders so mid-month and month-end never mix:
 *   fortnightly + --period=2026-07  → data/disclosures/fortnightly/2026-07-15/
 *   monthly     + --period=2026-07  → data/disclosures/monthly/2026-07-31/
 *   explicit    + --period=2026-07-15 → data/disclosures/fortnightly/2026-07-15/
 *
 * Usage:
 *   node scrapers/node/fetch-period.js --type=monthly --period=2026-06
 *   node scrapers/node/fetch-period.js --type=monthly --period=2026-06 --amc=sbi-mutual-fund
 *   node scrapers/node/fetch-period.js --type=monthly --period=2026-06 --list-only
 *   node scrapers/node/fetch-period.js --type=monthly --period=2026-07 --concurrency=12
 *   node scrapers/node/fetch-period.js --adapters
 *
 * AMCs are independent hosts — fetch them in parallel with --concurrency (default 10).
 */
import { existsSync, readFileSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parsePeriod } from "./lib/period.js";
import {
  parsePeriodInput,
  disclosureStorageKey,
} from "./lib/disclosurePeriod.js";
import { downloadDisclosureFile } from "./lib/download.js";
import { getAdapter, listAdapterIds, adapters } from "./adapters/index.js";
import { createPythonRefAdapter } from "./adapters/pythonRef.js";
import { filterFilesForStorageKey } from "./lib/asofFileFilter.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const registry = JSON.parse(
  readFileSync(existsSync(join(root, "registry/amcs.json")) ? join(root, "registry/amcs.json") : join(root, "data/sources/amcs.json"), "utf8"),
);

function arg(name, fallback = undefined) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  if (hit) return hit.slice(name.length + 3);
  if (process.argv.includes(`--${name}`)) return true;
  return fallback;
}

function resolveAdapter(amc, type) {
  const cfg = amc.fetch?.[type];
  const name = cfg?.adapter;
  if (!name || name === "unsupported") return null;
  if (name === "python_ref") {
    if (!cfg.script || !cfg.python_slug) {
      throw new Error(`python_ref requires script + python_slug for ${amc.id}`);
    }
    return createPythonRefAdapter({
      script: cfg.script,
      slug: cfg.python_slug,
      extraArgs: cfg.extra_args || [],
    });
  }
  if (!adapters[name]) throw new Error(`Unknown adapter: ${name}`);
  return getAdapter(name);
}

if (arg("adapters")) {
  console.log("Built-in adapters:");
  for (const id of listAdapterIds()) console.log(`  - ${id}`);
  console.log("  - python_ref (per-AMC script via registry)");
  process.exit(0);
}

const type = arg("type", "monthly");
const period = arg("period");
const amcFilter = arg("amc");
const dryRun = Boolean(arg("dry-run", false));
const listOnly = Boolean(arg("list-only", false));
const cleanDir = Boolean(arg("clean", false));
const supportedOnly = arg("supported-only", true) !== "false";
const concurrency = Math.max(1, Number(arg("concurrency", "10")) || 10);

if (!period) {
  console.error(
    "Required: --period=YYYY-MM or YYYY-MM-DD\nExample: node scrapers/node/fetch-period.js --type=fortnightly --period=2026-07 --list-only",
  );
  process.exit(1);
}
if (!["monthly", "fortnightly"].includes(type)) {
  console.error('--type must be "monthly" or "fortnightly"');
  process.exit(1);
}

const periodInput = String(period);
const parsedInput = parsePeriodInput(periodInput);
const storageKey = disclosureStorageKey(parsedInput, type);
// Adapters match filenames by calendar month (YYYY-MM).
const parsed = parsePeriod(
  parsedInput.isFullDate ? periodInput.slice(0, 7) : periodInput,
);
const amcs = (registry.amcs ?? []).filter((a) => {
  if (amcFilter && a.id !== amcFilter) return false;
  const adapterName = a.fetch?.[type]?.adapter;
  if (supportedOnly && (!adapterName || adapterName === "unsupported"))
    return false;
  return true;
});

if (!amcs.length) {
  console.error("No matching AMCs (need fetch.<type>.adapter in registry).");
  process.exit(1);
}

console.log(
  `Fetch ${type} ${periodInput} → ${storageKey} · ${amcs.length} AMC(s) · concurrency=${concurrency}${dryRun ? " · dry-run" : ""}${listOnly ? " · list-only" : ""}${cleanDir ? " · clean" : ""}\n`,
);

if (cleanDir && !dryRun && !listOnly) {
  const target = join(root, "data/disclosures", type, storageKey);
  if (existsSync(target)) {
    rmSync(target, { recursive: true, force: true });
    console.log(`Cleaned ${target}\n`);
  }
}

const run = {
  ran_at: new Date().toISOString(),
  type,
  period: parsed.period,
  storageKey,
  dryRun,
  listOnly,
  concurrency,
  results: [],
};

const amcTimeoutMs = Math.max(
  60_000,
  (Number(process.env.FETCH_TIMEOUT_MS) || 180_000) + 30_000,
);

function withTimeout(promise, ms, label) {
  let timer;
  return Promise.race([
    promise.finally(() => clearTimeout(timer)),
    new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new Error(`${label} timed out after ${ms}ms`)),
        ms,
      );
    }),
  ]);
}

async function fetchOneAmcInner(amc) {
  const adapterName = amc.fetch?.[type]?.adapter;
  process.stderr.write(`→ ${amc.id} [${adapterName}]\n`);
  try {
    const adapter = resolveAdapter(amc, type);
    if (!adapter) {
      return { id: amc.id, name: amc.name, status: "unsupported" };
    }
    const listed = await adapter.listFiles({
      amc,
      type,
      period: parsed.period,
      storageKey,
    });
    const rawFiles = listed.files ?? [];
    const files = filterFilesForStorageKey(rawFiles, storageKey, type);
    const filteredOut = rawFiles.length - files.length;
    const notesExtra = filteredOut
      ? `filtered ${filteredOut} wrong as-of`
      : "";
    const notes = [listed.notes, notesExtra].filter(Boolean).join(" · ");

    const downloads = [];
    if (!listOnly) {
      for (const f of files) {
        const d = await downloadDisclosureFile({
          root,
          type,
          period: storageKey,
          amcId: amc.id,
          url: f.url,
          filename: f.filename,
          localPath: f.localPath,
          dryRun,
        });
        downloads.push({ ...f, ...d });
        // Small pause between files for the *same* AMC host only.
        await new Promise((r) => setTimeout(r, 100));
      }
    }

    const result = {
      id: amc.id,
      name: amc.name,
      adapter: adapterName,
      status: files.length ? "ok" : "empty",
      notes,
      fileCount: files.length,
      files: listOnly ? files : downloads,
    };
    console.log(
      `  ${amc.name}: ${files.length} file(s)${notes ? ` (${notes})` : ""}`,
    );
    return result;
  } catch (e) {
    console.log(`  ${amc.name}: ERROR ${e.message || e}`);
    return {
      id: amc.id,
      name: amc.name,
      adapter: adapterName,
      status: "error",
      error: String(e.message || e),
    };
  }
}

/** Run async work over items with a fixed worker pool. */
async function mapPool(items, poolSize, fn) {
  const results = new Array(items.length);
  let next = 0;
  async function worker() {
    while (true) {
      const i = next++;
      if (i >= items.length) return;
      results[i] = await fn(items[i], i);
    }
  }
  const n = Math.min(poolSize, items.length);
  await Promise.all(Array.from({ length: n }, () => worker()));
  return results;
}

async function fetchOneAmc(amc) {
  try {
    return await withTimeout(fetchOneAmcInner(amc), amcTimeoutMs, amc.id);
  } catch (e) {
    const adapterName = amc.fetch?.[type]?.adapter;
    console.log(`  ${amc.name}: ERROR ${e.message || e}`);
    return {
      id: amc.id,
      name: amc.name,
      adapter: adapterName,
      status: "error",
      error: String(e.message || e),
    };
  }
}

run.results = await mapPool(amcs, concurrency, fetchOneAmc);

const outDir = join(root, "data/probes");
mkdirSync(outDir, { recursive: true });
const outPath = join(outDir, `fetch-${type}-${storageKey}.json`);
writeFileSync(outPath, JSON.stringify(run, null, 2) + "\n");

const ok = run.results.filter((r) => r.status === "ok").length;
const empty = run.results.filter((r) => r.status === "empty").length;
const err = run.results.filter((r) => r.status === "error").length;
console.log(
  `\nDone. ok=${ok} empty=${empty} error=${err}\nManifest: ${outPath}`,
);
