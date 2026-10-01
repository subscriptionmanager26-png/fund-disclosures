/**
 * Residential-capture manifests: links gathered on a phone/home IP when AMC
 * sites block datacenter egress. Stored in-repo under data/residential-capture/
 * and optionally fetched from raw GitHub (see RESIDENTIAL_MANIFEST_BASE).
 */
import { existsSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const defaultRoot = join(__dirname, "../../..");

const DEFAULT_REMOTE_BASE =
  "https://raw.githubusercontent.com/subscriptionmanager26-png/fund-disclosures/main/data/residential-capture/manifests";

function manifestRelPath(slug, period, cadence) {
  return `${slug}/${period}.${cadence}.json`;
}

function localManifestPath(root, slug, period, cadence) {
  return join(
    root,
    "data/residential-capture/manifests",
    manifestRelPath(slug, period, cadence),
  );
}

function remoteManifestUrl(slug, period, cadence) {
  const base = (
    process.env.RESIDENTIAL_MANIFEST_BASE || DEFAULT_REMOTE_BASE
  ).replace(/\/$/, "");
  return `${base}/${manifestRelPath(slug, period, cadence)}`;
}

function normalizeManifest(raw, { slug, period, cadence }) {
  if (!raw || typeof raw !== "object") return null;
  const files = Array.isArray(raw.files)
    ? raw.files
    : Array.isArray(raw)
      ? raw
      : [];
  const out = [];
  const seen = new Set();
  for (const row of files) {
    const url = String(row.download_url || row.url || "").trim();
    if (!url || seen.has(url)) continue;
    seen.add(url);
    const filename =
      row.saved_as ||
      decodeURIComponent(url.split("/").pop()?.split("?")[0] || "download.xlsx");
    out.push({
      url,
      filename,
      title: row.title || "",
      source: "residential_manifest",
    });
  }
  if (!out.length) return null;
  return {
    amc_id: raw.amc_id || slug,
    period: raw.period || period,
    cadence: raw.cadence || cadence,
    captured_at: raw.captured_at || null,
    files: out,
  };
}

function readLocalManifest(root, slug, period, cadence) {
  const path = localManifestPath(root, slug, period, cadence);
  if (!existsSync(path)) return null;
  try {
    return normalizeManifest(JSON.parse(readFileSync(path, "utf8")), {
      slug,
      period,
      cadence,
    });
  } catch {
    return null;
  }
}

async function fetchRemoteManifest(slug, period, cadence) {
  const url = remoteManifestUrl(slug, period, cadence);
  try {
    const res = await fetch(url, {
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(
        Number(process.env.RESIDENTIAL_MANIFEST_TIMEOUT_MS) || 30_000,
      ),
    });
    if (!res.ok) return null;
    return normalizeManifest(await res.json(), { slug, period, cadence });
  } catch {
    return null;
  }
}

/**
 * Load manifest for an AMC period. Local file wins; else raw GitHub URL.
 * @returns {Promise<{ files: object[], notes: string } | null>}
 */
export async function loadResidentialManifest({
  root = defaultRoot,
  slug,
  period,
  cadence,
}) {
  const local = readLocalManifest(root, slug, period, cadence);
  if (local?.files?.length) {
    return {
      files: local.files,
      notes: `residential manifest (local ${manifestRelPath(slug, period, cadence)})`,
    };
  }
  const remote = await fetchRemoteManifest(slug, period, cadence);
  if (remote?.files?.length) {
    return {
      files: remote.files,
      notes: `residential manifest (remote ${manifestRelPath(slug, period, cadence)})`,
    };
  }
  return null;
}

export function residentialManifestPaths(slug, period, cadence) {
  return {
    local: `data/residential-capture/manifests/${manifestRelPath(slug, period, cadence)}`,
    remote: remoteManifestUrl(slug, period, cadence),
    pages: `https://subscriptionmanager26-png.github.io/fund-disclosures/residential-capture/?amc=${slug}&period=${period}&cadence=${cadence}`,
  };
}
