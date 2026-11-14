/**
 * Runs on AMC sites (navi.com, unionmf.com, …) when loaded via bookmarklet or
 * Tampermonkey auto-resume. Executes in the PAGE origin so APIs + cookies work.
 */
function loadState() {
  try {
    return JSON.parse(window.name || "{}");
  } catch {
    return {};
  }
}

function saveState(state) {
  window.name = JSON.stringify(state);
}

function moduleParams() {
  if (typeof import.meta !== "undefined" && import.meta.url) {
    try {
      return new URL(import.meta.url).searchParams;
    } catch {
      /* ignore */
    }
  }
  return new URLSearchParams("");
}

export function cfg() {
  const q = moduleParams();
  const state = loadState();
  const w = (q.get("w") || state.w || "").replace(/\/$/, "");
  const p = q.get("p") || state.p || "";
  if (!w || !p) {
    throw new Error("Missing worker URL or PIN — open the capture page and save setup");
  }
  return { worker: w, pin: p };
}

export function periodYm() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function fortnightValue() {
  const d = new Date();
  const names = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];
  const m = names[d.getMonth()];
  const end = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
  return d.getDate() <= 18 ? `${m} 1-15` : `${m} 16-${end}`;
}

export function asOfDd() {
  const d = new Date();
  const y = d.getFullYear();
  const mo = String(d.getMonth() + 1).padStart(2, "0");
  return d.getDate() <= 18
    ? `${y}-${mo}-15`
    : `${y}-${mo}-${String(new Date(y, d.getMonth() + 1, 0).getDate()).padStart(2, "0")}`;
}

export function fy(y, mo) {
  return mo >= 4 ? `${y}-${y + 1}` : `${y - 1}-${y}`;
}

export function fname(url) {
  try {
    return decodeURIComponent(new URL(url).pathname.split("/").pop() || "file.xlsx");
  } catch {
    return String(url).split("/").pop() || "file.xlsx";
  }
}

function progressEl() {
  let el = document.getElementById("rh-capture-progress");
  if (!el) {
    el = document.createElement("div");
    el.id = "rh-capture-progress";
    el.style.cssText =
      "position:fixed;inset:0;z-index:2147483646;background:rgba(15,23,42,.92);" +
      "color:#ecfdf5;font:600 1rem/1.4 system-ui,sans-serif;display:flex;" +
      "align-items:center;justify-content:center;padding:1.5rem;text-align:center;";
    document.documentElement.appendChild(el);
  }
  return el;
}

export function showProgress(msg) {
  progressEl().textContent = msg;
}

export function hideProgress() {
  document.getElementById("rh-capture-progress")?.remove();
}

