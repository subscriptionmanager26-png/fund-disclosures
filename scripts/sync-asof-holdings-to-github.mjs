#!/usr/bin/env node
/**
 * Sync one calendar as-of date into portfolios/asof/YYYY-MM-DD/ and refresh
 * catalog/filings.json on the public fund-holdings-data repo.
 *
 * Usage:
 *   node scripts/sync-asof-holdings-to-github.mjs --asof=2026-08-15 --cadence=fortnightly --dry-run
 *   node scripts/sync-asof-holdings-to-github.mjs --asof=2026-07-15 --cadence=fortnightly --push
 *
 * Month-end dates: fortnightly sync merges (--merge default); monthly sync replaces.
 * Prefer scripts/sync-asof-window.mjs for multi-date pushes in safe order.
 *
 * Auth for --push: GH_TOKEN or GITHUB_TOKEN.
 */
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import {
  attachAvailableAsOf,
  assertCatalogPortfolioCoverage,
  buildFilingsFromAsOfDirs,
  collectAsOfPortfolios,
  mirrorLatestPortfolios,
  normalizeAsOf,
  isMonthEndAsOf,
  pruneOrphanAsOfPortfolios,
  scanExistingAsOfDirs,
  sourcePeriodFromAsOf,
} from "./lib/asof-portfolios.mjs";
import {
  assertNoHoldingsRegression,
  loadRepoCatalog,
} from "./lib/holdings-guard.mjs";
import { defaultHoldingsOutDir } from "./lib/resolve-holdings-out-dir.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");

const { shapeHoldingsPayload } = await import(
  pathToFileURL(join(ROOT, "holdings-browser/api/_contract.js")).href,
);

const OWNER = process.env.HOLDINGS_DATA_OWNER || "kushagra-agarwal-a";
const REPO = process.env.HOLDINGS_DATA_REPO || "fund-holdings-data";
const BRANCH = process.env.HOLDINGS_DATA_BRANCH || "main";

