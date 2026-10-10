#!/usr/bin/env node
/**
 * Fail when catalog/filings.json does not list every portfolios/asof/* slice on disk.
 * OpenFin and holdings-browser expose dates from this index only.
 *
 *   node scripts/verify-filings-index.mjs
 *   node scripts/verify-filings-index.mjs --out=/path/to/fund-holdings-data
 */
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  assertFilingsCoverOnDisk,
  loadRepoFilings,
} from "./lib/holdings-guard.mjs";
import { defaultHoldingsOutDir } from "./lib/resolve-holdings-out-dir.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");

function argValue(name, fallback = null) {
  const prefix = `--${name}=`;
  const hit = process.argv.find((a) => a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : fallback;
}

const outDir = argValue("out", defaultHoldingsOutDir(ROOT));
const filings = loadRepoFilings(outDir);
if (!filings?.filings?.length) {
  console.error(`No filings rows in ${join(outDir, "catalog/filings.json")}`);
  process.exit(1);
}

const result = assertFilingsCoverOnDisk(outDir, filings, { label: "verify-filings-index" });
console.log(
  JSON.stringify(
    {
      ok: true,
      outDir,
      ...result,
      filings: filings.filings.map((r) => ({
        as_of: r.as_of,
        cadence: r.cadence,
        portfolio_count: r.portfolio_count,
      })),
    },
    null,
    2,
  ),
);
