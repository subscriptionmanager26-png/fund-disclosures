#!/usr/bin/env node
/**
 * Recompute catalog/filings.json from portfolios/asof/* on-disk counts and push.
 * Use after fixing buildFilingsFromAsOfDirs or when filings rows drift from reality.
 *
 *   node scripts/refresh-filings-catalog.mjs --dry-run
 *   node scripts/refresh-filings-catalog.mjs --push
 */
import {
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import {
  attachAvailableAsOf,
  assertCatalogPortfolioCoverage,
  buildFilingsFromAsOfDirs,
  mirrorLatestPortfolios,
  scanExistingAsOfDirs,
} from "./lib/asof-portfolios.mjs";
import {
  assertNoHoldingsRegression,
  loadRepoCatalog,
} from "./lib/holdings-guard.mjs";
import { defaultHoldingsOutDir } from "./lib/resolve-holdings-out-dir.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");

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
const outDir = argValue("out", defaultHoldingsOutDir(ROOT));

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
function cdnUrl(objectKey) {
  return `https://cdn.jsdelivr.net/gh/${OWNER}/${REPO}@main/${objectKey}`;
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

function pushWithPin(message) {
  run("git", ["-C", outDir, "add", "-A"]);
  const st = spawnSync("git", ["-C", outDir, "status", "--porcelain"], {
    encoding: "utf8",
  });
  if (!st.stdout.trim()) {
    console.log("Nothing to commit.");
    return null;
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
  return (shaRes.stdout || "").trim() || null;
}

initOrClone();

const catalogPath = join(outDir, "catalog/amfi-lookup.json");
const baselineCatalog = loadRepoCatalog(outDir);
let catalog = baselineCatalog;
if (!Object.keys(catalog).length && existsSync(catalogPath)) {
  try {
    catalog = JSON.parse(readFileSync(catalogPath, "utf8"));
  } catch {
    catalog = {};
  }
}

const asOfMap = scanExistingAsOfDirs(outDir, catalog);
const withDates = attachAvailableAsOf(catalog, asOfMap, {
  cdnUrlFn: cdnUrl,
  outDir,
});
assertNoHoldingsRegression(outDir, baselineCatalog, withDates, {
  allowRegression,
  label: "refresh-filings-catalog",
});
const filingsDoc = buildFilingsFromAsOfDirs(outDir, withDates);

console.log(JSON.stringify(filingsDoc, null, 2));

if (dryRun) {
  console.log("\nDry run — no files written.");
  process.exit(0);
}

writeJson(catalogPath, withDates);
writeJson(join(outDir, "catalog/filings.json"), filingsDoc);

const coverage = assertCatalogPortfolioCoverage(outDir, withDates);
if (!coverage.ok) {
  const sample = coverage.missing.slice(0, 8);
  const msg =
    `Catalog/asof mismatch: ${coverage.missing.length} portfolio(s) missing on disk. ` +
    `Examples: ${sample.map((m) => `${m.portfolio_id}@${m.as_of || "?"}`).join(", ")}`;
  if (coverage.missing.length > 50) {
    throw new Error(msg);
  }
  console.warn(`Warning: ${msg}`);
}

const mirrored = mirrorLatestPortfolios(outDir, withDates);
if (mirrored) console.log(`Mirrored ${mirrored} portfolio(s) to portfolios/latest/.`);

const metaPath = join(outDir, "meta.json");
const meta = existsSync(metaPath)
  ? JSON.parse(readFileSync(metaPath, "utf8"))
  : {};
meta.filings_count = filingsDoc.filings.length;
meta.filings_refreshed_at = filingsDoc.generated_at;
writeJson(metaPath, meta);

if (doPush) {
  const commit = pushWithPin("fix: rebuild filings.json from on-disk as-of counts");
  if (commit) {
    meta.commit = commit;
    meta.raw_base = `https://raw.githubusercontent.com/${OWNER}/${REPO}/${commit}`;
    meta.cdn_filings = `${meta.raw_base}/catalog/filings.json`;
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
      `meta: pin filings refresh ${commit.slice(0, 7)}`,
    ]);
    run("git", ["-C", outDir, "push", "-u", "origin", `HEAD:${BRANCH}`]);
    console.log(`Pushed → https://github.com/${OWNER}/${REPO}\nPinned → ${commit}`);
  }
} else {
  console.log(`\nWrote ${join(outDir, "catalog/filings.json")} (use --push to publish)`);
}
