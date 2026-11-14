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
  copyFileSync,
  createWriteStream,
  existsSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
  mkdirSync,
} from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn, spawnSync } from "node:child_process";
import { ensureFullPortfoliosAsOfCheckout } from "./lib/holdings-guard.mjs";
import { defaultHoldingsOutDir } from "./lib/resolve-holdings-out-dir.mjs";
import { buildCompleteness, formatCompleteness } from "./lib/completeness.mjs";
import { isSweepDay, planFetchJobs } from "./lib/fetch-window.mjs";
import {
  MANIFEST_REL,
  amcsToParse,
  loadManifest,
  parserFingerprint,
  sliceDigests,
  unchangedUrlCache,
  updateManifest,
  writeManifest,
} from "./lib/parse-manifest.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const PROBES = join(ROOT, "data/probes");

/**
 * Where run evidence goes. Cursor Cloud Agents expose /opt/cursor/artifacts
 * through the API (GET /v0/agents/{id}/artifacts), so traces survive the VM.
 */
function artifactsDir() {
  if (process.env.HOLDINGS_ARTIFACTS_DIR) return process.env.HOLDINGS_ARTIFACTS_DIR;
  if (existsSync("/opt/cursor/artifacts")) return "/opt/cursor/artifacts";
  return join(PROBES, "artifacts");
}

/** Copy this run's log + probes (fetch, rejections, quarantine, report) to artifacts. */
function exportArtifacts(sinceMs, logPath) {
  const dest = artifactsDir();
  try {
    mkdirSync(dest, { recursive: true });
  } catch (e) {
    console.error(`Could not create artifacts dir ${dest}: ${e.message || e}`);
    return;
  }
  const copied = [];
  const files = existsSync(PROBES)
    ? readdirSync(PROBES)
        .filter((f) => /^(fetch-|fetch-rejections-|quarantine-|cloud-holdings-report-)/.test(f))
        .map((f) => join(PROBES, f))
    : [];
  if (logPath) files.push(logPath);
  for (const src of files) {
    try {
      if (!existsSync(src) || statSync(src).mtimeMs < sinceMs - 1000) continue;
      copyFileSync(src, join(dest, basename(src)));
      copied.push(basename(src));
    } catch (e) {
      console.error(`artifact copy failed for ${src}: ${e.message || e}`);
    }
  }
  console.log(`\nRun evidence → ${dest} (${copied.length} file(s))`);
}

// Re-run self as a child so every line (including npm/python subprocesses) is
// tee'd to a log file, and artifacts are exported even if the child crashes.
if (!process.env.HOLDINGS_CLOUD_CHILD) {
  const started = Date.now();
  mkdirSync(PROBES, { recursive: true });
  const logPath = join(PROBES, `holdings-cloud-run-${started}.log`);
  const log = createWriteStream(logPath);
  const child = spawn(process.execPath, [fileURLToPath(import.meta.url), ...process.argv.slice(2)], {
    cwd: ROOT,
    env: { ...process.env, HOLDINGS_CLOUD_CHILD: "1", HOLDINGS_CLOUD_LOG: logPath },
    stdio: ["inherit", "pipe", "pipe"],
  });
  child.stdout.on("data", (d) => {
    process.stdout.write(d);
    log.write(d);
  });
  child.stderr.on("data", (d) => {
    process.stderr.write(d);
    log.write(d);
  });
  const code = await new Promise((resolve) => child.on("close", (c) => resolve(c ?? 1)));
  await new Promise((resolve) => log.end(resolve));
  exportArtifacts(started, logPath);
  process.exit(code);
}

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

/** Wall-clock per step, written to report.timings so slow steps are measured, not guessed. */
const STEP_TIMINGS = [];

function run(cmd, args, { label, allowFail = false } = {}) {
  if (label) console.log(`\n→ ${label}  [${new Date().toISOString()}]`);
  const t0 = Date.now();
  const res = spawnSync(cmd, args, { stdio: "inherit", cwd: ROOT, env: process.env });
  const seconds = Math.round((Date.now() - t0) / 100) / 10;
  STEP_TIMINGS.push({ step: label || `${cmd} ${args[0] || ""}`.trim(), seconds, exit: res.status ?? null });
  if (label) console.log(`← ${label}: ${seconds}s (exit ${res.status ?? "?"})`);
  if (res.status !== 0 && !allowFail) {
    throw new Error(`${label || cmd} failed (${res.status})`);
  }
  return res.status ?? 1;
}

