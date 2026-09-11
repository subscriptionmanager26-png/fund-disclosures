#!/usr/bin/env node
/**
 * Repeatable disclosure fetch for a calendar period.
 *
 * Raw files land in date-keyed folders so mid-month and month-end never mix:
 *   fortnightly + --period=2026-07     → both 2026-07-15 and 2026-07-31
 *   monthly     + --period=2026-07     → data/disclosures/monthly/2026-07-31/
 *   explicit    + --period=2026-07-15  → data/disclosures/fortnightly/2026-07-15/
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
import { spawnSync } from "node:child_process";
import { filterFilesWithReport } from "./lib/portfolioFilter.js";
import { parsePeriod } from "./lib/period.js";
import {
  parsePeriodInput,
  disclosureStorageKey,
  disclosureStorageKeys,
} from "./lib/disclosurePeriod.js";
import { downloadDisclosureFile } from "./lib/download.js";
import { getAdapter, listAdapterIds, adapters } from "./adapters/index.js";
import { createPythonRefAdapter } from "./adapters/pythonRef.js";

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
const amcFilterRaw = arg("amc");
const amcFilter = amcFilterRaw
  ? new Set(
      String(amcFilterRaw)
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    )
  : null;
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
const storageKeys = disclosureStorageKeys(parsedInput, type);

// YYYY-MM fortnightly must fetch BOTH mid-month and month-end slices.
// Re-invoke with explicit YYYY-MM-DD so each slice gets its own folder + probe.
if (!parsedInput.isFullDate && storageKeys.length > 1) {
  let failed = 0;
  const self = fileURLToPath(import.meta.url);
  for (const key of storageKeys) {
    const childArgs = process.argv.slice(2).map((a) =>
      a.startsWith("--period=") ? `--period=${key}` : a,
    );
    if (!childArgs.some((a) => a.startsWith("--period="))) {
      childArgs.push(`--period=${key}`);
    }
    console.log(`\n=== ${type} ${periodInput} → slice ${key} ===\n`);
    const res = spawnSync(process.execPath, [self, ...childArgs], {
      stdio: "inherit",
      cwd: root,
      env: process.env,
    });
    if (res.status) failed = res.status || 1;
  }
  process.exit(failed);
}

const storageKey = disclosureStorageKey(parsedInput, type);
// Adapters match filenames by calendar month (YYYY-MM).
const parsed = parsePeriod(
  parsedInput.isFullDate ? periodInput.slice(0, 7) : periodInput,
);
const amcs = (registry.amcs ?? []).filter((a) => {
  if (amcFilter && !amcFilter.has(a.id)) return false;
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

const listTimeoutMs = Math.max(
  60_000,
  Number(process.env.FETCH_TIMEOUT_MS) || 420_000,
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
    // Timeout covers listing only — downloads of large packs (HSBC ~45 files)
    // must not be killed mid-transfer by a short AMC wall clock.
    const listed = await withTimeout(
      adapter.listFiles({
        amc,
        type,
        period: parsed.period,
        storageKey,
      }),
      listTimeoutMs,
      `${amc.id} listFiles`,
    );
    const rawFiles = listed.files ?? [];
    const listedRejected = Array.isArray(listed.rejected) ? listed.rejected : [];
    const fetchCfg = amc.fetch?.[type] || {};
    const { kept: files, rejected: sliceRejected } = filterFilesWithReport(
      rawFiles,
      {
        type,
        period: { year: parsed.year, month: parsed.month },
        storageKey,
        trustAdapterPeriod: Boolean(fetchCfg.trust_adapter_period),
      },
    );
    const rejected = [
      ...listedRejected.map((r) => ({ ...r, stage: r.stage || "adapter" })),
      ...sliceRejected.map((r) => ({ ...r, stage: "as_of" })),
    ];
    const notesExtra = sliceRejected.length
      ? `rejected ${sliceRejected.length} (see rejection report)`
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

    const failedDl = downloads.filter(
      (d) => d.status && d.status !== "ok" && d.status !== "ok_local" && d.status !== "dry_run",
    );
    const result = {
      id: amc.id,
      name: amc.name,
      adapter: adapterName,
      status: files.length ? (failedDl.length === downloads.length && downloads.length ? "error" : "ok") : "empty",
      notes,
      fileCount: files.length,
      downloadOk: downloads.filter((d) => d.status === "ok" || d.status === "ok_local").length,
      downloadFailed: failedDl.length,
      rejectedCount: rejected.length,
      rejected,
      files: listOnly ? files : downloads,
    };
    console.log(
      `  ${amc.name}: ${files.length} file(s)${notes ? ` (${notes})` : ""}${
        !listOnly && downloads.length
          ? ` · saved ${result.downloadOk}/${downloads.length}`
          : ""
      }`,
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
  // No outer wall-clock kill — listing has listTimeoutMs; downloads run to completion.
  return fetchOneAmcInner(amc);
}

run.results = await mapPool(amcs, concurrency, fetchOneAmc);

const allRejected = [];
for (const r of run.results) {
  for (const row of r.rejected || []) {
    allRejected.push({
      amc_id: r.id,
      amc_name: r.name,
      ...row,
    });
  }
}
run.rejectedCount = allRejected.length;
run.rejected = allRejected;

const outDir = join(root, "data/probes");
mkdirSync(outDir, { recursive: true });
const outPath = join(outDir, `fetch-${type}-${storageKey}.json`);
writeFileSync(outPath, JSON.stringify(run, null, 2) + "\n");

const rejectPath = join(outDir, `fetch-rejections-${type}-${storageKey}.json`);
writeFileSync(
  rejectPath,
  JSON.stringify(
    {
      ran_at: run.ran_at,
      type,
      period: parsed.period,
      storageKey,
      count: allRejected.length,
      rejected: allRejected,
    },
    null,
    2,
  ) + "\n",
);

const mdLines = [
  `# Fetch rejections — ${type} ${storageKey}`,
  "",
  `Generated ${run.ran_at}. ${allRejected.length} file(s) listed on a disclosure page but not downloaded.`,
  "",
  "| AMC | File | Reason | Detail | Stage |",
  "|-----|------|--------|--------|-------|",
];
for (const row of allRejected) {
  const name = (row.filename || row.url || "").replace(/\|/g, "\\|");
  mdLines.push(
    `| ${row.amc_id} | ${name} | ${row.reason} | ${String(row.detail || "").replace(/\|/g, "\\|")} | ${row.stage || ""} |`,
  );
}
const rejectMd = join(outDir, `fetch-rejections-${type}-${storageKey}.md`);
writeFileSync(rejectMd, mdLines.join("\n") + "\n");

const ok = run.results.filter((r) => r.status === "ok").length;
const empty = run.results.filter((r) => r.status === "empty").length;
const err = run.results.filter((r) => r.status === "error").length;
console.log(
  `\nDone. ok=${ok} empty=${empty} error=${err} rejected=${allRejected.length}\nManifest: ${outPath}\nRejections: ${rejectMd}`,
);
