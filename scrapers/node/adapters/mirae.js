import { httpFetch } from "../lib/http.js";
import { parsePeriod, periodMatchers } from "../lib/period.js";
import { isPeriodPortfolioFile } from "../lib/portfolioFilter.js";

/**
 * Mirae Asset Sitefinity AjaxService portfolio tab.
 * POST /AjaxService/GetDownloadsData { modulename: portfolio_tab1, pgno, pgsize }
 */
export const miraeAdapter = {
  id: "mirae_ajax",
  async listFiles(ctx) {
    if (ctx.type !== "monthly" && ctx.type !== "fortnightly") {
      return { files: [], notes: `unsupported type ${ctx.type}` };
    }
    const p = parsePeriod(ctx.period);
    const matchers = periodMatchers(p);
    const endpoint =
      "https://www.miraeassetmf.co.in/AjaxService/GetDownloadsData";

    const files = [];
    const seen = new Set();
    let page = 1;
    const pageSize = 50;
    let emptyStreak = 0;

    while (page <= 40 && emptyStreak < 2) {
      const res = await httpFetch(endpoint, {
        method: "POST",
        headers: {
          "content-type": "application/json; charset=UTF-8",
          "x-requested-with": "XMLHttpRequest",
          referer: "https://www.miraeassetmf.co.in/downloads/portfolio",
          origin: "https://www.miraeassetmf.co.in",
        },
        body: JSON.stringify({
          modulename: ctx.type === "fortnightly" ? "portfolio_tab3" : "portfolio_tab1",
          pgno: page,
          pgsize: pageSize,
        }),
      });
      const text = await res.text();
      if (!res.ok) return { files, notes: `http_${res.status} page ${page}` };

      let data;
      try {
        data = JSON.parse(text);
      } catch {
        return { files, notes: "non-json response" };
      }

      const items =
        data?.Data || data?.data || data?.Documents || data?.documents || [];
      const list = Array.isArray(items) ? items : [];
      if (!list.length) break;

      let matchedOnPage = 0;
      for (const item of list) {
        const blob = JSON.stringify(item);
        const urlMatch = blob.match(
          /https?:\/\/[^"\\]+\.xlsx?(?:\?[^"\\]*)?/i,
        );
        const pathMatch = blob.match(
          /\/docs\/default-source\/portfolios\/[^"\\]+\.xlsx?/i,
        );
        const raw = urlMatch?.[0] || pathMatch?.[0];
        if (!raw) continue;
        const url = raw.startsWith("http")
          ? raw
          : new URL(raw, "https://www.miraeassetmf.co.in").href;
        if (seen.has(url)) continue;
        if (!isPeriodPortfolioFile(url, blob, matchers, ctx.type, p)) continue;
        seen.add(url);
        matchedOnPage++;
        files.push({
          url,
          filename: decodeURIComponent(url.split("/").pop().split("?")[0]),
        });
      }

      // Mirae lists newest first; stop once a full page has zero matches after we already found some
      if (files.length && matchedOnPage === 0) emptyStreak++;
      else emptyStreak = 0;
      page++;
    }

    return {
      files,
      notes: files.length
        ? `scanned ${page - 1} pages`
        : `no period matches in ${ctx.type === "fortnightly" ? "portfolio_tab3" : "portfolio_tab1"}`,
    };
  },
};
