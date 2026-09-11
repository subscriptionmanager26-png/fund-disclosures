/**
 * Bridge to scrapers/python/*.py (AMC-direct only).
 * Runs the script with --dry-run when possible to list URLs; otherwise runs a
 * real fetch into a staging tree and reads manifest.json.
 */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, mkdirSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "../../..");
const scriptsDir = join(root, "scrapers/python");
const stagingRoot = join(root, "data/staging/python");

function pythonBin() {
  const venv = join(root, ".venv/bin/python3");
  if (existsSync(venv)) return venv;
  return "python3";
}

function runPython(script, args) {
  const scriptPath = join(scriptsDir, script);
  if (!existsSync(scriptPath)) {
    return { ok: false, error: `missing ${script}`, stdout: "", stderr: "" };
  }
  const proc = spawnSync(pythonBin(), [scriptPath, ...args], {
    cwd: root,
    encoding: "utf8",
    // Helios / slow AMC pages: allow retries inside the Python script within this budget.
    timeout: Number(process.env.PYTHON_REF_TIMEOUT_MS) || 420_000,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });
  return {
    ok: proc.status === 0,
    status: proc.status,
    stdout: proc.stdout || "",
    stderr: proc.stderr || "",
    error: proc.error ? String(proc.error.message || proc.error) : undefined,
  };
}

function readManifest(slug, period) {
  const man = join(stagingRoot, "amcs", slug, period, "manifest.json");
  if (!existsSync(man)) return [];
  try {
    const data = JSON.parse(readFileSync(man, "utf8"));
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

function stripFlags(args, ...flags) {
  const drop = new Set(flags);
  const out = [];
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (drop.has(a)) {
      if (a === "--as-of") i++;
      continue;
    }
    out.push(a);
  }
  return out;
}

function runWithArgFallback(script, baseArgs, forceRealFetch) {
  const argSets = [
    forceRealFetch ? baseArgs : ["--dry-run", ...baseArgs],
    baseArgs,
    stripFlags(baseArgs, "--as-of"),
    stripFlags(baseArgs, "--fortnightly", "--as-of"),
  ];
  const seen = new Set();
  for (const args of argSets) {
    const key = args.join("\0");
    if (seen.has(key)) continue;
    seen.add(key);
    const result = runPython(script, args);
    if (result.ok) return result;
    const msg = `${result.stderr}\n${result.stdout}`;
    const unknown =
      /unrecognized arguments:|no such option/i.test(msg) ||
      /error:.*--dry-run|--fortnightly|--as-of/i.test(msg);
    if (!unknown) return result;
  }
  return runPython(script, baseArgs);
}

/**
 * @param {object} cfg
 * @param {string} cfg.script e.g. fetch_bandhan.py
 * @param {string} cfg.slug folder slug under amcs/ in the python toolkit
 * @param {string[]} [cfg.extraArgs]
 */
export function createPythonRefAdapter(cfg) {
  return {
    id: "python_ref",
    script: cfg.script,
    slug: cfg.slug,
    async listFiles(ctx) {
      if (ctx.type !== "monthly" && ctx.type !== "fortnightly") {
        return { files: [], notes: `python_ref unsupported type ${ctx.type}` };
      }
      mkdirSync(stagingRoot, { recursive: true });

      const extra = [...(cfg.extraArgs || [])];
      // LIC consolidated fortnightly id; POSTs use numeric month from YYYY-MM
      if (ctx.type === "fortnightly" && cfg.script === "fetch_lic.py") {
        if (!extra.some((a) => String(a).startsWith("--consolidated-id"))) {
          extra.push("--scope", "consolidated", "--consolidated-id", "638");
        }
      }
      if (ctx.type === "fortnightly") {
        extra.push("--fortnightly");
        if (ctx.storageKey && /^\d{4}-\d{2}-\d{2}$/.test(ctx.storageKey)) {
          extra.push("--as-of", ctx.storageKey);
        }
      }

      const baseArgs = ["--months", ctx.period, "--root", stagingRoot, ...extra];

      // Hosts where Node fetch fails (TLS) or Akamai blocks plain HTTP (Edelweiss CDN).
      const forceRealFetch = [
        "fetch_unifi.py",
        "fetch_edelweiss.py",
        "fetch_navi.py",
        "fetch_union.py",
      ].includes(cfg.script);

      // Prefer dry-run if the script supports it (unless we must stage files)
      let result = runWithArgFallback(cfg.script, baseArgs, forceRealFetch);

      if (!result.ok) {
        return {
          files: [],
          notes: `python_exit_${result.status ?? "x"}: ${(result.stderr || result.stdout || result.error || "").slice(0, 240)}`,
        };
      }

      const rows = readManifest(cfg.slug, ctx.period);
      const stageDir = join(stagingRoot, "amcs", cfg.slug, ctx.period);
      const files = [];
      const seen = new Set();
      for (const row of rows) {
        const url = row.download_url || row.url;
        if (!url || seen.has(url)) continue;
        if (row.error) continue;
        seen.add(url);
        const filename =
          row.saved_as || decodeURIComponent(url.split("/").pop());
        const localPath = join(stageDir, filename);
        files.push({
          url,
          filename,
          ...(existsSync(localPath) ? { localPath } : {}),
        });
      }

      // Fallback: files already written on disk (non-dry-run scripts)
      if (!files.length) {
        if (existsSync(stageDir)) {
          for (const name of readdirSync(stageDir)) {
            if (name === "manifest.json") continue;
            if (!/\.(xlsx?|xlsb|csv|zip)$/i.test(name)) continue;
            files.push({
              url: `file://${join(stageDir, name)}`,
              filename: name,
              localPath: join(stageDir, name),
            });
          }
        }
      }

      return {
        files,
        notes: files.length
          ? `python ${cfg.script} (${ctx.type})`
          : `python ok but empty (${cfg.script}, ${ctx.type})`,
      };
    },
  };
}
