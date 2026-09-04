#!/usr/bin/env node
/**
 * Cursor Cloud entrypoint: fetch → parse → enrich → sync holdings → refresh filings.
 *
 *   node scripts/cloud-holdings-update.mjs           # dry run
 *   node scripts/cloud-holdings-update.mjs --push      # publish
 */
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");

function hasFlag(name) {
  return process.argv.includes(`--${name}`);
}
function run(cmd, args, { label } = {}) {
  if (label) console.log(`\n→ ${label}`);
  const res = spawnSync(cmd, args, { stdio: "inherit", cwd: ROOT, env: process.env });
  if (res.status !== 0) {
    throw new Error(`${cmd} ${args.join(" ")} failed (${res.status})`);
  }
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

const doPush = hasFlag("push");
const token = process.env.HOLDINGS_GH_TOKEN || process.env.GH_TOKEN || process.env.GITHUB_TOKEN;
if (doPush && !token) {
  console.error("Set HOLDINGS_GH_TOKEN (or GH_TOKEN) for --push");
  process.exit(1);
}
if (token) process.env.GH_TOKEN = token;

const toYm = monthYm();
const fromYm = shiftYm(toYm, -2);
const periods = [fromYm, shiftYm(toYm, -1), toYm].filter((v, i, a) => a.indexOf(v) === i);

console.log(
  JSON.stringify(
    { mode: doPush ? "push" : "dry-run", periods, holdings_owner: "kushagra-agarwal-a" },
    null,
    2,
  ),
);

for (const period of periods) {
  run("npm", ["run", "fetch", "--", `--type=monthly`, `--period=${period}`], {
    label: `fetch monthly ${period}`,
  });
  run("npm", ["run", "fetch", "--", `--type=fortnightly`, `--period=${period}`], {
    label: `fetch fortnightly ${period}`,
  });
  run("npm", ["run", "parse:amc", "--", `--type=monthly`, `--period=${period}`], {
    label: `parse monthly ${period}`,
  });
  run("npm", ["run", "parse:amc", "--", `--type=fortnightly`, `--period=${period}`], {
    label: `parse fortnightly ${period}`,
  });
}

run("npm", ["run", "holdings:enrich"], { label: "enrich identifiers" });
run("npm", ["run", "holdings:assert-locks"], { label: "assert mapping locks" });

const syncArgs = [
  join(ROOT, "scripts/sync-asof-window.mjs"),
  `--from=${fromYm}`,
  `--to=${toYm}`,
  ...(doPush ? ["--push"] : ["--dry-run"]),
];
run(process.execPath, syncArgs, { label: `sync window ${fromYm}..${toYm}` });

if (doPush) {
  run(process.execPath, [join(ROOT, "scripts/refresh-filings-catalog.mjs"), "--push"], {
    label: "refresh filings catalog",
  });

  const verify = spawnSync(
    "curl",
    ["-sS", "https://openfin.pocketedge.in/api/v1/filings", "-H", "Cache-Control: no-cache"],
    { encoding: "utf8" },
  );
  if (verify.status === 0) {
    try {
      const body = JSON.parse(verify.stdout || "{}");
      console.log("\nOpenFin filings:", JSON.stringify(body.filings || [], null, 2));
    } catch {
      console.log("\nOpenFin filings response:", (verify.stdout || "").slice(0, 500));
    }
  } else {
    console.warn("Warning: could not verify OpenFin filings API");
  }
}

console.log("\ncloud-holdings-update: done");