/** Like run(), but captures stdout (still echoed) for small JSON summaries. */
function runCapture(cmd, args, { label } = {}) {
  if (label) console.log(`\n→ ${label}  [${new Date().toISOString()}]`);
  const t0 = Date.now();
  const res = spawnSync(cmd, args, {
    stdio: ["inherit", "pipe", "inherit"],
    cwd: ROOT,
    env: process.env,
    encoding: "utf8",
    maxBuffer: 256 * 1024 * 1024,
  });
  const seconds = Math.round((Date.now() - t0) / 100) / 10;
  STEP_TIMINGS.push({ step: label || cmd, seconds, exit: res.status ?? null });
  if (res.stdout) process.stdout.write(res.stdout);
  if (label) console.log(`← ${label}: ${seconds}s (exit ${res.status ?? "?"})`);
  if (res.status !== 0) throw new Error(`${label || cmd} failed (${res.status})`);
  return res.stdout || "";
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
  const probesDir = join(ROOT, "data/probes");
  if (!existsSync(probesDir)) return null;
  const exact = join(probesDir, `fetch-${type}-${period}.json`);
  if (existsSync(exact)) {
    return JSON.parse(readFileSync(exact, "utf8"));
  }
  // Legacy YYYY-MM probes / fallbacks
  const probes = readdirSync(probesDir)
    .filter((f) => f.startsWith(`fetch-${type}-`))
    .filter((f) => f.includes(period.slice(0, 7)));
  const latest = probes.sort().pop();
  if (!latest) return null;
  return JSON.parse(readFileSync(join(probesDir, latest), "utf8"));
}

function summarizeFetch(probe, quarantine = null) {
  if (!probe) {
    return {
      amcs: 0, ok: 0, empty: 0, needs_review: 0, error: 0, files: 0, rejected: 0,
      quarantine_promoted: 0, quarantine_unresolved: 0, errors: [], empty_amcs: [], amc_errors: {},
    };
  }
  const results = probe.results || [];
  const ids = (s) => results.filter((r) => r.status === s).map((r) => r.id);
  const amcErrors = {};
  for (const r of results) {
    if (r.status === "error") amcErrors[r.id] = String(r.error || `download_failed=${r.downloadFailed ?? "?"}`).slice(0, 300);
  }
  return {
    storageKey: probe.storageKey,
    amcs: results.length,
    ok: ids("ok").length,
    unchanged: ids("unchanged").length,
    empty: ids("empty").length,
    needs_review: ids("needs_review").length,
    error: ids("error").length,
    files: results.reduce((n, r) => n + (r.fileCount || 0), 0),
    rejected: probe.rejectedCount || results.reduce((n, r) => n + (r.rejectedCount || 0), 0),
    quarantine_promoted: quarantine?.promoted || 0,
    quarantine_unresolved: quarantine?.kept_quarantined || 0,
    errors: ids("error"),
    // Named, so "6 empty" is actionable without opening the VM.
    empty_amcs: ids("empty"),
    needs_review_amcs: ids("needs_review"),
    amc_errors: amcErrors,
    slowest_amcs: results
      .filter((r) => typeof r.seconds === "number")
      .sort((a, b) => b.seconds - a.seconds)
      .slice(0, 5)
      .map((r) => `${r.id} ${r.seconds}s`),
  };
}

function pythonBin() {
  const venv = join(ROOT, ".venv/bin/python3");
  return existsSync(venv) ? venv : "python3";
}

function loadJsonIfExists(path) {
  try {
    return existsSync(path) ? JSON.parse(readFileSync(path, "utf8")) : null;
  } catch {
    return null;
  }
}

loadDotenv();

/**
 * Cloud Agent secrets are sometimes saved with different casing
 * (e.g. "cloudflare_account_id", "cloudflare_API_token"), which the fetchers
 * never see — Navi was blocked on every run for that reason. Copy any
 * case-insensitive match onto the canonical name. Values are never logged.
 */
