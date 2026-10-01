/**
 * One-tap residential manifest upload (phone → cloud agent).
 * POST /publish  { pin, manifest }
 * GET  /manifest/:amc/:period/:cadence
 */
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
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

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS });
    }

    const url = new URL(request.url);

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
      });
    }

    return json({ error: "not_found" }, 404);
  },
};
