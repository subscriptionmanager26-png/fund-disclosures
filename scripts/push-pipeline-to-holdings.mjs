#!/usr/bin/env node
/**
 * Sync local parser tree into kushagra-agarwal-a/fund-holdings-data pipeline/.
 * Excludes large local data dirs and secrets.
 */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CLONE = join(ROOT, ".tmp/fund-holdings-data-monorepo");
const token = process.env.HOLDINGS_GH_TOKEN || process.env.GH_TOKEN;

function run(cmd, args, cwd = ROOT) {
  const r = spawnSync(cmd, args, { cwd, stdio: "inherit", env: process.env });
  if (r.status !== 0) process.exit(r.status || 1);
}

if (!token) {
  console.error("Set HOLDINGS_GH_TOKEN");
  process.exit(1);
}

mkdirSync(join(ROOT, ".tmp"), { recursive: true });
if (!existsSync(join(CLONE, ".git"))) {
  run("git", [
    "clone",
    "--depth",
    "1",
    `https://x-access-token:${token}@github.com/kushagra-agarwal-a/fund-holdings-data.git`,
    CLONE,
  ]);
} else {
  run("git", ["fetch", "origin", "main"], CLONE);
  run("git", ["checkout", "main"], CLONE);
  run("git", ["reset", "--hard", "origin/main"], CLONE);
}

const dest = join(CLONE, "pipeline");
const excludes = [
  ".git",
  ".tmp",
  "node_modules",
  ".venv",
  "data/disclosures",
  "data/parsed",
  "data/probes",
  ".env",
];
const rsyncArgs = ["-a", "--delete", ...excludes.flatMap((x) => ["--exclude", x]), `${ROOT}/`, `${dest}/`];
run("rsync", rsyncArgs);

run("git", ["add", "pipeline"], CLONE);
const diff = spawnSync("git", ["diff", "--cached", "--quiet"], { cwd: CLONE });
if (diff.status === 0) {
  console.log("No pipeline changes to push.");
  process.exit(0);
}
run("git", ["commit", "-m", "chore(pipeline): sync parser for daily cloud holdings update"], CLONE);
run("git", ["push", "origin", "main"], CLONE);
console.log("Pushed pipeline → kushagra-agarwal-a/fund-holdings-data");