const CANONICAL_SECRETS = [
  "HOLDINGS_GH_TOKEN",
  "CLOUDFLARE_ACCOUNT_ID",
  "CLOUDFLARE_API_TOKEN",
  "UNION_WEBSHARE_PROXY_USER",
  "UNION_WEBSHARE_PROXY_PASSWORD",
  "UNION_WEBSHARE_PROXY_HOSTS",
  "UNION_WEBSHARE_PROXY_URL",
  "EDELWEISS_API_SECRET",
  "EDELWEISS_HASH_KEY",
  "NAVI_WP_NONCE",
];
const secretAliases = [];
for (const name of CANONICAL_SECRETS) {
  if (process.env[name]) continue;
  const alias = Object.keys(process.env).find((k) => k !== name && k.toUpperCase() === name && process.env[k]);
  if (alias) {
    process.env[name] = process.env[alias];
    secretAliases.push(`${alias} → ${name}`);
  }
}
if (secretAliases.length) console.log(`Secret name aliases applied: ${secretAliases.join(", ")}`);
const secretsPresent = Object.fromEntries(CANONICAL_SECRETS.map((n) => [n, Boolean(process.env[n])]));

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
if (
  process.env.EDELWEISS_API_SECRET?.trim() &&
  !process.env.EDELWEISS_HASH_KEY?.trim()
) {
  console.warn(
    "Warning: clearing stale EDELWEISS_API_SECRET (set EDELWEISS_HASH_KEY too to override SPA keys).",
  );
  delete process.env.EDELWEISS_API_SECRET;
}
if (!process.env.EDELWEISS_API_SECRET) {
  console.warn(
    "Note: Edelweiss uses bundled SPA keys when EDELWEISS_API_SECRET is unset.",
  );
}

// Per-request HTTP timeout (each httpFetch). List pagination uses FETCH_LIST_TIMEOUT_MS.
process.env.FETCH_TIMEOUT_MS = process.env.FETCH_TIMEOUT_MS || "180000";
process.env.FETCH_LIST_TIMEOUT_MS =
  process.env.FETCH_LIST_TIMEOUT_MS || "900000";
// AMCs are different hosts; errored AMCs are retried serially by fetch-period,
// so moderate parallelism no longer trades speed for false errors.
const fetchConcurrency = process.env.FETCH_CONCURRENCY || "4";
const monthlyFetchConcurrency = process.env.FETCH_MONTHLY_CONCURRENCY || "3";
const parseWorkers = String(
  Math.max(1, Number(process.env.PARSE_WORKERS) || Math.min(4, (await import("node:os")).cpus().length || 1)),
);
const HOLDINGS_OWNER = process.env.HOLDINGS_DATA_OWNER || "kushagra-agarwal-a";
const HOLDINGS_REPO = process.env.HOLDINGS_DATA_REPO || "fund-holdings-data";
const HOLDINGS_BRANCH = process.env.HOLDINGS_DATA_BRANCH || "main";

function monthEndIso(ym) {
  const [y, m] = ym.split("-").map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return `${ym}-${String(last).padStart(2, "0")}`;
}

/** Fetch+parse jobs: FN mid-month, FN month-end, then monthly — never skip FN-31. */
function buildFetchParseJobs(yms) {
  const jobs = [];
  for (const ym of yms) {
    jobs.push({ type: "fortnightly", period: `${ym}-15` });
    jobs.push({ type: "fortnightly", period: monthEndIso(ym) });
    jobs.push({ type: "monthly", period: monthEndIso(ym) });
  }
  return jobs;
}

