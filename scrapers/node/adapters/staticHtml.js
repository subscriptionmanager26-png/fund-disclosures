import { fetchText, absUrl } from "../lib/http.js";
import { periodMatchers, parsePeriod } from "../lib/period.js";
import { isPeriodPortfolioFile, FILE_EXT } from "../lib/portfolioFilter.js";

function extractLinks(html, base) {
  const links = [];
  const re = /<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;
  let m;
  while ((m = re.exec(html))) {
    const url = absUrl(m[1].trim(), base);
    if (!url) continue;
    const text = m[2].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    links.push({ url, text });
  }
  for (const b of html.matchAll(/https?:\/\/[^\s"'<>\\]+/g)) {
    const url = b[0].replace(/[),.;]+$/, "");
    if (FILE_EXT.test(url)) links.push({ url, text: "" });
  }
  return links;
}

/** Pull portfolio asset URLs out of Next.js / JSON-ish page payloads. */
function normalizeAssetUrl(raw, base) {
  let s = String(raw || "").replace(/\\u002F/g, "/").trim();
  if (s.startsWith("//")) s = `https:${s}`;
  // Encode spaces in path while preserving already-encoded sequences
  try {
    const u = new URL(s, base);
    u.pathname = u.pathname
      .split("/")
      .map((seg) => encodeURIComponent(decodeURIComponent(seg)))
      .join("/");
    return u.href;
  } catch {
    return absUrl(s.replace(/ /g, "%20"), base);
  }
}

function extractJsonAssetLinks(html, base) {
  const links = [];
  const next = html.match(
    /<script[^>]*id=["']__NEXT_DATA__["'][^>]*>([\s\S]*?)<\/script>/i,
  );
  const blobs = next ? [next[1]] : [html];
  // Allow spaces inside asset paths (Zerodha CDN)
  const urlRe =
    /(?:https?:)?\/\/[^"'<>]+?\.(?:xlsx?|xlsb|csv|zip)|\/[^"'<>]*?(?:Fortnightly|fortnightly|Portfolio|portfolio)[^"'<>]*?\.(?:xlsx?|xlsb|csv|zip)/gi;
  for (const blob of blobs) {
    let m;
    urlRe.lastIndex = 0;
    while ((m = urlRe.exec(blob))) {
      const url = normalizeAssetUrl(m[0], base);
      if (!url || !FILE_EXT.test(url)) continue;
      let text = "";
      try {
        text = decodeURIComponent(new URL(url).pathname.split("/").pop() || "");
      } catch {
        text = url.split("/").pop() || "";
      }
      links.push({ url, text });
    }
  }
  const qre =
    /["']((?:https?:)?\/\/?[^"']*?(?:[Ff]ortnightly|[Pp]ortfolio)[^"']*?\.(?:xlsx?|xlsb|csv|zip))["']/g;
  let qm;
  while ((qm = qre.exec(html))) {
    const url = normalizeAssetUrl(qm[1], base);
    if (url) {
      links.push({
        url,
        text: decodeURIComponent((qm[1].split("/").pop() || "").replace(/\+/g, "%20")),
      });
    }
  }
  return links;
}

/**
 * Generic adapter: GET hub HTML, collect period+portfolio file links.
 */
export const staticHtmlAdapter = {
  id: "static_html",
  /**
   * @param {{ amc: object, type: string, period: string }} ctx
   */
  async listFiles(ctx) {
    const pageUrl = ctx.amc.disclosures?.[ctx.type]?.page_url;
    if (!pageUrl) return { files: [], notes: "no page_url" };

    if (FILE_EXT.test(pageUrl)) {
      const p = parsePeriod(ctx.period);
      const matchers = periodMatchers(p);
      if (isPeriodPortfolioFile(pageUrl, "", matchers, ctx.type, p)) {
        return {
          files: [
            {
              url: pageUrl,
              filename: decodeURIComponent(
                new URL(pageUrl).pathname.split("/").pop() || "file",
              ),
            },
          ],
        };
      }
      return { files: [], notes: "direct url does not match period/type" };
    }

    const fetchOpts = ctx.amc.fetch?.[ctx.type]?.insecure_ssl
      ? { insecure: true }
      : {};
    const { res, text, url } = await fetchText(pageUrl, fetchOpts);
    if (!res.ok) return { files: [], notes: `http_${res.status}` };

    const p = parsePeriod(ctx.period);
    const matchers = periodMatchers(p);
    const seen = new Set();
    const files = [];
    const candidates = [
      ...extractLinks(text, url),
      ...extractJsonAssetLinks(text, url),
    ];
    for (const link of candidates) {
      if (seen.has(link.url)) continue;
      if (!isPeriodPortfolioFile(link.url, link.text, matchers, ctx.type, p))
        continue;
      seen.add(link.url);
      let filename = (link.text || "").slice(0, 120);
      try {
        const u = new URL(link.url);
        const fromQuery = u.searchParams.get("file");
        filename =
          (fromQuery && decodeURIComponent(fromQuery)) ||
          decodeURIComponent(u.pathname.split("/").pop() || "") ||
          filename;
      } catch {
        /* keep text */
      }
      files.push({ url: link.url, filename: filename || "file.xlsx" });
    }
    return {
      files,
      notes: files.length ? undefined : "no matching files in HTML",
    };
  },
};
