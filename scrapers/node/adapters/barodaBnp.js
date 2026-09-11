import { fetchText, httpFetch, absUrl } from "../lib/http.js";
import { parsePeriod, periodMatchers } from "../lib/period.js";
import { isPeriodPortfolioFile } from "../lib/portfolioFilter.js";

async function fetchTextRetry(url, attempts = 3) {
  let lastErr;
  for (let i = 0; i < attempts; i++) {
    try {
      const out = await fetchText(url);
      if (out.res.ok) return out;
      lastErr = new Error(`http_${out.res.status}`);
    } catch (e) {
      lastErr = e;
    }
    if (i < attempts - 1) {
      await new Promise((r) => setTimeout(r, 1000 * (i + 1)));
    }
  }
  throw lastErr || new Error("fetch failed");
}

/**
 * Baroda BNP — HTML + CSRF paginated POST /ajax-load-more-documents
 * Mid-month (fortnightly) category id = 23; monthly uses slug/category from page.
 */
export const barodaAdapter = {
  id: "baroda_ajax",
  async listFiles(ctx) {
    if (ctx.type !== "monthly" && ctx.type !== "fortnightly") {
      return { files: [], notes: `unsupported type ${ctx.type}` };
    }
    const pageUrl =
      ctx.amc.disclosures?.[ctx.type]?.page_url ||
      (ctx.type === "fortnightly"
        ? "https://www.barodabnpparibasmf.in/downloads/midmonth-portfolio-scheme"
        : "https://www.barodabnpparibasmf.in/downloads/monthly-portfolio-scheme");
    const p = parsePeriod(ctx.period);
    const matchers = periodMatchers(p);

    let res;
    let text;
    let url;
    try {
      ({ res, text, url } = await fetchTextRetry(pageUrl));
    } catch (e) {
      return { files: [], notes: String(e.message || e) };
    }

    const csrf =
      text.match(
        /name=["']csrf_test_name["']\s+value=["']([^"']+)["']/i,
      )?.[1] ||
      text.match(/csrf_test_name["']\s*:\s*["']([^"']+)["']/i)?.[1] ||
      text.match(/"csrf_hash"\s*:\s*"([^"]+)"/)?.[1];

    const cookie = res.headers.getSetCookie?.()?.join("; ") || "";
    const files = [];
    const seen = new Set();

    const absorb = (fileUrl, title) => {
      const u = absUrl(fileUrl, url);
      if (!u || seen.has(u)) return;
      const blob = `${u} ${title || ""}`;
      // Opaque URLs (YR##.xlsx) rarely contain dates — rely on title text.
      if (!isPeriodPortfolioFile(u, title || "", matchers, ctx.type, p)) {
        // For fortnightly midmonth page, accept period match on title alone
        if (!(title && matchers.periodRe.test(title))) return;
        if (ctx.type === "fortnightly") {
          if (/\bmonthly\b/i.test(title) && !/fortnight|mid/i.test(title)) return;
        }
      }
      seen.add(u);
      files.push({
        url: u,
        filename: filenameWithExt(title, u),
      });
    };

    const collect = (html) => {
      // Card blocks: filename title near href
      const cardRe =
        /class=["'][^"']*file-name[^"']*["'][^>]*>([\s\S]*?)<\/[^>]+>[\s\S]{0,400}?href\s*=\s*["']([^"']+\.xlsx?(?:\?[^"']*)?)["']/gi;
      let m;
      while ((m = cardRe.exec(html))) {
        const title = m[1].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
        absorb(m[2], title);
      }
      // Reverse order: href then nearby title
      const hrefRe = /href\s*=\s*["']([^"']+\.xlsx?(?:\?[^"']*)?)["']([^>]*>)([\s\S]{0,300})/gi;
      while ((m = hrefRe.exec(html))) {
        const around = m[3].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
        absorb(m[1], around.slice(0, 160));
      }
      // JSON ajax payloads sometimes return HTML with data-title
      const titleHref =
        /(?:title|data-title|aria-label)=["']([^"']{8,160})["'][\s\S]{0,120}?href\s*=\s*["']([^"']+\.xlsx?)["']/gi;
      while ((m = titleHref.exec(html))) absorb(m[2], m[1]);
    };

    collect(text);

    // Prefer documented ajax field names for midmonth (send_category=23)
    const maxPages = ctx.type === "fortnightly" ? 12 : 20;
    let stalePages = 0;
    for (let page = 1; page <= maxPages; page++) {
      const body = new URLSearchParams();
      body.set("csrf_test_name", csrf || "");
      body.set("send_year", String(p.year));
      body.set("pagination", String(page));
      body.set("page", String(page));
      body.set("cnt", String(page));
      if (ctx.type === "fortnightly") {
        body.set("send_category", "23");
        body.set("category", "midmonth-portfolio-scheme");
      } else {
        body.set("category", "monthly-portfolio-scheme");
        body.set("send_category", "monthly-portfolio-scheme");
      }

      const r = await httpFetch(
        "https://www.barodabnpparibasmf.in/ajax-load-more-documents",
        {
          method: "POST",
          headers: {
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "x-requested-with": "XMLHttpRequest",
            accept: "application/json, text/javascript, */*; q=0.01",
            referer: pageUrl,
            origin: "https://www.barodabnpparibasmf.in",
            ...(cookie ? { cookie } : {}),
          },
          body,
        },
      );
      const raw = await r.text();
      if (!r.ok) break;
      let html = raw;
      try {
        const j = JSON.parse(raw);
        html = j.data || j.html || j.content || raw;
        if (typeof html !== "string") html = JSON.stringify(html);
      } catch {
        /* plain HTML */
      }
      const before = files.length;
      collect(html);
      if (files.length === before) stalePages++;
      else stalePages = 0;
      if (stalePages >= 2) break;
      if (ctx.type === "fortnightly" && files.length >= 12 && stalePages >= 1) break;
    }

    return {
      files,
      notes: files.length
        ? `year=${p.year} type=${ctx.type} n=${files.length}`
        : "no matching files after pagination",
    };
  },
};

/** Prefer human title but keep spreadsheet extension from the download URL. */
function filenameWithExt(title, fileUrl) {
  let ext = "";
  try {
    const base = decodeURIComponent(
      new URL(fileUrl).pathname.split("/").pop() || "",
    );
    const m = /\.(xlsx?|xlsb)(\?|$)/i.exec(base);
    if (m) ext = `.${m[1].toLowerCase()}`;
  } catch {
    /* ignore */
  }
  const fromUrl = (() => {
    try {
      return decodeURIComponent(
        new URL(fileUrl).pathname.split("/").pop().split("?")[0] || "",
      );
    } catch {
      return "";
    }
  })();
  let name =
    (title && title.replace(/[^\w.\-()+ ]+/g, "").trim().slice(0, 120)) ||
    fromUrl ||
    "portfolio";
  if (ext && !/\.(xlsx?|xlsb)$/i.test(name)) name = `${name}${ext}`;
  return name;
}