function todayUtcIso() {
  const d = new Date();
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Monthly month-end disclosures are not published before calendar month-end. */
function isFutureMonthEnd(periodIso) {
  return /^\d{4}-\d{2}-\d{2}$/.test(periodIso) && periodIso > todayUtcIso();
}

const toYm = monthYm();
// Daily job: previous + current month only. Older months are already on GitHub.
const fromYm = shiftYm(toYm, -1);
const periods = [fromYm, toYm].filter((v, i, a) => a.indexOf(v) === i);
const fetchJobs = buildFetchParseJobs(periods);
const today = todayUtcIso();
const sweep = isSweepDay(today, {
  weekday: process.env.HOLDINGS_SWEEP_WEEKDAY === undefined ? 0 : Number(process.env.HOLDINGS_SWEEP_WEEKDAY),
  force: hasFlag("full") || process.env.HOLDINGS_FULL_SWEEP === "1",
});
const fetchPlan = planFetchJobs(fetchJobs, {
  today,
  sweep,
  windowDays: process.env.FETCH_WINDOW_DAYS ? Number(process.env.FETCH_WINDOW_DAYS) : undefined,
});

const outDir = defaultHoldingsOutDir(ROOT);
// Clone the data repo up front (shallow: the daily job never needs its history)
// so the parse manifest is available before deciding what to parse.
if (doPush && !existsSync(join(outDir, ".git"))) {
  mkdirSync(dirname(outDir), { recursive: true });
  run(
    "git",
    [
      "clone",
      "--depth=1",
      "--branch",
      HOLDINGS_BRANCH,
      "--single-branch",
      `https://x-access-token:${holdingsToken}@github.com/${HOLDINGS_OWNER}/${HOLDINGS_REPO}.git`,
      outDir,
    ],
    { label: "clone holdings data (shallow)", allowFail: true },
  );
}
if (existsSync(join(outDir, ".git"))) {
  ensureFullPortfoliosAsOfCheckout(outDir);
}
const fingerprint = parserFingerprint(ROOT);
const parseManifest = loadManifest(outDir);
const forceParse = hasFlag("force-parse") || process.env.HOLDINGS_FORCE_PARSE === "1";
const report = {
  started_at: new Date().toISOString(),
  mode: doPush ? "push" : "dry-run",
  holdings_out: outDir,
  periods,
  fetch_jobs: fetchJobs,
  fetch_plan: fetchPlan,
  sweep,
  parser_fingerprint: fingerprint,
  parse: [],
  fetch: [],
  baseline_files: countAsOfFiles(outDir),
  after_files: {},
  new_as_of_dates: [],
  new_portfolio_files: 0,
  fetch_totals: null,
  openfin_filings: null,
  push_error: null,
  edelweiss_secret_present: Boolean(process.env.EDELWEISS_API_SECRET),
  secrets_present: secretsPresent,
  secret_aliases: secretAliases,
  holdings_token_present: Boolean(holdingsToken),
};

console.log(
  JSON.stringify(
    {
      mode: report.mode,
      periods,
      fetch_plan: fetchPlan.map((j) => `${j.type} ${j.period}: ${j.skip || "fetch"} (until ${j.fetch_until})`),
      sweep,
      parse_workers: Number(parseWorkers),
      fetch_timeout_ms: Number(process.env.FETCH_TIMEOUT_MS),
      fetch_concurrency: Number(fetchConcurrency),
      fetch_monthly_concurrency: Number(monthlyFetchConcurrency),
      fetch_list_timeout_ms: Number(process.env.FETCH_LIST_TIMEOUT_MS),
      holdings_owner: "kushagra-agarwal-a",
      holdings_out: outDir,
    },
    null,
    2,
  ),
);

const probes = [];
const quarantineReports = [];
const parsedRecords = [];
for (const { type, period, skip, fetch_until } of fetchPlan) {
  if (skip) {
    console.log(`\n→ skip fetch ${type} ${period} (${skip}; fetch window until ${fetch_until})`);
    report.fetch.push({
      type,
      period,
      skipped: true,
      reason: skip,
      fetch_until,
      ...summarizeFetch(null),
    });
    continue;
  }

  const jobConcurrency =
    type === "monthly" ? monthlyFetchConcurrency : fetchConcurrency;
  // Skip downloading AMCs whose file list matches what was last published
  // (not on full sweeps, which re-download everything to catch revisions).
  const key = `${type}/${period}`;
  const urlCache = sweep || forceParse ? {} : unchangedUrlCache(parseManifest, fingerprint, key);
  const cachePath = join(PROBES, `fetch-cache-${type}-${period}.json`);
  mkdirSync(PROBES, { recursive: true });
  writeFileSync(cachePath, JSON.stringify(urlCache) + "\n");
  process.env.FETCH_UNCHANGED_CACHE = cachePath;
  run(
    "npm",
    [
      "run",
      "fetch",
      "--",
      `--type=${type}`,
      `--period=${period}`,
      `--concurrency=${jobConcurrency}`,
    ],
    { label: `fetch ${type} ${period} (concurrency=${jobConcurrency})` },
  );
  // Undated files were downloaded to data/quarantine/; promote the ones whose
  // in-workbook as-of matches this slice so they are parsed below.
  run(
    pythonBin(),
    [join(ROOT, "scrapers/python/resolve_quarantine.py"), `--type=${type}`, `--period=${period}`],
    { label: `resolve quarantine ${type} ${period}`, allowFail: true },
  );
  const quarantine = loadJsonIfExists(join(PROBES, `quarantine-${type}-${period}.json`));
  delete process.env.FETCH_UNCHANGED_CACHE;
  const probe = loadFetchProbe(type, period);
  probes.push(probe);
  const urlsByAmc = Object.fromEntries(
    (probe?.results || []).filter((r) => r.status === "ok" && r.urlsHash).map((r) => [r.id, r.urlsHash]),
  );
  if (quarantine) quarantineReports.push(quarantine);
  report.fetch.push({ type, period, ...summarizeFetch(probe, quarantine) });

  // Incremental parse: only AMCs whose source files changed since they were
  // last parsed + published with this code (see scripts/lib/parse-manifest.mjs).
  // AMCs skipped as "unchanged" have no files on disk, so they are not parsed.
  const digests = sliceDigests(ROOT, type, period);
  const decision = forceParse
    ? { amcs: Object.keys(digests).sort(), reason: "forced" }
    : amcsToParse(parseManifest, fingerprint, key, digests);
  report.parse.push({
    type,
    period,
    amcs_with_files: Object.keys(digests).length,
    reason: decision.reason,
    parsed: decision.amcs,
  });
  if (!decision.amcs.length) {
    console.log(`\n→ skip parse ${type} ${period}: all ${Object.keys(digests).length} AMC(s) unchanged since last publish`);
    continue;
  }
  let summary = null;
  try {
    const out = runCapture(
      pythonBin(),
      [
        join(ROOT, "parsers/run_amc_parser.py"),
        "--all",
        `--type=${type}`,
        `--period=${period}`,
        `--only=${decision.amcs.join(",")}`,
        `--workers=${parseWorkers}`,
        "--summary-only",
      ],
      { label: `parse ${type} ${period} (${decision.amcs.length} AMC(s), ${decision.reason}, workers=${parseWorkers})` },
    );
    // --summary-only prints one JSON object last; earlier lines may be parser chatter.
    const start = out.lastIndexOf('{\n  "amcs"');
    summary = JSON.parse(out.slice(start >= 0 ? start : out.indexOf("{")));
  } catch (e) {
    console.error(`parse ${type} ${period} failed: ${e.message || e}`);
  }
  const okAmcs = (summary?.rows || [])
    .filter((r) => r.schemes > 0 && r.errors === 0)
    .map((r) => r.amc_id);
  parsedRecords.push({ key, digests, amcs: okAmcs, urls: urlsByAmc });
}

const anythingParsed = report.parse.some((p) => p.parsed.length);
if (!anythingParsed) {
  report.publish_skipped = "nothing_changed";
  console.log("\n→ nothing new to parse — skipping enrich / sync / filings refresh");
}

if (anythingParsed) {
run("npm", ["run", "holdings:enrich", "--", "--allow-incomplete"], {
  label: "enrich identifiers",
});
run("npm", ["run", "holdings:assert-locks"], { label: "assert mapping locks" });
}

let syncWindowFailed = false;
if (anythingParsed) try {
  const syncArgs = [
    join(ROOT, "scripts/sync-asof-window.mjs"),
    `--from=${fromYm}`,
    `--to=${toYm}`,
    "--merge",
    "--continue-on-error",
    ...(doPush ? ["--push"] : ["--dry-run"]),
  ];
  run(process.execPath, syncArgs, { label: `sync window ${fromYm}..${toYm} (merge)` });
} catch (e) {
  syncWindowFailed = true;
  report.push_error = String(e.message || e);
  console.error("\nSync window failed:", report.push_error);
}

if (anythingParsed && syncWindowFailed && doPush) {
  for (const ym of periods) {
    const monthlyAsOf = monthEndIso(ym);
    if (isFutureMonthEnd(monthlyAsOf)) continue;
    try {
      run(
        process.execPath,
        [
          join(ROOT, "scripts/sync-asof-holdings-to-github.mjs"),
          `--asof=${monthlyAsOf}`,
          "--cadence=monthly",
          "--push",
        ],
        { label: `fallback sync monthly ${monthlyAsOf}` },
      );
      report.push_error = null;
    } catch (e2) {
      report.push_error = String(e2.message || e2);
      console.error("Fallback monthly sync failed:", report.push_error);
    }
  }
}

try {
  if (doPush && anythingParsed) {
    run(process.execPath, [join(ROOT, "scripts/refresh-filings-catalog.mjs"), "--push"], {
      label: "refresh filings catalog",
    });
    run(
      process.execPath,
      [join(ROOT, "scripts/verify-filings-index.mjs"), `--out=${outDir}`],
      { label: "verify filings index vs on-disk as-of" },
    );
  }
} catch (e) {
  if (!report.push_error) report.push_error = String(e.message || e);
  console.error("\nFilings refresh/verify failed:", e.message || e);
}

// Remember what was parsed + published so unchanged AMCs are skipped next run.
// Only after a fully successful publish; failed AMCs are simply retried.
if (doPush && anythingParsed && !report.push_error && !syncWindowFailed && existsSync(join(outDir, ".git"))) {
  try {
    const next = updateManifest(parseManifest, fingerprint, parsedRecords, today);
    writeManifest(outDir, next);
    run("git", ["-C", outDir, "add", MANIFEST_REL], { label: "stage parse manifest" });
    const st = spawnSync("git", ["-C", outDir, "status", "--porcelain", "--", MANIFEST_REL], { encoding: "utf8" });
    if (st.stdout.trim()) {
      run("git", [
        "-C", outDir,
        "-c", "user.email=holdings-bot@users.noreply.github.com",
        "-c", "user.name=holdings-sync",
        "commit", "-m", `meta: parse manifest ${today}`,
      ], { label: "commit parse manifest" });
      run("git", ["-C", outDir, "push", "origin", `HEAD:${HOLDINGS_BRANCH}`], { label: "push parse manifest" });
    }
    report.parse_manifest = { recorded: parsedRecords.map((r) => ({ slice: r.key, amcs: r.amcs.length })) };
  } catch (e) {
    // Non-fatal: next run just re-parses those AMCs.
    report.parse_manifest = { error: String(e.message || e) };
    console.error("parse manifest update failed (non-fatal):", e.message || e);
  }
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
    amcs_unchanged: a.amcs_unchanged + (r.unchanged || 0),
    amcs_empty: a.amcs_empty + r.empty,
    amcs_error: a.amcs_error + r.error,
    amcs_needs_review: a.amcs_needs_review + (r.needs_review || 0),
    files_downloaded: a.files_downloaded + r.files,
    files_rejected: a.files_rejected + (r.rejected || 0),
    quarantine_promoted: a.quarantine_promoted + (r.quarantine_promoted || 0),
    quarantine_unresolved: a.quarantine_unresolved + (r.quarantine_unresolved || 0),
  }),
  {
    amcs_checked: 0, amcs_ok: 0, amcs_unchanged: 0, amcs_empty: 0, amcs_error: 0, amcs_needs_review: 0,
    files_downloaded: 0, files_rejected: 0, quarantine_promoted: 0, quarantine_unresolved: 0,
  },
);

