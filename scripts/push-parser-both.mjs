#!/usr/bin/env node
/**
 * Push parser mirror to subscriptionmanager26-png/fund-disclosures.
 * Canonical: kushagra-agarwal-a/fund-holdings-data (pipeline/).
 */
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(ROOT, "..");
const branch =
  spawnSync("git", ["rev-parse", "--abbrev-ref", "HEAD"], {
    cwd: REPO_ROOT,
    encoding: "utf8",
  }).stdout?.trim() || "main";

const token = process.env.GH_TOKEN || process.env.SUBSCRIPTION_GH_TOKEN || "";
const subUrl = token
  ? `https://x-access-token:${token}@github.com/subscriptionmanager26-png/fund-disclosures.git`
  : "https://github.com/subscriptionmanager26-png/fund-disclosures.git";

function run(cmd, args) {
  const res = spawnSync(cmd, args, { cwd: REPO_ROOT, stdio: "inherit" });
  if (res.status !== 0) process.exit(res.status || 1);
}

spawnSync("git", ["remote", "remove", "subscriptionmanager"], { cwd: REPO_ROOT });
const add = spawnSync("git", ["remote", "add", "subscriptionmanager", subUrl], {
  cwd: REPO_ROOT,
});
if (add.status !== 0) {
  run("git", ["remote", "set-url", "subscriptionmanager", subUrl]);
}

console.log(`Pushing ${branch} → subscriptionmanager26-png/fund-disclosures…`);
run("git", ["-c", "credential.helper=", "push", "subscriptionmanager", branch]);
console.log("Done.");