export async function publish(manifest) {
  const { worker, pin } = cfg();
  const r = await fetch(`${worker}/publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin, manifest }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || !data.ok) throw new Error(data.error || `Upload ${r.status}`);
  return data;
}

export async function captureNavi() {
  const html = document.documentElement.innerHTML;
  const nonce =
    html.match(/"nonce"\s*:\s*"([a-f0-9]+)"/i)?.[1] ||
    html.match(/wp-nonce["']?\s*[:=]\s*["']([a-f0-9]+)/i)?.[1];
  if (!nonce) {
    throw new Error("Navi: wp-nonce not found — wait for the portfolio page to finish loading");
  }
  const period = periodYm();
  const [y, mo] = period.split("-").map(Number);
  const val = fortnightValue();
  const r = await fetch("https://navi.com/wp-json/nv/v1/documents", {
    method: "POST",
    credentials: "include",
    headers: {
      "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
      "wp-nonce": nonce,
      "x-requested-with": "XMLHttpRequest",
    },
    body: new URLSearchParams({
      financial_year: fy(y, mo),
      value: val,
      category: "885",
      type: "Fortnightly",
      order: "DESC",
    }),
  });
  const j = await r.json();
  if (!j.success) throw new Error("Navi API failed");
  const files = [];
  for (const row of j.data || []) {
    const urls = Array.isArray(row.url) ? row.url : row.url ? [row.url] : [];
    for (const u of urls) {
      if (!u) continue;
      files.push({
        title: row.title || "",
        download_url: String(u).replace(/\\\//g, "/"),
        saved_as: fname(u),
      });
    }
  }
  if (!files.length) throw new Error("Navi: no files for this fortnight");
  return {
    amc_id: "navi-mutual-fund",
    period,
    cadence: "fortnightly",
    captured_at: new Date().toISOString(),
    captured_from: "mobile chain",
    files,
  };
}

export async function captureUnion() {
  const period = periodYm();
  const asOf = asOfDd();
  const asOfTail = asOf.slice(8, 10) + asOf.slice(5, 7) + asOf.slice(0, 4);
  const re = new RegExp(
    `fortnightly-portfolio[^"'\\s<>]*-${asOf.slice(8, 10)}-${asOf.slice(5, 7)}-${asOf.slice(0, 4)}[^"'\\s<>]*\\.(?:xlsx|xls|xlsb)`,
    "i",
  );
  const pageUrl =
    "https://www.unionmf.com/about-us/downloads/fortnightly-portfolio";
  const apiUrl =
    "https://www.unionmf.com/api/downloads/documents?$filter=FolderId%20eq%20b6cafa81-47fb-4935-bc54-b752b9e7d797&$orderby=Yearfilter%20desc";
  let urls = [];
  try {
    const pr = await fetch(pageUrl, { credentials: "include" });
    const html = await pr.text();
    const found = html.match(
      /https?:\/\/www\.unionmf\.com\/docs\/default-source\/downloads\/scheme-disclosures\/portfolios-disclosure\/fortnightly-portfolio\/[^"'\\s<>]+\.(?:xlsx|xls|xlsb)/gi,
    );
    if (found) urls = found.filter((u) => re.test(u) || u.includes(asOfTail));
  } catch {
    /* try API */
  }
  if (!urls.length) {
    const ar = await fetch(apiUrl, { credentials: "include" });
    const data = await ar.json();
    for (const doc of data.value || []) {
      const u = String(doc.Url || "");
      if (/fortnight/i.test(u) && re.test(u)) urls.push(u);
    }
  }
  urls = [...new Set(urls)];
  if (!urls.length) throw new Error("Union: no fortnightly files for " + asOf);
  const files = urls.map((u) => ({
    title: fname(u),
    download_url: u,
    saved_as: fname(u),
  }));
  return {
    amc_id: "union-mutual-fund",
    period,
    cadence: "fortnightly",
    as_of: asOf,
    captured_at: new Date().toISOString(),
    captured_from: "mobile chain",
    files,
  };
}

export async function captureAbakkus() {
  const period = periodYm();
  const [y, mo] = period.split("-").map(Number);
  const pageUrl = "https://www.abakkusmf.com/statutory-disclosures.html";
  const pr = await fetch(pageUrl, { credentials: "include" });
  const html = await pr.text();
  const m = html.match(
    /<script[^>]+id=["']verticals-data["'][^>]*>(\[.*?\])<\/script>/is,
  );
  if (!m) throw new Error("Abakkus: verticals-data not found");
  const verticals = JSON.parse(m[1]);
  let target = null;
  for (const v of verticals) {
    const t = String(v.title || "").toLowerCase();
    if (t.includes("fortnightly") && t.includes("portfolio")) {
      target = v;
      break;
    }
  }
  if (!target) throw new Error("Abakkus: fortnightly vertical missing");
  const files = [];
  const seen = new Set();
  for (const sec of target.sections || []) {
    for (const sub of sec.subSections || []) {
      for (const item of sub.items || []) {
        const title = String(item.title || "");
        const media = item.downloadMedia || {};
        let rel = String(media.url || item.downloadUrl || "");
        if (!rel) continue;
        const url = rel.startsWith("http")
          ? rel
          : `https://www.abakkusmf.com/${rel.replace(/^\//, "")}`;
        const monthMatch = title.match(
          /(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{4})/i,
        );
        if (!monthMatch) continue;
        const months = {
          jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
          jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
        };
        const mm = months[monthMatch[1].slice(0, 3).toLowerCase()];
        const yy = Number(monthMatch[2]);
        if (yy !== y || mm !== mo) continue;
        if (seen.has(url)) continue;
        seen.add(url);
        files.push({ title, download_url: url, saved_as: fname(url) });
      }
    }
  }
  if (!files.length) throw new Error("Abakkus: no files for " + period);
  return {
    amc_id: "abakkus-mutual-fund",
    period,
    cadence: "fortnightly",
    captured_at: new Date().toISOString(),
    captured_from: "mobile chain",
    files,
  };
}

/** AMCs that block datacenter IPs — captured from the phone browser on each origin. */
export const AMC_CHAIN = [
  {
    id: "navi",
    label: "Navi",
    host: /navi\.com$/i,
    startUrl: "https://navi.com/mutual-fund/downloads/portfolio",
    capture: captureNavi,
  },
  {
    id: "union",
    label: "Union",
    host: /unionmf\.com$/i,
    startUrl:
      "https://www.unionmf.com/about-us/downloads/fortnightly-portfolio",
    capture: captureUnion,
  },
  {
    id: "abakkus",
    label: "Abakkus",
    host: /abakkusmf\.com$/i,
    startUrl: "https://www.abakkusmf.com/statutory-disclosures.html",
    capture: captureAbakkus,
  },
];

function currentAmc() {
  const state = loadState();
  const step = state.step || "";
  const host = location.hostname;
  const idx = step
    ? AMC_CHAIN.findIndex((a) => a.id === step)
    : AMC_CHAIN.findIndex((a) => a.host.test(host));
  if (idx < 0) return { idx: -1, amc: null };
  const amc = AMC_CHAIN[idx];
  if (!amc.host.test(host)) return { idx, amc, wrongHost: true };
  return { idx, amc, wrongHost: false };
}

export async function runChain() {
  const { worker, pin } = cfg();
  const state = loadState();
  if (!state.w) saveState({ w: worker, p: pin, step: state.step || "" });

  const { idx, amc, wrongHost } = currentAmc();

  if (idx < 0 || wrongHost) {
    const first = AMC_CHAIN[0];
    saveState({ w: worker, p: pin, step: first.id });
    showProgress(`Opening ${first.label}…`);
    location.replace(first.startUrl);
    return { redirect: first.id };
  }

  try {
    showProgress(`${amc.label}: calling API from your phone…`);
    const manifest = await amc.capture();
    showProgress(`${amc.label}: uploading ${manifest.files.length} files…`);
    const pub = await publish(manifest);

    const next = AMC_CHAIN[idx + 1];
    if (!next) {
      window.name = "";
      hideProgress();
      location.replace(`${worker}/done.html?amcs=${AMC_CHAIN.length}`);
      return { done: true, published: pub, files: manifest.files.length };
    }

    saveState({ w: worker, p: pin, step: next.id });
    showProgress(`${amc.label} done → opening ${next.label}…`);
    location.replace(next.startUrl);
    return { next: next.id, published: pub, files: manifest.files.length };
  } catch (e) {
    hideProgress();
    throw e;
  }
}
