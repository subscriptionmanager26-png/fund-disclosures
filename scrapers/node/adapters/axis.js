import { randomUUID } from "node:crypto";
import { httpFetch } from "../lib/http.js";
import { parsePeriod } from "../lib/period.js";

/** Live statutory CMS on www (transact feed is stale / missing July 2026). */
const CMS_HOST = "https://www.axismf.com";
const TOKEN_URL = `${CMS_HOST}/cms/token`;
const DOCS_URL = `${CMS_HOST}/cms/get-scheme-documents`;
const PAGE_URL = `${CMS_HOST}/statutory-disclosures`;

const SD = {
  monthly: "sdMonthSchemePortfolio",
  fortnightly: "sdFortnightlyPortfolio",
};

/** Legacy: "Monthly Portfolio-31 07 26" */
const MONTHLY_CONSOLIDATED_LEGACY_RE =
  /^Monthly\s+Portfolio-(\d{1,2})\s+(\d{2})\s+(\d{2})$/i;
/** Current CMS: "Monthly Portfolio 31-08-2026" */
const MONTHLY_CONSOLIDATED_ISO_RE =
  /^Monthly\s+Portfolio\s+(\d{1,2})-(\d{2})-(20\d{2})$/i;

/** "Fortnightly Portfolio - 31-07-2026" */
const FN_CONSOLIDATED_RE =
  /^Fortnightly\s+Portfolio\s*-\s*(\d{1,2})-(\d{1,2})-(\d{4})$/i;

/**
 * Axis — www.axismf.com CMS (AMC-direct).
 * Auth: POST /cms/token with browser-id → Bearer for /cms/get-scheme-documents.
 * Response typo: document URL field is `docuementURL`.
 */
export const axisAdapter = {
  id: "axis_cms",
  async listFiles(ctx) {
    if (ctx.type !== "monthly" && ctx.type !== "fortnightly") {
      return { files: [], notes: `unsupported type ${ctx.type}` };
    }
    return listYearMonthDocs(ctx);
  },
};

async function cmsSession() {
  const browserId = randomUUID();
  const res = await httpFetch(TOKEN_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      "browser-id": browserId,
      origin: CMS_HOST,
      referer: PAGE_URL,
    },
    body: "{}",
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`axis token http_${res.status}`);
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    throw new Error("axis token non-json");
  }
  let token = json?.data?.token || json?.token || null;
  if (!token) throw new Error("axis token missing");
  if (!/^Bearer\s/i.test(token)) token = `Bearer ${token}`;
  return { browserId, authorization: token };
}

async function fetchSchemeDocs({ sdID, year, monthName, schemeCode = "Consolidated" }) {
  const { browserId, authorization } = await cmsSession();
  const body = {
    sdType: "yearMonthSchemeDocs",
    sdID,
    year: String(year),
    month: monthName,
    schemeCode,
  };
  const res = await httpFetch(DOCS_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      "browser-id": browserId,
      authorization,
      origin: CMS_HOST,
      referer: PAGE_URL,
    },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) return { ok: false, notes: `http_${res.status}`, docs: [] };
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    return { ok: false, notes: "non-json response", docs: [] };
  }
  if (String(json?.status || "").toLowerCase() !== "success") {
    return {
      ok: false,
      notes: `api_status_${json?.status ?? "null"}`,
      docs: [],
    };
  }
  const docs = json?.data?.documentList;
  return {
    ok: true,
    notes: "ok",
    docs: Array.isArray(docs) ? docs : [],
  };
}

function docUrl(doc) {
  return String(doc?.docuementURL || doc?.documentURL || "").trim() || null;
}

function absUrl(url) {
  if (!url) return null;
  if (/^https?:\/\//i.test(url)) return url;
  try {
    return new URL(url.startsWith("/") ? url : `/${url}`, CMS_HOST).href;
  } catch {
    return null;
  }
}

function filenameFromUrl(url, fallback) {
  try {
    const base = decodeURIComponent(new URL(url).pathname.split("/").pop() || "");
    return base || fallback;
  } catch {
    return fallback;
  }
}

async function listYearMonthDocs(ctx) {
  const p = parsePeriod(ctx.period);
  const sdID = SD[ctx.type];
  const { ok, notes, docs } = await fetchSchemeDocs({
    sdID,
    year: p.year,
    monthName: p.monthName,
  });
  if (!ok) return { files: [], notes };

  const wantYy = String(p.year % 100).padStart(2, "0");
  const wantYyyy = String(p.year);
  const wantMm = p.mm;
  const files = [];
  const seen = new Set();

  for (const doc of docs) {
    const title = String(doc?.documentName || "").trim();
    const url = absUrl(docUrl(doc));
    if (!url || !/\.(xlsx?|xlsb)(\?|$)/i.test(url)) continue;

    if (ctx.type === "monthly") {
      const legacy = MONTHLY_CONSOLIDATED_LEGACY_RE.exec(title);
      const iso = MONTHLY_CONSOLIDATED_ISO_RE.exec(title);
      const m = legacy || iso;
      if (!m) continue;
      const fileMm = legacy ? m[2] : m[2];
      const fileYy = legacy ? m[3] : String(Number(m[3]) % 100).padStart(2, "0");
      const fileYyyy = legacy ? `20${m[3]}` : m[3];
      if (fileMm !== wantMm || fileYy !== wantYy || fileYyyy !== wantYyyy) continue;
      if (seen.has(url)) continue;
      seen.add(url);
      files.push({
        url,
        filename: filenameFromUrl(
          url,
          `Monthly_Portfolio_${m[1].padStart(2, "0")}_${wantMm}_${fileYyyy}.xlsx`
        ),
      });
      break;
    }

    const m = FN_CONSOLIDATED_RE.exec(title);
    if (!m) continue;
    const dd = m[1].padStart(2, "0");
    const mm = m[2].padStart(2, "0");
    const yyyy = m[3];
    if (yyyy !== wantYyyy || mm !== wantMm) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    files.push({
      url,
      filename: filenameFromUrl(
        url,
        `Fortnightly_Portfolio_${dd}_${mm}_${yyyy}.xlsx`
      ),
    });
  }

  if (ctx.type === "fortnightly") {
    files.sort((a, b) => a.filename.localeCompare(b.filename));
  }

  return {
    files,
    notes: files.length
      ? ctx.type === "monthly"
        ? "www cms consolidated monthly"
        : `www cms consolidated FN (${files.length})`
      : ctx.type === "monthly"
        ? "no consolidated monthly for period"
        : "no consolidated FN for period",
  };
}
