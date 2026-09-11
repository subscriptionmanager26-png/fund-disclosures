#!/usr/bin/env node
/**
 * Fetch → parse → sync new portfolios (additive only) + JSON report.
 * Usage: HOLDINGS_GH_TOKEN=... node scripts/run-portfolio-update-report.mjs --push
 */
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");

function hasFlag(n) {
  return process.argv.includes(`--${n}`);
}
function run(cmd, args, label) {
  console.log(`\n→ ${label}`);
  const res = spawnSync(cmd, args, { stdio: "inherit", cwd: ROOT, env: process.env });
  if (res.status !== 0) throw new Error(`${label} failed (${res.status})`);
}

function countAsOfFiles(dir) {
  const counts = {};
  const root = join(dir, "portfolios", "asof");
  if (!existsSync(root)) return counts;
  for (const d of readdirSync(root)) {
    const p = join(root, d);
    try {
      if (!statSync(p).isDirectory()) continue;
    } catch {
      continue;
    }
    counts[d] = readdirSync(p).filter((f) => f.endsWith(".json")).length;
  }
  return counts;
}

function loadFetchProbe(type, storageKey) {
  const p = join(ROOT, "data/probes", `fetch-${type}-${storageKey}.json`);
  if (!existsSync(p)) return null;
  return JSON.parse(readFileSync(p, "utf8"));
}

function summarizeFetch(probe) {
  if (!probe) return { amcs: 0, ok: 0, empty: 0, error: 0, files: 0 };
  const results = probe.results || [];
  return {
    amcs: results.length,
    ok: results.filter((r) => r.status === "ok").length,
    empty: results.filter((r) => r.status === "empty").length,
    error: results.filter((r) => r.status === "error").length,
    files: results.reduce((n, r) => n + (r.fileCount || 0), 0),
    errors: results.filter((r) => r.status === "error").map((r) => r.id),
  };
}

function collectParsedPortfolios(type, period) {
  const base = join(ROOT, "data/parsed", type, period);
  if (!existsSync(base)) return 0;
  let n = 0;
  function walk(d) {
    for (const name of readdirSync(d)) {
      const p = join(d, name);
      const st = statSync(p);
      if (st.isDirectory()) walk(p);
      else if (name === "portfolio.json") n += 1;
    }
  }
  walk(base);
  return n;
}

const doPush = hasFlag("push");
const token = process.env.HOLDINGS_GH_TOKEN || process.env.GH_TOKEN;
if (doPush && !token) {
  console.error("Set HOLDINGS_GH_TOKEN for --push");
  process.exit(1);
}
if (token) process.env.GH_TOKEN = token;

// Load .env for EDELWEISS_API_SECRET etc.
const envPath = join(ROOT, ".env");
if (existsSync(envPath)) {
  for (const line of readFileSync(envPath, "utf8").split("\n")) {
    const m = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
}

const periods = ["2026-08", "2026-09"];
const report = {
  started_at: new Date().toISOString(),
  mode: doPush ? "push" : "dry-run",
  fetch: [],
  parse: [],
  sync: [],
  baseline_files: {},
  after_files: {},
  new_portfolio_files: 0,
  new_as_of_dates: [],
};

// Baseline: clone holdings repo if needed
const outDir = join(ROOT, ".tmp/fund-holdings-data");
if (!existsSync(join(outDir, ".git"))) {
  mkdirSync(dirname(outDir), { recursive: true });
  if (token) {
    run(
      "git",
      [
        "clone",
        "--depth",
        "1",
        `https://x-access-token:${token}@github.com/kushagra-agarwal-a/fund-holdings-data.git`,
        outDir,
      ],
      "clone holdings baseline",
    );
  }
}
if (existsSync(outDir)) {
  report.baseline_files = countAsOfFiles(outDir);
}

for (const period of periods) {
  for (const type of ["monthly", "fortnightly"]) {
    run("npm", ["run", "fetch", "--", `--type=${type}`, `--period=${period}`], `fetch ${type} ${period}`);
    const storageKey = type === "monthly" && /^\d{4}-\d{2}$/.test(period)
      ? `${period}-${String(new Date(Date.UTC(+period.slice(0, 4), +period.slice(5, 7), 0)).getUTCDate()).padStart(2, "0")}`
      : period;
    // fetch writes storageKey in probe filename - read actual from probe dir
    const probes = readdirSync(join(ROOT, "data/probes")).filter((f) =>
      f.startsWith(`fetch-${type}-`) && f.includes(period.replace(/-/g, "-")),
    );
    const latestProbe = probes.sort().pop();
    const probe = latestProbe
      ? JSON.parse(readFileSync(join(ROOT, "data/probes", latestProbe), "utf8"))
      : null;
    report.fetch.push({ type, period, storageKey: probe?.storageKey, ...summarizeFetch(probe) });

    run("npm", ["run", "parse:amc", "--", `--type=${type}`, `--period=${period}`, "--all"], `parse ${type} ${period}`);
    const parsedCount = collectParsedPortfolios(type, probe?.storageKey || period);
    report.parse.push({ type, period, storageKey: probe?.storageKey, portfolios_parsed: parsedCount });
  }
}

run("npm", ["run", "holdings:enrich", "--", "--allow-incomplete"], "enrich");
run("npm", ["run", "holdings:assert-locks"], "assert locks");

// Sync only Aug window with merge on all slices (additive fortnightly; monthly aug-31 is new)
const syncArgs = [
  join(ROOT, "scripts/sync-asof-window.mjs"),
  "--from=2026-08",
  "--to=2026-09",
  "--merge",
  ...(doPush ? ["--push"] : ["--dry-run"]),
];
run(process.execPath, syncArgs, "sync as-of window 2026-08..2026-09");

if (doPush) {
  run(process.execPath, [join(ROOT, "scripts/refresh-filings-catalog.mjs"), "--push"], "refresh filings");
}

if (existsSync(outDir)) {
  report.after_files = countAsOfFiles(outDir);
}
for (const [date, after] of Object.entries(report.after_files)) {
  const before = report.baseline_files[date] || 0;
  if (after > before) {
    report.new_as_of_dates.push({ date, before, after, added: after - before });
    report.new_portfolio_files += after - before;
  }
}

report.finished_at = new Date().toISOString();
report.fetch_totals = report.fetch.reduce(
  (a, r) => ({
    amcs_checked: a.amcs_checked + r.amcs,
    amcs_ok: a.amcs_ok + r.ok,
    amcs_empty: a.amcs_empty + r.empty,
    amcs_error: a.amcs_error + r.error,
    files_downloaded: a.files_downloaded + r.files,
  }),
  { amcs_checked: 0, amcs_ok: 0, amcs_empty: 0, amcs_error: 0, files_downloaded: 0 },
);

mkdirSync(join(ROOT, "data/probes"), { recursive: true });
const reportPath = join(ROOT, "data/probes", `portfolio-update-report-${Date.now()}.json`);
writeFileSync(reportPath, JSON.stringify(report, null, 2));
console.log("\nReport:", reportPath);
console.log(JSON.stringify(report, null, 2));
