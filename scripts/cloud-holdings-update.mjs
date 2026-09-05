#!/usr/bin/env node
/**
 * Daily Cursor Cloud entrypoint: fetch → parse → enrich → merge-sync → filings.
 *
 *   node scripts/cloud-holdings-update.mjs           # dry run
 *   node scripts/cloud-holdings-update.mjs --push      # publish to GitHub
 *
 * Designed for kushagra-agarwal-a/fund-holdings-data monorepo (run from pipeline/).
 */
import {
  existsSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
  mkdirSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { defaultHoldingsOutDir } from "./lib/resolve-holdings-out-dir.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");

function hasFlag(name) {
  return process.argv.includes(`--${name}`);
}

function loadDotenv() {
  const envPath = join(ROOT, ".env");
  if (!existsSync(envPath)) return;
  for (const line of readFileSync(envPath, "utf8").split("\n")) {
    const m = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (m && !process.env[m[1]]) {
      process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
  }
}

function run(cmd, args, { label, allowFail = false } = {}) {
  if (label) console.log(`\n→ ${label}`);
  const res = spawnSync(cmd, args, { stdio: "inherit", cwd: ROOT, env: process.env });
  if (res.status !== 0 && !allowFail) {
    throw new Error(`${label || cmd} failed (${res.status})`);
  }
  return res.status ?? 1;
}

function monthYm(d = new Date()) {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  return `${y}-${m}`;
}

function shiftYm(ym, delta) {
  const [y, m] = ym.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return monthYm(d);
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

function loadFetchProbe(type, period) {
  const probes = readdirSync(join(ROOT, "data/probes"))
    .filter((f) => f.startsWith(`fetch-${type}-`))
    .filter((f) => f.includes(period.slice(0, 7)));
  const latest = probes.sort().pop();
  if (!latest) return null;
  return JSON.parse(readFileSync(join(ROOT, "data/probes", latest), "utf8"));
}

function summarizeFetch(probe) {
  if (!probe) return { amcs: 0, ok: 0, empty: 0, error: 0, files: 0, errors: [] };
  const results = probe.results || [];
  return {
    storageKey: probe.storageKey,
    amcs: results.length,
    ok: results.filter((r) => r.status === "ok").length,
    empty: results.filter((r) => r.status === "empty").length,
    error: results.filter((r) => r.status === "error").length,
    files: results.reduce((n, r) => n + (r.fileCount || 0), 0),
    errors: results.filter((r) => r.status === "error").map((r) => r.id),
  };
}

loadDotenv();

const doPush = hasFlag("push");
// Cloud Agents inject GH_TOKEN / GITHUB_TOKEN as cursor[bot], which cannot
// write to kushagra-agarwal-a/fund-holdings-data. Push must use the PAT only.
const holdingsToken = process.env.HOLDINGS_GH_TOKEN;
if (doPush && !holdingsToken) {
  console.error(
    "Set HOLDINGS_GH_TOKEN (kushagra-agarwal-a PAT with repo write on fund-holdings-data).\n" +
      "Do not use Cursor's GH_TOKEN — it is cursor[bot] and gets 403 on the data repo.",
  );
  process.exit(1);
}
if (holdingsToken) {
  process.env.GH_TOKEN = holdingsToken;
  process.env.GITHUB_TOKEN = holdingsToken;
}
if (!process.env.EDELWEISS_API_SECRET) {
  console.warn("Warning: EDELWEISS_API_SECRET is not set — Edelweiss fetches will be skipped.");
}

// Fewer parallel AMC fetches + longer HTTP timeout reduces false "error" from timeouts.
process.env.FETCH_TIMEOUT_MS = process.env.FETCH_TIMEOUT_MS || "180000";
const fetchConcurrency = process.env.FETCH_CONCURRENCY || "4";

const toYm = monthYm();
// Daily job: previous + current month only. Older months are already on GitHub.
const fromYm = shiftYm(toYm, -1);
const periods = [fromYm, toYm].filter((v, i, a) => a.indexOf(v) === i);

const outDir = defaultHoldingsOutDir(ROOT);
const report = {
  started_at: new Date().toISOString(),
  mode: doPush ? "push" : "dry-run",
  holdings_out: outDir,
  periods,
  fetch: [],
  baseline_files: countAsOfFiles(outDir),
  after_files: {},
  new_as_of_dates: [],
  new_portfolio_files: 0,
  fetch_totals: null,
  openfin_filings: null,
  push_error: null,
  edelweiss_secret_present: Boolean(process.env.EDELWEISS_API_SECRET),
  holdings_token_present: Boolean(holdingsToken),
};

console.log(
  JSON.stringify(
    {
      mode: report.mode,
      periods,
      fetch_timeout_ms: Number(process.env.FETCH_TIMEOUT_MS),
      fetch_concurrency: Number(fetchConcurrency),
      holdings_owner: "kushagra-agarwal-a",
      holdings_out: outDir,
    },
    null,
    2,
  ),
);

for (const period of periods) {
  for (const type of ["monthly", "fortnightly"]) {
    run(
      "npm",
      [
        "run",
        "fetch",
        "--",
        `--type=${type}`,
        `--period=${period}`,
        `--concurrency=${fetchConcurrency}`,
      ],
      { label: `fetch ${type} ${period}` },
    );
    const probe = loadFetchProbe(type, period);
    report.fetch.push({ type, period, ...summarizeFetch(probe) });

    run(
      "npm",
      ["run", "parse:amc", "--", `--type=${type}`, `--period=${period}`, "--all"],
      { label: `parse ${type} ${period}` },
    );
  }
}

run("npm", ["run", "holdings:enrich", "--", "--allow-incomplete"], {
  label: "enrich identifiers",
});
run("npm", ["run", "holdings:assert-locks"], { label: "assert mapping locks" });

try {
  const syncArgs = [
    join(ROOT, "scripts/sync-asof-window.mjs"),
    `--from=${fromYm}`,
    `--to=${toYm}`,
    "--merge",
    ...(doPush ? ["--push"] : ["--dry-run"]),
  ];
  run(process.execPath, syncArgs, { label: `sync window ${fromYm}..${toYm} (merge)` });

  if (doPush) {
    run(process.execPath, [join(ROOT, "scripts/refresh-filings-catalog.mjs"), "--push"], {
      label: "refresh filings catalog",
    });
  }
} catch (e) {
  report.push_error = String(e.message || e);
  console.error("\nSync/push failed:", report.push_error);
}

report.after_files = countAsOfFiles(outDir);
for (const [date, after] of Object.entries(report.after_files)) {
  const before = report.baseline_files[date] || 0;
  if (after > before) {
    report.new_as_of_dates.push({ date, before, after, added: after - before });
    report.new_portfolio_files += after - before;
  }
}

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

const verify = spawnSync(
  "curl",
  ["-sS", "https://openfin.pocketedge.in/api/v1/filings", "-H", "Cache-Control: no-cache"],
  { encoding: "utf8" },
);
if (verify.status === 0) {
  try {
    const body = JSON.parse(verify.stdout || "{}");
    report.openfin_filings = body.filings || [];
    console.log("\nOpenFin filings:", JSON.stringify(report.openfin_filings, null, 2));
  } catch {
    report.openfin_filings = { raw: (verify.stdout || "").slice(0, 500) };
  }
} else {
  console.warn("Warning: could not verify OpenFin filings API");
}

report.finished_at = new Date().toISOString();
mkdirSync(join(ROOT, "data/probes"), { recursive: true });
const reportPath = join(ROOT, "data/probes", `cloud-holdings-report-${Date.now()}.json`);
writeFileSync(reportPath, JSON.stringify(report, null, 2) + "\n");
console.log("\nReport:", reportPath);
console.log(JSON.stringify(report, null, 2));
if (report.push_error) {
  console.error("\ncloud-holdings-update: finished with push failure");
  process.exit(1);
}
console.log("\ncloud-holdings-update: done");
