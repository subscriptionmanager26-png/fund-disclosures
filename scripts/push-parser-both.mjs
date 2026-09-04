#!/usr/bin/env node
/**
 * Push current branch to both parser remotes (subscriptionmanager + kushagra).
 *
 *   git remote add kushagra https://github.com/kushagra-agarwal-a/fund-disclosures.git
 *   HOLDINGS_GH_TOKEN=... node scripts/push-parser-both.mjs
 */
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(ROOT, "..");

const token = process.env.HOLDINGS_GH_TOKEN || process.env.GH_TOKEN || "";
const branch =
  spawnSync("git", ["rev-parse", "--abbrev-ref", "HEAD"], {
    cwd: REPO_ROOT,
    encoding: "utf8",
  }).stdout?.trim() || "main";

function run(cmd, args, env = {}) {
  const res = spawnSync(cmd, args, {
    cwd: REPO_ROOT,
    stdio: "inherit",
    env: { ...process.env, ...env },
  });
  if (res.status !== 0) process.exit(res.status || 1);
}

// Ensure kushagra remote uses token when set
if (token) {
  const kushagraUrl = `https://x-access-token:${token}@github.com/kushagra-agarwal-a/fund-disclosures.git`;
  run("git", ["remote", "get-url", "kushagra"], {});
  const has = spawnSync("git", ["remote", "get-url", "kushagra"], {
    cwd: REPO_ROOT,
    encoding: "utf8",
  });
  if (has.status !== 0) {
    run("git", ["remote", "add", "kushagra", kushagraUrl]);
  } else {
    run("git", ["remote", "set-url", "kushagra", kushagraUrl]);
  }
}

console.log(`Pushing ${branch} → origin (subscriptionmanager26-png)…`);
run("git", ["push", "origin", branch]);

console.log(`Pushing ${branch} → kushagra (kushagra-agarwal-a)…`);
run("git", ["-c", "credential.helper=", "push", "kushagra", branch]);

console.log("Done — parser mirrored to both accounts.");
