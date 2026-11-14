/**
 * Incremental parse: skip AMCs whose source workbooks have not changed since
 * they were last parsed AND published, with the same parser/mapping code.
 *
 * Cloud runs start on a fresh VM, so the parser's own resume (which needs
 * data/parsed on disk) never applies and every workbook is re-parsed daily.
 * This manifest lives in the holdings data repo (meta/parse-sources.json) so it
 * survives between runs:
 *
 *   { version: 1, fingerprint, slices: { "monthly/2026-10-31": { amc_id: digest } } }
 *
 * - digest      sha256 over the AMC's sorted (filename, sha256(content)) for that slice
 * - fingerprint git blob ids of everything that shapes published output
 *               (parsers, registry, reference data, contract, scripts). Any
 *               change → full re-parse once.
 * An AMC is recorded only after it parsed with schemes > 0 and no errors and the
 * sync succeeded, so failures are retried on the next run.
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { spawnSync } from "node:child_process";

export const MANIFEST_REL = "meta/parse-sources.json";
export const FINGERPRINT_PATHS = ["parsers", "registry", "data/sources", "holdings-browser", "scripts"];
/** Keep entries for this many months back. */
export const KEEP_MONTHS = 4;

function sha256(buf) {
  return createHash("sha256").update(buf).digest("hex");
}

function walk(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir).sort()) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

/** Fingerprint of code + reference data that determine published output. */
export function parserFingerprint(root, paths = FINGERPRINT_PATHS) {
  const res = spawnSync("git", ["-C", root, "ls-files", "-s", "--", ...paths], { encoding: "utf8" });
  const dirty = spawnSync("git", ["-C", root, "status", "--porcelain", "--", ...paths], { encoding: "utf8" });
  if (res.status === 0 && res.stdout && dirty.status === 0 && !dirty.stdout.trim()) {
    return `git:${sha256(res.stdout).slice(0, 16)}`;
  }
  // Not a clean git checkout: hash file contents directly.
  const h = createHash("sha256");
  for (const p of paths) {
    for (const f of walk(join(root, p))) {
      h.update(relative(root, f));
      h.update(readFileSync(f));
    }
  }
  return `content:${h.digest("hex").slice(0, 16)}`;
}

/** { amc_id: digest } for data/disclosures/{type}/{asOf}/{amc}/*. */
export function sliceDigests(root, type, asOf) {
  const base = join(root, "data", "disclosures", type, asOf);
  const out = {};
  if (!existsSync(base)) return out;
  for (const amc of readdirSync(base).sort()) {
    const dir = join(base, amc);
    if (!statSync(dir).isDirectory()) continue;
    const rows = walk(dir).map((f) => `${relative(dir, f)}\t${sha256(readFileSync(f))}`);
    if (rows.length) out[amc] = sha256(rows.join("\n"));
  }
  return out;
}

export function loadManifest(holdingsDir) {
  const p = join(holdingsDir, MANIFEST_REL);
  try {
    const doc = JSON.parse(readFileSync(p, "utf8"));
    if (doc && doc.version === 1 && doc.slices) return doc;
  } catch {
    /* missing or unreadable → empty */
  }
  return { version: 1, fingerprint: null, slices: {} };
}

/**
 * AMCs that need parsing for a slice.
 * @returns {{ amcs: string[], reason: string }}
 */
export function amcsToParse(manifest, fingerprint, key, digests) {
  const all = Object.keys(digests).sort();
  if (manifest.fingerprint !== fingerprint) {
    return { amcs: all, reason: manifest.fingerprint ? "code_changed" : "no_manifest" };
  }
  const prev = manifest.slices[key] || {};
  const changed = all.filter((a) => prev[a] !== digests[a]);
  return { amcs: changed, reason: changed.length ? "sources_changed" : "unchanged" };
}

/**
 * Record successfully published AMCs and drop entries older than KEEP_MONTHS.
 * When the fingerprint changed, entries recorded under the old code are discarded.
 * @param {{ key: string, digests: object, amcs: string[] }[]} recorded
 */
export function updateManifest(manifest, fingerprint, recorded, today) {
  const next = {
    version: 1,
    fingerprint,
    updated_at: new Date().toISOString(),
    slices: manifest.fingerprint === fingerprint ? { ...manifest.slices } : {},
  };
  for (const { key, digests, amcs } of recorded) {
    const slice = { ...(next.slices[key] || {}) };
    for (const a of amcs) if (digests[a]) slice[a] = digests[a];
    if (Object.keys(slice).length) next.slices[key] = slice;
  }
  const [y, m] = today.split("-").map(Number);
  const cutoff = new Date(Date.UTC(y, m - 1 - KEEP_MONTHS, 1)).toISOString().slice(0, 10);
  for (const key of Object.keys(next.slices)) {
    const asOf = key.split("/")[1] || "";
    if (asOf < cutoff) delete next.slices[key];
  }
  return next;
}

export function writeManifest(holdingsDir, manifest) {
  const p = join(holdingsDir, MANIFEST_REL);
  mkdirSync(join(holdingsDir, "meta"), { recursive: true });
  writeFileSync(p, JSON.stringify(manifest, null, 2) + "\n");
  return p;
}
