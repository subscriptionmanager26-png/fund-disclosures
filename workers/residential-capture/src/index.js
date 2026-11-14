/**
 * Residential manifest upload (phone browser → cloud agent).
 * POST /publish  { pin, manifest }
 * GET  /manifest/:amc/:period/:cadence
 * GET  /chain.mjs, /capture-auto.user.js, /done.html
 */
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

const MIME = {
  ".mjs": "application/javascript; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".html": "text/html; charset=utf-8",
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

function kvKey(amc, period, cadence) {
  return `manifest:${amc}:${period}:${cadence}`;
}

async function serveStatic(pathname, env) {
  const key = pathname.replace(/^\//, "");
  if (!key || key.includes("..")) return null;
  const asset = await env.ASSETS?.fetch?.(new Request(`https://assets/${key}`));
  if (!asset?.ok) return null;
  const ext = key.includes(".") ? key.slice(key.lastIndexOf(".")) : "";
  const type = MIME[ext] || "application/octet-stream";
  return new Response(asset.body, {
    headers: { ...CORS, "Content-Type": type },
  });
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS });
    }

    const url = new URL(request.url);
    const staticPaths = ["/chain.mjs", "/done.html", "/capture-auto.user.js"];
    if (staticPaths.includes(url.pathname) || url.pathname.endsWith(".user.js")) {
      const staticRes = await serveStatic(url.pathname, env);
      if (staticRes) return staticRes;
    }

    if (url.pathname === "/publish" && request.method === "POST") {
      let body;
      try {
        body = await request.json();
      } catch {
        return json({ ok: false, error: "invalid_json" }, 400);
      }
      const pin = String(body.pin || "");
      if (!env.CAPTURE_PIN || pin !== env.CAPTURE_PIN) {
        return json({ ok: false, error: "unauthorized" }, 401);
      }
      const manifest = body.manifest;
      if (!manifest?.amc_id || !manifest?.period || !manifest?.cadence) {
        return json({ ok: false, error: "missing_manifest_fields" }, 400);
      }
      if (!Array.isArray(manifest.files) || !manifest.files.length) {
        return json({ ok: false, error: "no_files" }, 400);
      }
      manifest.captured_at = manifest.captured_at || new Date().toISOString();
      const key = kvKey(manifest.amc_id, manifest.period, manifest.cadence);
      await env.MANIFESTS.put(key, JSON.stringify(manifest));
      return json({
        ok: true,
        key,
        files: manifest.files.length,
        url: `${url.origin}/manifest/${manifest.amc_id}/${manifest.period}/${manifest.cadence}`,
      });
    }

    const m = url.pathname.match(
      /^\/manifest\/([^/]+)\/(\d{4}-\d{2})\/(monthly|fortnightly)$/,
    );
    if (m && request.method === "GET") {
      const [, amc, period, cadence] = m;
      const raw = await env.MANIFESTS.get(kvKey(amc, period, cadence));
      if (!raw) return json({ error: "not_found" }, 404);
      return new Response(raw, {
        headers: { ...CORS, "Content-Type": "application/json" },
      });
    }

    if (url.pathname === "/" || url.pathname === "") {
      return json({
        service: "fund-holdings-residential-capture",
        publish: "POST /publish",
        read: "GET /manifest/:amc/:period/:cadence",
        chain: "GET /chain.mjs",
        auto_helper: "GET /capture-auto.user.js",
      });
    }

    return json({ error: "not_found" }, 404);
  },
};
