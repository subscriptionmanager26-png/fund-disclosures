#!/usr/bin/env node
/**
 * Drop portfolios/asof folders older than a rolling N-month window and refresh
 * catalog/filings.json + catalog/amfi-lookup.json + meta.json.
 *
 * Usage:
 *   node scripts/prune-retention-asof.mjs --months=3 --dry-run
 *   node scripts/prune-retention-asof.mjs --months=3 --push
 */
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import {
  attachAvailableAsOf,
  buildFilingsFromAsOfDirs,
  scanExistingAsOfDirs,
} from "./lib/asof-portfolios.mjs";
import { defaultHoldingsOutDir } from "./lib/resolve-holdings-out-dir.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");

const OWNER = process.env.HOLDINGS_DATA_OWNER || "kushagra-agarwal-a";
const REPO = process.env.HOLDINGS_DATA_REPO || "fund-holdings-data";
const BRANCH = process.env.HOLDINGS_DATA_BRANCH || "main";

const AS_OF_RE = /^\d{4}-\d{2}-\d{2}$/;

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
const months = Math.max(1, Number(argValue("months", "3")) || 3);
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

function listAsOfDates() {
  const asofRoot = join(outDir, "portfolios", "asof");
  if (!existsSync(asofRoot)) return [];
  return readdirSync(asofRoot)
    .filter((d) => AS_OF_RE.test(d))
    .sort();
}

/** First day of month N months before anchor month (UTC). */
function retentionCutoff(latestMonthEnd, monthCount) {
  const [y, m] = latestMonthEnd.slice(0, 7).split("-").map(Number);
  const anchor = new Date(Date.UTC(y, m - 1, 1));
  anchor.setUTCMonth(anchor.getUTCMonth() - (monthCount - 1));
  const cy = anchor.getUTCFullYear();
  const cm = String(anchor.getUTCMonth() + 1).padStart(2, "0");
  return `${cy}-${cm}-01`;
}

function latestMonthEnd(dates) {
  const monthEnds = dates.filter((d) => {
    const day = Number(d.slice(8, 10));
    const [y, m] = d.slice(0, 7).split("-").map(Number);
    const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
    return day === last;
  });
  if (monthEnds.length) return monthEnds.sort().at(-1);
  return dates.sort().at(-1) || null;
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

const dates = listAsOfDates();
if (!dates.length) {
  console.log("No as-of folders found.");
  process.exit(0);
}

const anchor = latestMonthEnd(dates);
const cutoff = retentionCutoff(anchor, months);
const toDrop = dates.filter((d) => d < cutoff);
const toKeep = dates.filter((d) => d >= cutoff);

console.log(
  JSON.stringify(
    {
      months,
      anchor,
      retention_cutoff: cutoff,
      keep: toKeep,
      drop: toDrop,
      dry_run: dryRun,
      push: doPush,
      out: outDir,
    },
    null,
    2,
  ),
);

if (!toDrop.length) {
  console.log("Nothing to prune.");
  process.exit(0);
}

if (!dryRun) {
  for (const date of toDrop) {
    const dir = join(outDir, "portfolios", "asof", date);
    if (existsSync(dir)) {
      rmSync(dir, { recursive: true, force: true });
      console.log(`Removed portfolios/asof/${date}/`);
    }
  }

  const catalogPath = join(outDir, "catalog/amfi-lookup.json");
  let catalog = {};
  if (existsSync(catalogPath)) {
    try {
      catalog = JSON.parse(readFileSync(catalogPath, "utf8"));
    } catch {
      catalog = {};
    }
  }

  const asOfMap = scanExistingAsOfDirs(outDir, catalog);
  const withDates = attachAvailableAsOf(catalog, asOfMap, { cdnUrlFn: cdnUrl });
  writeJson(catalogPath, withDates);

  const filingsDoc = buildFilingsFromAsOfDirs(outDir, withDates);
  writeJson(join(outDir, "catalog/filings.json"), filingsDoc);

  const metaPath = join(outDir, "meta.json");
  const meta = existsSync(metaPath)
    ? JSON.parse(readFileSync(metaPath, "utf8"))
    : {};
  meta.retention_cutoff = cutoff;
  meta.retention_months = months;
  meta.retention_pruned_at = new Date().toISOString();
  meta.retention_dropped_as_of = toDrop;
  writeJson(metaPath, meta);
}

if (doPush && !dryRun) {
  const commit = pushWithPin(
    `chore(retention): prune as-of before ${cutoff} (${toDrop.length} folder(s))`,
  );
  if (commit) {
    const metaPath = join(outDir, "meta.json");
    const meta = JSON.parse(readFileSync(metaPath, "utf8"));
    meta.commit = commit;
    meta.raw_base = `https://raw.githubusercontent.com/${OWNER}/${REPO}/${commit}`;
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
      `meta: pin retention commit ${commit.slice(0, 7)}`,
    ]);
    run("git", ["-C", outDir, "push", "-u", "origin", `HEAD:${BRANCH}`]);
    console.log(`Pushed → https://github.com/${OWNER}/${REPO}\nPinned → ${commit}`);
  }
}
