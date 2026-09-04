#!/usr/bin/env node
/**
 * Sync unique fund portfolios + AMFI catalog to the public GitHub data repo.
 *
 * Dedup model:
 *   - One JSON per unique portfolio, keyed by portfolio_id
 *   - Catalog lists all schemes; holdings rows link via portfolio_id
 *   - Sibling share-classes share one portfolio object
 *
 * Layout (--out, default .tmp/fund-holdings-data):
 *   portfolios/asof/{yyyy-mm-dd}/{portfolio_id}.json   ← sole portfolio store
 *   catalog/amfi-lookup.json   (latest_as_of + available_as_of per scheme)
 *   catalog/filings.json
 *   meta.json
 *
 * Usage:
 *   node scripts/sync-holdings-to-github.mjs --push
 *   node scripts/sync-holdings-to-github.mjs --asof=2026-08-15 --cadence=fortnightly --push
 *   node scripts/sync-holdings-to-github.mjs --asof=2026-06-30 --cadence=monthly --push
 *
 * Auth for --push: GH_TOKEN or GITHUB_TOKEN. Never commit tokens.
 */
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import {
  attachAvailableAsOf,
  assertCatalogPortfolioCoverage,
  buildFilingsFromAsOfDirs,
  collectAsOfPortfolios,
  mirrorLatestPortfolios,
  normalizeAsOf,
  portfolioAsofKey,
  pruneOrphanAsOfPortfolios,
  scanExistingAsOfDirs,
  sourcePeriodFromAsOf,
} from "./lib/asof-portfolios.mjs";
import {
  assertNoHoldingsRegression,
  loadRepoCatalog,
  mergeCatalogAsOfFromRepo,
} from "./lib/holdings-guard.mjs";
import { defaultHoldingsOutDir } from "./lib/resolve-holdings-out-dir.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");

const { shapeHoldingsPayload } = await import(
  pathToFileURL(join(ROOT, "holdings-browser/api/_contract.js")).href,
);

const OWNER = process.env.HOLDINGS_DATA_OWNER || "kushagra-agarwal-a";
const REPO = process.env.HOLDINGS_DATA_REPO || "fund-holdings-data";
const BRANCH = process.env.HOLDINGS_DATA_BRANCH || "main";