function argValue(name, fallback = null) {
  const prefix = `--${name}=`;
  const hit = process.argv.find((a) => a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : fallback;
}
function hasFlag(name) {
  return process.argv.includes(`--${name}`);
}

const dryRun = hasFlag("dry-run");
const doPush = hasFlag("push");
const allowRegression = hasFlag("allow-regression");
const updateLatest = hasFlag("update-latest");
if (updateLatest) {
  console.warn(
    "Warning: --update-latest is deprecated. Portfolios live under portfolios/asof/ only.",
  );
}

function cdnUrl(objectKey) {
  return `https://cdn.jsdelivr.net/gh/${OWNER}/${REPO}@main/${objectKey}`;
}
const limit = Number(argValue("limit", "0")) || 0;
const asof = normalizeAsOf(argValue("asof", ""));
const cadence = argValue("cadence", "");
const sourcePeriod =
  argValue("source-period", "") || (asof ? sourcePeriodFromAsOf(asof) : "");
const outDir = argValue("out", defaultHoldingsOutDir(ROOT));
const lookupPath = argValue(
  "lookup",
  join(ROOT, "holdings-browser/api/amfi-lookup.json"),
);

if (!asof) {
  console.error("Required: --asof=YYYY-MM-DD");
  process.exit(1);
}
if (!["monthly", "fortnightly"].includes(cadence)) {
  console.error("Required: --cadence=monthly|fortnightly");
  process.exit(1);
}

function ensureDir(p) {
  mkdirSync(p, { recursive: true });
}
function writeJson(filePath, value) {
  ensureDir(dirname(filePath));
  writeFileSync(filePath, `${JSON.stringify(value)}\n`, "utf8");
}
function run(cmd, args) {
  const res = spawnSync(cmd, args, { stdio: "inherit", encoding: "utf8" });
  if (res.status !== 0) {
    throw new Error(`${cmd} ${args.join(" ")} failed (${res.status})`);
  }
}
function gitTokenUrl() {
  const token = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;
  if (!token) throw new Error("Set GH_TOKEN or GITHUB_TOKEN for --push");
  return `https://x-access-token:${token}@github.com/${OWNER}/${REPO}.git`;
}

function initOrClone() {
  if (existsSync(join(outDir, ".git"))) {
    if (doPush) {
      run("git", ["-C", outDir, "remote", "set-url", "origin", gitTokenUrl()]);
      run("git", ["-C", outDir, "fetch", "origin", BRANCH]);
      run("git", ["-C", outDir, "checkout", "-B", BRANCH, `origin/${BRANCH}`]);
    }
    return;
  }
  if (doPush) {
    ensureDir(dirname(outDir));
    const clone = spawnSync(
      "git",
      ["clone", "--branch", BRANCH, "--single-branch", gitTokenUrl(), outDir],
      { encoding: "utf8" },
    );
    if (clone.status === 0) return;
  }
  ensureDir(outDir);
}

function schemeFromMeta(meta, portfolioId) {
  return {
    amfi_code: String(portfolioId),
    name: meta?.scheme_name || meta?.amfi_name || null,
    amc_name: meta?.amc_name || null,
    parent_name: meta?.amfi_name || meta?.scheme_name || null,
    parent_amfi: String(portfolioId),
    shortcode: meta?.shortcode || null,
    as_of: asof,
    source_file: meta?.source_file || null,
  };
}

function refreshFilings(catalog, { baselineCatalog = null } = {}) {
  const beforeCatalog = baselineCatalog || loadRepoCatalog(outDir);
  const asOfMap = scanExistingAsOfDirs(outDir, catalog);
  for (const dates of asOfMap.values()) dates.add(asof);

  const withDates = attachAvailableAsOf(catalog, asOfMap, {
    cdnUrlFn: cdnUrl,
    outDir,
  });
  assertNoHoldingsRegression(outDir, beforeCatalog, withDates, {
    allowRegression,
    label: `sync-asof(${asof})`,
    syncedDates: [asof],
  });
  writeJson(join(outDir, "catalog/amfi-lookup.json"), withDates);

  const doc = buildFilingsFromAsOfDirs(outDir, withDates);
  writeJson(join(outDir, "catalog/filings.json"), doc);

  const coverage = assertCatalogPortfolioCoverage(outDir, withDates);
  if (!coverage.ok) {
    const sample = coverage.missing.slice(0, 8);
    console.warn(
      `Warning: catalog/asof mismatch (${coverage.missing.length}): ` +
        sample.map((m) => `${m.portfolio_id}@${m.as_of || "?"}`).join(", "),
    );
  }

  const mirrored = mirrorLatestPortfolios(outDir, withDates);
  if (mirrored) console.log(`Mirrored ${mirrored} portfolio(s) to portfolios/latest/.`);

  return { catalog: withDates, filings: doc };
}

function pushWithPin(message) {
  run("git", ["-C", outDir, "add", "-A"]);
  const st = spawnSync("git", ["-C", outDir, "status", "--porcelain"], {
    encoding: "utf8",
  });
  if (!st.stdout.trim()) {
    console.log("Nothing to commit.");
    return;
  }
  run("git", [
    "-C",
    outDir,
    "-c",
    "user.email=holdings-bot@users.noreply.github.com",
    "-c",
    "user.name=holdings-sync",
    "commit",
    "-m",
    message,
  ]);
  run("git", ["-C", outDir, "remote", "set-url", "origin", gitTokenUrl()]);
  run("git", ["-C", outDir, "push", "-u", "origin", `HEAD:${BRANCH}`]);

  const shaRes = spawnSync("git", ["-C", outDir, "rev-parse", "HEAD"], {
    encoding: "utf8",
  });
  const commit = (shaRes.stdout || "").trim();
  if (!commit) return;
  const metaPath = join(outDir, "meta.json");
  const meta = existsSync(metaPath)
    ? JSON.parse(readFileSync(metaPath, "utf8"))
    : {};
  meta.commit = commit;
  meta.raw_base = `https://raw.githubusercontent.com/${OWNER}/${REPO}/${commit}`;
  meta.cdn_catalog = `https://cdn.jsdelivr.net/gh/${OWNER}/${REPO}@${commit}/catalog/amfi-lookup.json`;
  meta.cdn_filings = `https://cdn.jsdelivr.net/gh/${OWNER}/${REPO}@${commit}/catalog/filings.json`;
  meta.cdn_portfolio_template = `https://cdn.jsdelivr.net/gh/${OWNER}/${REPO}@${commit}/portfolios/asof/{as_of}/{portfolio_id}.json`;
  meta.cdn_asof_template = `https://cdn.jsdelivr.net/gh/${OWNER}/${REPO}@${commit}/portfolios/asof/{as_of}/{portfolio_id}.json`;
  meta.last_asof_sync = {
    as_of: asof,
    cadence,
    source_period: sourcePeriod,
    update_latest: updateLatest,
  };
  writeJson(metaPath, meta);
  run("git", ["-C", outDir, "add", "meta.json"]);
  run("git", [
    "-C",
    outDir,
    "-c",
    "user.email=holdings-bot@users.noreply.github.com",
    "-c",
    "user.name=holdings-sync",
    "commit",
    "-m",
    `meta: pin content commit ${commit.slice(0, 7)}`,
  ]);
  run("git", ["-C", outDir, "push", "-u", "origin", `HEAD:${BRANCH}`]);
  console.log(`Pushed → https://github.com/${OWNER}/${REPO}\nPinned → ${commit}`);
}

const lookup = JSON.parse(readFileSync(lookupPath, "utf8"));
const collected = collectAsOfPortfolios({
  root: ROOT,
  cadence,
  sourcePeriod,
  asOf: asof,
  catalogLookup: lookup,
});
let entries = [...collected.values()].sort((a, b) =>
  a.portfolio_id.localeCompare(b.portfolio_id),
);
if (limit > 0) entries = entries.slice(0, limit);

console.log(
  JSON.stringify(
    {
      asof,
      cadence,
      source_period: sourcePeriod,
      portfolios_found: collected.size,
      syncing: entries.length,
      update_latest: updateLatest,
      dry_run: dryRun,
      push: doPush,
      out: outDir,
    },
    null,
    2,
  ),
);

if (dryRun) {
  for (const e of entries.slice(0, 10)) {
    console.log(
      `  portfolios/asof/${asof}/${e.portfolio_id}.json ← ${e.local_path}`,
    );
  }
  if (entries.length > 10) console.log(`  … ${entries.length - 10} more`);
  process.exit(0);
}

if (entries.length === 0) {
  console.log("skip: no local portfolios for this slice (nothing to push)");
  process.exit(0);
}

initOrClone();
const repoCatalogBefore = loadRepoCatalog(outDir);

let written = 0;
let failed = 0;
for (const entry of entries) {
  try {
    const portfolio =
      entry.payload ||
      JSON.parse(readFileSync(join(ROOT, entry.local_path), "utf8"));
    const shaped = shapeHoldingsPayload(
      schemeFromMeta(entry.meta, entry.portfolio_id),
      portfolio,
    );
    const payload = {
      portfolio_id: entry.portfolio_id,
      member_amfi_codes: entry.members,
      scheme: shaped.scheme,
      meta: {
        ...shaped.meta,
        portfolio_id: entry.portfolio_id,
        member_count: entry.members.length,
        as_of: asof,
        cadence,
      },
      holdings: shaped.holdings,
    };
    writeJson(
      join(outDir, `portfolios/asof/${asof}/${entry.portfolio_id}.json`),
      payload,
    );
    written += 1;
    if (written % 100 === 0) {
      console.log(`  wrote ${written}/${entries.length}`);
    }
  } catch (err) {
    failed += 1;
    console.error(`  FAIL ${entry.portfolio_id}`, err?.message || err);
  }
}

const pruned = pruneOrphanAsOfPortfolios(
  outDir,
  asof,
  entries.map((e) => e.portfolio_id),
  lookup,
  {
  mergeExisting:
    hasFlag("merge") ||
    cadence === "fortnightly" ||
    (isMonthEndAsOf(asof) && !hasFlag("no-merge")),
  },
);
if (pruned) {
  const mode =
    cadence === "fortnightly" && isMonthEndAsOf(asof) ? "merge" : "replace";
  console.log(`Pruned ${pruned} stale asof file(s) for ${asof} (${mode}).`);
}

let catalog = lookup;
const catalogPath = join(outDir, "catalog/amfi-lookup.json");
if (existsSync(catalogPath)) {
  try {
    catalog = JSON.parse(readFileSync(catalogPath, "utf8"));
  } catch {
    catalog = lookup;
  }
}
const { filings } = refreshFilings(catalog, { baselineCatalog: repoCatalogBefore });

console.log(
  JSON.stringify({ written, failed, filings: filings.filings.length }, null, 2),
);

if (doPush) {
  pushWithPin(`sync(asof=${asof}/${cadence}): ${written} portfolios`);
}