// Per-AMC completeness vs the previous month's same slice (after SEBI deadline).
// Include slices whose fetch window closed, so AMCs that never published stay flagged.
const checkedDates = fetchPlan.filter((j) => j.skip !== "future_as_of").map((j) => j.period);
report.completeness = buildCompleteness({
  holdingsDir: outDir,
  asOfDates: checkedDates,
  today: todayUtcIso(),
  probes,
  quarantineReports,
  registryAmcs: (loadJsonIfExists(join(ROOT, "registry/amcs.json")) || {}).amcs || [],
});
console.log("\nCompleteness:\n" + formatCompleteness(report.completeness));

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

report.run_status = report.push_error
  ? "push_failed"
  : report.completeness.failures
    ? "incomplete"
    : "ok";
report.log_path = process.env.HOLDINGS_CLOUD_LOG || null;
report.timings = {
  total_seconds: Math.round((Date.now() - Date.parse(report.started_at)) / 1000),
  steps: STEP_TIMINGS,
};
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
if (report.run_status === "incomplete") {
  // Data that WAS fetched has been published; this exit code means
  // "AMCs are missing past their SEBI deadline — investigate", not "rolled back".
  console.error(
    `\ncloud-holdings-update: published, but INCOMPLETE — ${report.completeness.failures} AMC gap(s) past deadline (see completeness above)`,
  );
  process.exit(2);
}
console.log("\ncloud-holdings-update: done");
