#!/usr/bin/env node
/**
 * Sync multiple as-of slices in safe order:
 *   1) mid-month fortnightly dates
 *   2) month-end fortnightly (merge — does not drop monthly books)
 *   3) month-end monthly last (full replace — authoritative universe)
 *
 * Usage:
 *   node scripts/sync-asof-window.mjs --dry-run
 *   node scripts/sync-asof-window.mjs --push
 *   node scripts/sync-asof-window.mjs --from=2026-06 --to=2026-08 --push
 */
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { isMonthEndAsOf } from "./lib/asof-portfolios.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const SYNC = join(ROOT, "scripts/sync-asof-holdings-to-github.mjs");

function argValue(name, fallback = null) {
  const prefix = `--${name}=`;
  const hit = process.argv.find((a) => a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : fallback;
}
function hasFlag(name) {
  return process.argv.includes(`--${name}`);
}

function monthEnd(y, m) {
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return `${y}-${String(m).padStart(2, "0")}-${String(last).padStart(2, "0")}`;
}

/** @returns {{ asof: string, cadence: 'fortnightly'|'monthly' }[]} */
function buildWindow(fromYm, toYm) {
  const [fy, fm] = fromYm.split("-").map(Number);
  const [ty, tm] = toYm.split("-").map(Number);
  const specs = [];
  const seen = new Set();

  function add(asof, cadence) {
    const key = `${asof}::${cadence}`;
    if (seen.has(key)) return;
    seen.add(key);
    specs.push({ asof, cadence });
  }

  for (let y = fy, m = fm; y < ty || (y === ty && m <= tm); ) {
    const mid = `${y}-${String(m).padStart(2, "0")}-15`;
    const end = monthEnd(y, m);
    add(mid, "fortnightly");
    add(end, "fortnightly");
    add(end, "monthly");
    m += 1;
    if (m > 12) {
      m = 1;
      y += 1;
    }
  }

  // Stable order: all mid-month fn, then month-end fn, then month-end monthly per date
  const mid = specs.filter((s) => s.cadence === "fortnightly" && !isMonthEndAsOf(s.asof));
  const endFn = specs.filter((s) => s.cadence === "fortnightly" && isMonthEndAsOf(s.asof));
  const endMo = specs.filter((s) => s.cadence === "monthly");
  return [...mid, ...endFn, ...endMo];
}

const dryRun = hasFlag("dry-run");
const doPush = hasFlag("push");
const fromYm = argValue("from", "2026-06");
const toYm = argValue("to", "2026-08");
const specs = buildWindow(fromYm, toYm);

console.log(
  JSON.stringify(
    { from: fromYm, to: toYm, slices: specs.length, dry_run: dryRun, push: doPush },
    null,
    2,
  ),
);

for (const { asof, cadence } of specs) {
  const args = [
    SYNC,
    `--asof=${asof}`,
    `--cadence=${cadence}`,
    ...(hasFlag("merge") ? ["--merge"] : []),
    ...(dryRun ? ["--dry-run"] : []),
    ...(doPush ? ["--push"] : []),
  ];
  console.log(`\n→ sync ${asof} (${cadence})`);
  const res = spawnSync(process.execPath, args, { stdio: "inherit", cwd: ROOT });
  if (res.status !== 0) {
    console.error(`FAIL ${asof} ${cadence}`);
    process.exit(res.status || 1);
  }
}

console.log("\nDone.");