function argValue(name, fallback = null) {
  const prefix = `--${name}=`;
  const hit = process.argv.find((a) => a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : fallback;
}

function hasFlag(name) {
  return process.argv.includes(`--${name}`);
}

const dryRun = hasFlag("dry-run");
const doPush = hasFlag("push");
const keepOut = hasFlag("keep");
const allowRegression = hasFlag("allow-regression");
const updateLatest = hasFlag("update-latest");
if (updateLatest) {
  console.warn(
    "Warning: --update-latest is deprecated. Portfolios are stored under portfolios/asof/ only; catalog.latest_as_of drives API latest.",
  );
}
const limit = Number(argValue("limit", "0")) || 0;
const asofRaw = argValue("asof", "");
const asof = normalizeAsOf(asofRaw);
const cadence = argValue("cadence", asof ? "monthly" : "");
const sourcePeriod =
  argValue("source-period", "") || (asof ? sourcePeriodFromAsOf(asof) : "");
const outDir = argValue("out", defaultHoldingsOutDir(ROOT));
const lookupPath = argValue(
  "lookup",
  join(ROOT, "holdings-browser/api/amfi-lookup.json"),
);

function portfolioIdFromRow(row) {
  // Prefer numeric id from B2 key. Reject scheme-name path segments from bad matches.
  const key = row?.b2_key || "";
  const m = String(key).match(/\/latest\/[^/]+\/([^/]+)\/portfolio\.json$/);
  if (m && /^\d{4,8}$/.test(m[1])) return m[1];
  for (const candidate of [row?.parent_amfi, row?.amfi_code]) {
    const id = String(candidate || "");
    if (/^\d{4,8}$/.test(id)) return id;
  }
  return null;
}

function portfolioObjectKey(_id) {
  return null;
}

function portfolioAsofObjectKey(period, id) {
  return portfolioAsofKey(period, id);
}

function cdnUrl(objectKey) {
  return `https://cdn.jsdelivr.net/gh/${OWNER}/${REPO}@${BRANCH}/${objectKey}`;
}

function ensureDir(p) {
  mkdirSync(p, { recursive: true });
}

function writeJson(filePath, value) {
  ensureDir(dirname(filePath));
  writeFileSync(filePath, `${JSON.stringify(value)}\n`, "utf8");
}

function run(cmd, args) {
  const res = spawnSync(cmd, args, { stdio: "inherit", encoding: "utf8" });
  if (res.status !== 0) {
    throw new Error(`${cmd} ${args.join(" ")} failed with status ${res.status}`);
  }
}

function gitTokenUrl() {
  const token = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;
  if (!token) throw new Error("Set GH_TOKEN or GITHUB_TOKEN for --push");
  return `https://x-access-token:${token}@github.com/${OWNER}/${REPO}.git`;
}

function writeReadme() {
  writeFileSync(
    join(outDir, "README.md"),
    `# ${REPO}

Public AMFI mutual-fund holdings (zero paid cloud).

## Storage model

- **Portfolios** — one JSON per unique book per as-of date: \`portfolios/asof/{YYYY-MM-DD}/{portfolio_id}.json\`
- **Catalog** — all AMFI schemes; \`latest_as_of\` + \`available_as_of\` point at asof paths (no \`portfolios/latest/\` duplicate)

Sibling share-classes share one portfolio object.

## CDN

\`\`\`
https://cdn.jsdelivr.net/gh/${OWNER}/${REPO}@${BRANCH}/catalog/amfi-lookup.json
https://cdn.jsdelivr.net/gh/${OWNER}/${REPO}@${BRANCH}/portfolios/asof/{as_of}/{portfolio_id}.json
\`\`\`

Resolve: catalog[amfi].portfolio_id + catalog[amfi].latest_as_of → portfolios/asof/{date}/{id}.json

Synced via \`scripts/sync-holdings-to-github.mjs\`.
`,
    "utf8",
  );
}

function initOrClone() {
  if (existsSync(join(outDir, ".git"))) {
    if (doPush) {
      run("git", ["-C", outDir, "remote", "set-url", "origin", gitTokenUrl()]);
      run("git", ["-C", outDir, "fetch", "origin", BRANCH]);
      run("git", [
        "-C",
        outDir,
        "checkout",
        "-B",
        BRANCH,
        `origin/${BRANCH}`,
      ]);
    }
    return;
  }

  if (doPush) {
    ensureDir(dirname(outDir));
    if (existsSync(outDir)) rmSync(outDir, { recursive: true, force: true });
    const clone = spawnSync(
      "git",
      ["clone", "--branch", BRANCH, "--single-branch", gitTokenUrl(), outDir],
      { encoding: "utf8" },
    );
    if (clone.status === 0) return;
    ensureDir(outDir);
    run("git", ["-C", outDir, "init", "-b", BRANCH]);
    run("git", ["-C", outDir, "remote", "add", "origin", gitTokenUrl()]);
    writeReadme();
    return;
  }

  if (existsSync(outDir) && !keepOut) {
    rmSync(outDir, { recursive: true, force: true });
  }
  ensureDir(outDir);
  writeReadme();
}

function schemeFromRow(row) {
  return {
    amfi_code: String(row.amfi_code),
    name: row.name ?? null,
    amc_name: row.amc_name ?? null,
    parent_name: row.parent_name ?? null,
    parent_amfi: row.parent_amfi ?? null,
    nav: row.nav ?? null,
    nav_date: row.nav_date ?? null,
    isin: row.isin ?? null,
    category: row.category ?? null,
    shortcode: row.shortcode ?? null,
    as_of: row.as_of ?? null,
    source_file: row.source_file ?? null,
  };
}

function buildPortfolioIndex(lookup) {
  const byId = new Map();

  for (const row of Object.values(lookup)) {
    const id = portfolioIdFromRow(row);
    if (!id) continue;
    if (!row.has_holdings && !row.local_path && !row.b2_key) continue;

    let entry = byId.get(id);
    if (!entry) {
      entry = {
        portfolio_id: id,
        members: [],
        canonical: null,
        local_path: null,
        b2_key: null,
      };
      byId.set(id, entry);
    }

    const amfi = String(row.amfi_code || "");
    if (amfi && !entry.members.includes(amfi)) entry.members.push(amfi);

    if (row.local_path && existsSync(join(ROOT, row.local_path))) {
      entry.local_path = row.local_path;
    }
    if (row.b2_key) entry.b2_key = row.b2_key;

    // Prefer the scheme whose amfi_code equals portfolio_id as canonical.
    if (!entry.canonical || String(row.amfi_code) === id) {
      entry.canonical = row;
    }
  }

  for (const entry of byId.values()) {
    entry.members.sort();
    if (!entry.canonical && entry.members.length) {
      entry.canonical = lookup[entry.members[0]] || null;
    }
  }

  return byId;
}

function rewriteCatalog(lookup, byId) {
  const out = {};
  for (const [code, row] of Object.entries(lookup)) {
    const id = portfolioIdFromRow(row);
    const linked = Boolean(row.has_holdings) && Boolean(id) && byId.has(id);
    const { local_path: _local, portfolio_key: _pk, portfolio_url: _pu, ...rest } =
      row;
    out[code] = {
      ...rest,
      portfolio_id: linked ? id : null,
      portfolio_key: null,
      portfolio_url: null,
      b2_key: row.b2_key ?? null,
    };
  }
  return out;
}

function shapePortfolioPayload(entry, portfolio) {
  const scheme = schemeFromRow(
    entry.canonical || { amfi_code: entry.portfolio_id },
  );
  const shaped = shapeHoldingsPayload(scheme, portfolio);
  return {
    portfolio_id: entry.portfolio_id,
    member_amfi_codes: entry.members,
    scheme: shaped.scheme,
    meta: {
      ...shaped.meta,
      portfolio_id: entry.portfolio_id,
      member_count: entry.members.length,
    },
    holdings: shaped.holdings,
  };
}

function removeLegacyHoldingsDir() {
  const legacy = join(outDir, "holdings");
  if (existsSync(legacy)) {
    rmSync(legacy, { recursive: true, force: true });
    console.log("Removed legacy holdings/ tree (replaced by portfolios/).");
  }
}

/** Drop portfolio files not in this sync set (bad ids, removed books). */
function pruneOrphanPortfolios(keepIds) {
  const dir = join(outDir, "portfolios/latest");
  if (!existsSync(dir)) return 0;
  const keep = new Set([...keepIds].map((id) => `${id}.json`));
  let removed = 0;
  for (const name of readdirSync(dir)) {
    if (!name.endsWith(".json")) continue;
    if (keep.has(name)) continue;
    unlinkSync(join(dir, name));
    removed += 1;
  }
  if (removed) console.log(`Pruned ${removed} orphan portfolio file(s).`);
  return removed;
}

function loadExistingFilings() {
  const p = join(outDir, "catalog/filings.json");
  if (!existsSync(p)) return { filings: [] };
  try {
    return JSON.parse(readFileSync(p, "utf8"));
  } catch {
    return { filings: [] };
  }
}

function writeFilingsAndCatalogAvailability(
  catalog,
  { baselineCatalog = null, syncedDates = [] } = {},
) {
  const beforeCatalog = baselineCatalog || loadRepoCatalog(outDir);
  const asOfMap = scanExistingAsOfDirs(outDir, catalog);
  const withDates = attachAvailableAsOf(catalog, asOfMap, {
    cdnUrlFn: cdnUrl,
    outDir,
  });
  assertNoHoldingsRegression(outDir, beforeCatalog, withDates, {
    allowRegression,
    label: "writeFilingsAndCatalogAvailability",
    syncedDates,
  });
  writeJson(join(outDir, "catalog/amfi-lookup.json"), withDates);

  const merged = buildFilingsFromAsOfDirs(outDir, withDates);
  writeJson(join(outDir, "catalog/filings.json"), merged);

  const coverage = assertCatalogPortfolioCoverage(outDir, withDates);
  if (!coverage.ok) {
    const sample = coverage.missing.slice(0, 8);
    const msg =
      `Catalog/asof mismatch: ${coverage.missing.length} portfolio(s) missing on disk. ` +
      `Examples: ${sample.map((m) => `${m.portfolio_id}@${m.as_of || "?"}`).join(", ")}`;
    if (coverage.missing.length > 20) {
      throw new Error(msg);
    }
    console.warn(`Warning: ${msg}`);
  }

  const mirrored = mirrorLatestPortfolios(outDir, withDates);
  if (mirrored) console.log(`Mirrored ${mirrored} portfolio(s) to portfolios/latest/.`);

  return { catalog: withDates, filings: merged };
}

function syncHistoricalAsOf(lookup) {
  if (!asof) throw new Error("--asof=YYYY-MM-DD required for historical sync");
  if (!cadence || !["monthly", "fortnightly"].includes(cadence)) {
    throw new Error("--cadence=monthly|fortnightly required with --asof");
  }

  const collected = collectAsOfPortfolios({
    root: ROOT,
    cadence,
    sourcePeriod,
    asOf: asof,
    catalogLookup: lookup,
  });

  let entries = [...collected.values()];
  entries.sort((a, b) => a.portfolio_id.localeCompare(b.portfolio_id));
  if (limit > 0) entries = entries.slice(0, limit);

  console.log(
    JSON.stringify(
      {
        mode: "historical-asof",
        owner: OWNER,
        repo: REPO,
        out: outDir,
        asof,
        cadence,
        source_period: sourcePeriod,
        portfolios_found: collected.size,
        syncing: entries.length,
        update_latest: updateLatest,
        dryRun,
        push: doPush,
      },
      null,
      2,
    ),
  );

  if (dryRun) {
    for (const e of entries.slice(0, 8)) {
      console.log(
        `  would write ${portfolioAsofObjectKey(asof, e.portfolio_id)} ← ${e.local_path}`,
      );
    }
    if (entries.length > 8) console.log(`  … ${entries.length - 8} more`);
    return;
  }

  initOrClone();
  const repoCatalogBefore = loadRepoCatalog(outDir);

  let written = 0;
  let failed = 0;
  for (const entry of entries) {
    try {
      const portfolio =
        entry.payload ||
        JSON.parse(readFileSync(join(ROOT, entry.local_path), "utf8"));
      const fakeRow = {
        amfi_code: entry.portfolio_id,
        name: entry.meta?.scheme_name || entry.meta?.amfi_name || null,
        amc_name: entry.meta?.amc_name || null,
        parent_name: entry.meta?.amfi_name || entry.meta?.scheme_name || null,
        parent_amfi: entry.portfolio_id,
        shortcode: entry.meta?.shortcode || null,
        as_of: asof,
        source_file: entry.meta?.source_file || null,
      };
      const shaped = shapeHoldingsPayload(schemeFromRow(fakeRow), portfolio);
      const payload = {
        portfolio_id: entry.portfolio_id,
        member_amfi_codes: entry.members,
        scheme: shaped.scheme,
        meta: {
          ...shaped.meta,
          portfolio_id: entry.portfolio_id,
          member_count: entry.members.length,
          as_of: asof,
          cadence,
        },
        holdings: shaped.holdings,
      };
      writeJson(
        join(outDir, portfolioAsofObjectKey(asof, entry.portfolio_id)),
        payload,
      );
      written += 1;
      if (written % 100 === 0) console.log(`  wrote ${written}/${entries.length}`);
    } catch (err) {
      failed += 1;
      console.error(`  FAIL ${entry.portfolio_id}`, err?.message || err);
    }
  }

  const keepIds = entries.map((e) => e.portfolio_id);
  const pruned = pruneOrphanAsOfPortfolios(outDir, asof, keepIds, lookup, {
    mergeExisting: cadence === "fortnightly" || hasFlag("merge"),
  });
  if (pruned) console.log(`Pruned ${pruned} stale asof file(s) for ${asof}.`);

  // Keep existing catalog; refresh availability + filings
  let catalog = lookup;
  const catalogPath = join(outDir, "catalog/amfi-lookup.json");
  if (existsSync(catalogPath)) {
    try {
      catalog = JSON.parse(readFileSync(catalogPath, "utf8"));
    } catch {
      catalog = lookup;
    }
  }
  const { filings } = writeFilingsAndCatalogAvailability(catalog, {
    baselineCatalog: repoCatalogBefore,
    syncedDates: [asof],
  });

  writeJson(join(outDir, "meta.json"), {
    generated_at: new Date().toISOString(),
    owner: OWNER,
    repo: REPO,
    branch: BRANCH,
    model: "deduped-portfolios",
    last_asof_sync: {
      as_of: asof,
      cadence,
      source_period: sourcePeriod,
      portfolios_written: written,
      portfolios_failed: failed,
      update_latest: updateLatest,
    },
    filings_count: filings.filings.length,
    cdn_catalog: cdnUrl("catalog/amfi-lookup.json"),
    cdn_filings: cdnUrl("catalog/filings.json"),
    cdn_portfolio_template: cdnUrl("portfolios/asof/{as_of}/{portfolio_id}.json"),
    cdn_asof_template: cdnUrl("portfolios/asof/{as_of}/{portfolio_id}.json"),
  });

  console.log(
    JSON.stringify(
      {
        written,
        failed,
        asof,
        filings: filings.filings.length,
        outDir,
      },
      null,
      2,
    ),
  );

  if (!doPush) return;
  pushWithMetaPin(
    `sync(asof=${asof}/${cadence}): ${written} portfolios`,
  );
}

function pushWithMetaPin(commitMessage) {
  run("git", ["-C", outDir, "add", "-A"]);
  const status = spawnSync("git", ["-C", outDir, "status", "--porcelain"], {
    encoding: "utf8",
  });
  if (!status.stdout.trim()) {
    console.log("Nothing to commit.");
    return;
  }
  run("git", [
    "-C",
    outDir,
    "-c",
    "user.email=holdings-bot@users.noreply.github.com",
    "-c",
    "user.name=holdings-sync",
    "commit",
    "-m",
    commitMessage,
  ]);
  run("git", ["-C", outDir, "remote", "set-url", "origin", gitTokenUrl()]);
  run("git", ["-C", outDir, "push", "-u", "origin", `HEAD:${BRANCH}`]);

  const shaRes = spawnSync("git", ["-C", outDir, "rev-parse", "HEAD"], {
    encoding: "utf8",
  });
  const commit = (shaRes.stdout || "").trim();
  if (commit) {
    const metaPath = join(outDir, "meta.json");
    const meta = JSON.parse(readFileSync(metaPath, "utf8"));
    meta.commit = commit;
    meta.raw_base = `https://raw.githubusercontent.com/${OWNER}/${REPO}/${commit}`;
    meta.cdn_catalog = `https://cdn.jsdelivr.net/gh/${OWNER}/${REPO}@${commit}/catalog/amfi-lookup.json`;
    meta.cdn_filings = `https://cdn.jsdelivr.net/gh/${OWNER}/${REPO}@${commit}/catalog/filings.json`;
    meta.cdn_portfolio_template = `https://cdn.jsdelivr.net/gh/${OWNER}/${REPO}@${commit}/portfolios/asof/{as_of}/{portfolio_id}.json`;
    meta.cdn_asof_template = `https://cdn.jsdelivr.net/gh/${OWNER}/${REPO}@${commit}/portfolios/asof/{as_of}/{portfolio_id}.json`;
    writeJson(metaPath, meta);
    run("git", ["-C", outDir, "add", "meta.json"]);
    run("git", [
      "-C",
      outDir,
      "-c",
      "user.email=holdings-bot@users.noreply.github.com",
      "-c",
      "user.name=holdings-sync",
      "commit",
      "-m",
      `meta: pin content commit ${commit.slice(0, 7)}`,
    ]);
    run("git", ["-C", outDir, "push", "-u", "origin", `HEAD:${BRANCH}`]);
  }
  console.log(
    `Pushed → https://github.com/${OWNER}/${REPO}${
      commit ? `\nPinned commit → ${commit}` : ""
    }`,
  );
}

function main() {
  const lookup = JSON.parse(readFileSync(lookupPath, "utf8"));

  if (asof) {
    syncHistoricalAsOf(lookup);
    return;
  }

  const byId = buildPortfolioIndex(lookup);

  let portfolios = [...byId.values()].filter((e) => e.local_path);
  portfolios.sort((a, b) => a.portfolio_id.localeCompare(b.portfolio_id));
  if (limit > 0) portfolios = portfolios.slice(0, limit);

  const withLocal = [...byId.values()].filter((e) => e.local_path).length;

  console.log(
    JSON.stringify(
      {
        mode: "catalog-asof",
        owner: OWNER,
        repo: REPO,
        out: outDir,
        catalog_schemes: Object.keys(lookup).length,
        unique_portfolios_in_catalog: byId.size,
        unique_portfolios_with_local: withLocal,
        syncing_portfolios: portfolios.length,
        dryRun,
        push: doPush,
      },
      null,
      2,
    ),
  );

  if (dryRun) {
    for (const e of portfolios.slice(0, 8)) {
      const asOfDay =
        normalizeAsOf(e.canonical?.as_of) ||
        normalizeAsOf(
          JSON.parse(readFileSync(join(ROOT, e.local_path), "utf8"))?.meta?.as_of,
        );
      console.log(
        `  would write ${portfolioAsofObjectKey(asOfDay || "YYYY-MM-DD", e.portfolio_id)} ← ${e.local_path} (${e.members.length} schemes)`,
      );
    }
    if (portfolios.length > 8) {
      console.log(`  … ${portfolios.length - 8} more portfolios`);
    }
    return;
  }

  initOrClone();
  const repoCatalogBefore = loadRepoCatalog(outDir);
  removeLegacyHoldingsDir();

  let written = 0;
  let failed = 0;
  /** @type {Map<string, Set<string>>} */
  const writtenByAsOf = new Map();
  for (const entry of portfolios) {
    try {
      const portfolio = JSON.parse(
        readFileSync(join(ROOT, entry.local_path), "utf8"),
      );
      const asOfDay =
        normalizeAsOf(portfolio?.meta?.as_of) ||
        normalizeAsOf(entry.canonical?.as_of);
      if (!asOfDay) {
        failed += 1;
        console.error(`  SKIP ${entry.portfolio_id}: missing meta.as_of`);
        continue;
      }
      const payload = shapePortfolioPayload(entry, portfolio);
      payload.meta = {
        ...payload.meta,
        as_of: asOfDay,
        cadence: portfolio?.meta?.disclosure_type || null,
      };
      writeJson(
        join(outDir, portfolioAsofObjectKey(asOfDay, entry.portfolio_id)),
        payload,
      );
      if (!writtenByAsOf.has(asOfDay)) writtenByAsOf.set(asOfDay, new Set());
      writtenByAsOf.get(asOfDay).add(entry.portfolio_id);
      written += 1;
      if (written % 100 === 0) {
        console.log(`  wrote ${written}/${portfolios.length}`);
      }
    } catch (err) {
      failed += 1;
      console.error(`  FAIL ${entry.portfolio_id}`, err?.message || err);
    }
  }
  let prunedTotal = 0;
  for (const [day, ids] of writtenByAsOf) {
    // Only prune within the as-of date(s) written in this run — never touch other dates.
    prunedTotal += pruneOrphanAsOfPortfolios(outDir, day, [...ids], lookup);
  }
  if (prunedTotal) console.log(`Pruned ${prunedTotal} stale asof file(s).`);

  let catalog = rewriteCatalog(lookup, byId);
  catalog = mergeCatalogAsOfFromRepo(outDir, catalog, repoCatalogBefore);
  const { filings } = writeFilingsAndCatalogAvailability(catalog, {
    baselineCatalog: repoCatalogBefore,
    syncedDates: [...writtenByAsOf.keys()],
  });
  writeJson(join(outDir, "meta.json"), {
    generated_at: new Date().toISOString(),
    owner: OWNER,
    repo: REPO,
    branch: BRANCH,
    model: "deduped-portfolios",
    portfolios_written: written,
    portfolios_failed: failed,
    unique_portfolios_in_catalog: byId.size,
    catalog_schemes: Object.keys(catalog).length,
    schemes_with_portfolio_link: Object.values(catalog).filter(
      (r) => r.portfolio_id,
    ).length,
    filings_count: filings.filings.length,
    cdn_catalog: cdnUrl("catalog/amfi-lookup.json"),
    cdn_filings: cdnUrl("catalog/filings.json"),
    cdn_portfolio_template: cdnUrl("portfolios/asof/{as_of}/{portfolio_id}.json"),
    cdn_asof_template: cdnUrl("portfolios/asof/{as_of}/{portfolio_id}.json"),
  });
  writeReadme();

  console.log(
    JSON.stringify(
      { written, failed, catalog: "catalog/amfi-lookup.json", outDir },
      null,
      2,
    ),
  );

  if (!doPush) return;
  pushWithMetaPin(`sync: ${written} unique portfolios + linked catalog`);
}

main();
