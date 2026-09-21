#!/usr/bin/env node
/**
 * CI guard: catalog available_as_of / latest_as_of must match on-disk portfolio files.
 *
 *   node scripts/verify-catalog-coverage.mjs
 *   node scripts/verify-catalog-coverage.mjs --out=/path/to/fund-holdings-data
 */
import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import {
  assertNoPhantomAsOfLinks,
  assertCatalogPortfolioCoverage,
} from "./lib/asof-portfolios.mjs";
import { defaultHoldingsOutDir } from "./lib/resolve-holdings-out-dir.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");

function argValue(name, fallback = null) {
  const prefix = `--${name}=`;
  const hit = process.argv.find((a) => a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : fallback;
}

function pinnedCommitFromMeta(meta) {
  const url = String(meta?.cdn_catalog || "");
  const m = url.match(/@([0-9a-f]{7,40})\//i);
  return m?.[1] || String(meta?.commit || "").trim() || null;
}

function assertMetaPinMatchesCatalog(outDir, catalog) {
  const metaPath = join(outDir, "meta.json");
  if (!existsSync(metaPath)) return { ok: true, skipped: true };

  const meta = JSON.parse(readFileSync(metaPath, "utf8"));
  const pinned = pinnedCommitFromMeta(meta);
  if (!pinned) {
    return { ok: false, error: "meta.json missing commit / cdn_catalog pin" };
  }

  if (meta.cdn_catalog && !meta.cdn_catalog.includes(`@${pinned}/`)) {
    return {
      ok: false,
      error: "meta.cdn_catalog commit does not match meta.commit",
    };
  }

  const show = spawnSync(
    "git",
    ["show", `${pinned}:catalog/amfi-lookup.json`],
    { cwd: outDir, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 },
  );
  if (show.status !== 0) {
    const diff = spawnSync(
      "git",
      ["diff", "--quiet", pinned, "HEAD", "--", "catalog/amfi-lookup.json"],
      { cwd: outDir },
    );
    if (diff.status === 0) {
      return { ok: true, pinned: pinned.slice(0, 7), note: "pin behind HEAD but catalog unchanged" };
    }
    return {
      ok: false,
      error: `cannot read catalog at pinned commit ${pinned.slice(0, 7)} (meta pin stale?)`,
    };
  }

  let pinnedCatalog;
  try {
    pinnedCatalog = JSON.parse(show.stdout);
  } catch {
    return { ok: false, error: "pinned catalog JSON invalid" };
  }

  const pinnedPhantom = assertNoPhantomAsOfLinks(outDir, pinnedCatalog);
  if (!pinnedPhantom.ok) {
    return {
      ok: false,
      error: `pinned catalog (${pinned.slice(0, 7)}) has ${pinnedPhantom.phantom.length} phantom link(s)`,
    };
  }

  const probe = "122640";
  const diskLatest = catalog?.[probe]?.latest_as_of;
  const pinnedLatest = pinnedCatalog?.[probe]?.latest_as_of;
  if (diskLatest && pinnedLatest && diskLatest !== pinnedLatest) {
    return {
      ok: false,
      error: `meta pin drift: on-disk ${probe} latest_as_of=${diskLatest} but pinned catalog has ${pinnedLatest}`,
    };
  }

  return { ok: true, pinned: pinned.slice(0, 7) };
}

const outDir = argValue("out", defaultHoldingsOutDir(ROOT));
const catalogPath = join(outDir, "catalog/amfi-lookup.json");

if (!existsSync(catalogPath)) {
  console.error(`Missing catalog: ${catalogPath}`);
  process.exit(1);
}

const catalog = JSON.parse(readFileSync(catalogPath, "utf8"));
const phantom = assertNoPhantomAsOfLinks(outDir, catalog);
const coverage = assertCatalogPortfolioCoverage(outDir, catalog);
const metaPin = assertMetaPinMatchesCatalog(outDir, catalog);

let failed = false;

if (!phantom.ok) {
  failed = true;
  console.error(
    `Phantom available_as_of links: ${phantom.phantom.length} (catalog date with no portfolio file)`,
  );
  for (const p of phantom.phantom.slice(0, 12)) {
    console.error(`  ${p.sample_amfi} → ${p.portfolio_id}@${p.as_of}`);
  }
  if (phantom.phantom.length > 12) {
    console.error(`  … ${phantom.phantom.length - 12} more`);
  }
}

if (!metaPin.ok) {
  failed = true;
  console.error(`meta.json pin: ${metaPin.error}`);
}

if (!coverage.ok) {
  console.warn(
    `Latest as-of file gaps: ${coverage.missing.length} portfolio(s)`,
  );
  for (const m of coverage.missing.slice(0, 8)) {
    console.warn(`  ${m.sample_amfi || "?"} → ${m.portfolio_id}@${m.as_of || "?"}`);
  }
}

if (failed) {
  process.exit(1);
}

console.log(
  JSON.stringify(
    {
      ok: true,
      out: outDir,
      phantom_links: 0,
      latest_gaps: coverage.missing.length,
      meta_pin: metaPin.pinned || null,
    },
    null,
    2,
  ),
);
