/** Public fund holdings on GitHub raw, pinned by commit SHA (raw @main lags on large files). */

export const HOLDINGS_REPO_OWNER = 'kushagra-agarwal-a';
export const HOLDINGS_REPO_NAME = 'fund-holdings-data';
export const HOLDINGS_BRANCH = 'main';

export const HOLDINGS_CDN_BASE = `https://raw.githubusercontent.com/${HOLDINGS_REPO_OWNER}/${HOLDINGS_REPO_NAME}/${HOLDINGS_BRANCH}`;

export const HOLDINGS_META_URL = `${HOLDINGS_CDN_BASE}/meta.json`;
/** @deprecated Prefer resolveHoldingsBase() — branch tip may lag. */
export const HOLDINGS_CATALOG_URL = `${HOLDINGS_CDN_BASE}/catalog/amfi-lookup.json`;
export const HOLDINGS_PUBLIC_CATALOG_URL = `${HOLDINGS_CDN_BASE}/catalog/amfi-public.json`;
export const HOLDINGS_FILINGS_URL = `${HOLDINGS_CDN_BASE}/catalog/filings.json`;

export const HOLDINGS_CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

export function jsonResponse(
  body: unknown,
  status = 200,
  extraHeaders: Record<string, string> = {},
) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      ...HOLDINGS_CORS,
      ...extraHeaders,
    },
  });
}

export function normalizeAmfi(raw: string) {
  const code = String(raw || '').trim();
  if (!/^\d{4,8}$/.test(code)) return '';
  return code;
}

/** Mutual fund scheme ISIN (INF + 9 chars). */
export function normalizeFundIsin(raw: string) {
  const isin = String(raw || '')
    .trim()
    .toUpperCase();
  if (!/^INF[A-Z0-9]{9}$/.test(isin)) return '';
  return isin;
}

/** Accept YYYY-MM-DD, or YYYY-MM → month-end. */
export function normalizeAsOf(raw: string | null | undefined): string {
  const s = String(raw || '').trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  if (/^\d{4}-\d{2}$/.test(s)) {
    const [y, m] = s.split('-').map(Number);
    const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
    return `${s}-${String(last).padStart(2, '0')}`;
  }
  return '';
}

type CatalogRow = {
  amfi_code?: string;
  portfolio_id?: string | null;
  portfolio_url?: string | null;
  portfolio_key?: string | null;
  has_holdings?: boolean;
  name?: string;
  amc_name?: string;
  parent_name?: string;
  parent_amfi?: string;
  nav?: string | number | null;
  nav_date?: string | null;
  isin?: string | null;
  category?: string | null;
  available_as_of?: string[];
  latest_as_of?: string | null;
  [key: string]: unknown;
};

export function publicCatalogLookup(
  catalog: Record<string, CatalogRow>,
): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {};
  for (const [code, row] of Object.entries(catalog)) {
    out[code] = {
      amfi_code: row.amfi_code ?? code,
      parent_name: row.parent_name ?? null,
      available_as_of: Array.isArray(row.available_as_of)
        ? row.available_as_of
        : [],
      latest_as_of: row.latest_as_of ?? null,
    };
  }
  return out;
}

const FUND_ISIN_RE = /^INF[A-Z0-9]{9}$/;
const NUMERIC_NAV_RE = /^\d+(?:\.\d+)?$/;

function isFundIsin(value: unknown): boolean {
  return typeof value === 'string' && FUND_ISIN_RE.test(value.toUpperCase());
}

function isSchemeNav(value: unknown): boolean {
  if (value == null || value === '') return false;
  const s = String(value).replace(/,/g, '').trim();
  if (isFundIsin(s)) return false;
  return NUMERIC_NAV_RE.test(s);
}

function pickNav(...candidates: unknown[]): string | number | null {
  for (const value of candidates) {
    if (isSchemeNav(value)) return value as string | number;
  }
  return null;
}

function pickIsin(...candidates: unknown[]): string | null {
  for (const value of candidates) {
    if (isFundIsin(value)) return String(value).toUpperCase();
  }
  return null;
}

/** Merge scheme card from catalog + portfolio; never surface swapped NAV/ISIN columns. */
export function mergeSchemeFromCatalog(
  amfi: string,
  row: CatalogRow,
  portfolioScheme: Record<string, unknown> | null | undefined,
) {
  const fallback = portfolioScheme && typeof portfolioScheme === 'object' ? portfolioScheme : {};
  return {
    amfi_code: amfi,
    name: row.name ?? fallback.name ?? null,
    amc_name: row.amc_name ?? fallback.amc_name ?? null,
    parent_name: row.parent_name ?? fallback.parent_name ?? null,
    parent_amfi: row.parent_amfi ?? fallback.parent_amfi ?? null,
    nav: pickNav(row.nav, fallback.nav),
    nav_date: row.nav_date ?? fallback.nav_date ?? null,
    isin: pickIsin(row.isin, row.nav, fallback.isin, fallback.nav),
    category: row.category ?? fallback.category ?? null,
  };
}

export async function holdingsPortfolioUrlForRow(
  row: CatalogRow,
  portfolioId: string,
  asOf?: string | null,
) {
  const day = normalizeAsOf(asOf || '') || resolveLatestAsOf(row);
  if (!day) {
    throw new Error('Catalog has no latest_as_of or available_as_of for this scheme');
  }
  return holdingsPortfolioUrl(portfolioId, day);
}

type HoldingsMeta = {
  commit?: string;
  raw_base?: string;
  generated_at?: string;
  [key: string]: unknown;
};

export type HoldingsFiling = {
  as_of: string;
  cadence?: string;
  portfolio_count?: number;
  [key: string]: unknown;
};

let pinMemory: { at: number; base: string; commit: string | null } | null =
  null;
let catalogMemory: { at: number; data: Record<string, CatalogRow> } | null =
  null;
let publicCatalogMemory: {
  at: number;
  data: Record<string, Record<string, unknown>>;
} | null = null;
let isinIndexMemory: {
  at: number;
  data: Record<string, { amfi: string; row: CatalogRow }>;
} | null = null;
let filingsMemory: { at: number; data: { filings: HoldingsFiling[] } } | null =
  null;
const PIN_TTL_MS = 5 * 60 * 1000;
const CATALOG_TTL_MS = 5 * 60 * 1000;

function rawBaseForCommit(commit: string) {
  return `https://raw.githubusercontent.com/${HOLDINGS_REPO_OWNER}/${HOLDINGS_REPO_NAME}/${commit}`;
}

function isCommitSha(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{7,40}$/i.test(value);
}

async function resolveTipCommit(): Promise<string | null> {
  const url = `https://api.github.com/repos/${HOLDINGS_REPO_OWNER}/${HOLDINGS_REPO_NAME}/commits/${HOLDINGS_BRANCH}`;
  const res = await fetch(url, {
    headers: {
      Accept: 'application/vnd.github+json',
      'User-Agent': 'PocketEdgeHoldings/1.0',
      'Cache-Control': 'no-cache',
    },
  });
  if (!res.ok) return null;
  const body = (await res.json()) as { sha?: string };
  return isCommitSha(body.sha) ? body.sha : null;
}

async function resolveMetaCommit(): Promise<string | null> {
  const res = await fetch(HOLDINGS_META_URL, {
    headers: { Accept: 'application/json', 'Cache-Control': 'no-cache' },
  });
  if (!res.ok) return null;
  const meta = (await res.json()) as HoldingsMeta;
  if (isCommitSha(meta.commit)) return meta.commit;
  if (typeof meta.raw_base === 'string') {
    const m = meta.raw_base.match(/\/([0-9a-f]{7,40})\/?$/i);
    if (m) return m[1];
  }
  return null;
}

export async function resolveHoldingsBase(): Promise<{
  base: string;
  commit: string | null;
}> {
  const now = Date.now();
  if (pinMemory && now - pinMemory.at < PIN_TTL_MS) {
    return { base: pinMemory.base, commit: pinMemory.commit };
  }

  // Prefer meta.json commit: the pipeline pins catalog + portfolios together on push.
  let commit: string | null = null;
  try {
    commit = await resolveMetaCommit();
  } catch {
    commit = null;
  }
  if (!commit) {
    try {
      commit = await resolveTipCommit();
    } catch {
      commit = null;
    }
  }

  const base = commit ? rawBaseForCommit(commit) : HOLDINGS_CDN_BASE;
  pinMemory = { at: now, base, commit };
  return { base, commit };
}

/** Newest as-of date for a catalog row (catalog-driven latest; no portfolios/latest/). */
export function resolveLatestAsOf(row: CatalogRow | null | undefined): string {
  if (!row || typeof row !== 'object') return '';
  const explicit = normalizeAsOf(String(row.latest_as_of ?? ''));
  if (explicit) return explicit;
  const dates = Array.isArray(row.available_as_of)
    ? row.available_as_of.map((d) => normalizeAsOf(String(d))).filter(Boolean)
    : [];
  if (!dates.length) return '';
  dates.sort((a, b) => b.localeCompare(a));
  return dates[0] ?? '';
}

export async function holdingsPortfolioUrl(
  portfolioId: string,
  asOf: string,
) {
  const { base } = await resolveHoldingsBase();
  const id = encodeURIComponent(portfolioId);
  const day = normalizeAsOf(asOf);
  if (!day) {
    throw new Error('as_of required to resolve portfolio URL');
  }
  return `${base}/portfolios/asof/${day}/${id}.json`;
}

export async function loadAmfiPublicCatalog(): Promise<
  Record<string, Record<string, unknown>>
> {
  const now = Date.now();
  if (publicCatalogMemory && now - publicCatalogMemory.at < CATALOG_TTL_MS) {
    return publicCatalogMemory.data;
  }

  const { base } = await resolveHoldingsBase();
  const catalogUrl = `${base}/catalog/amfi-public.json`;

  const cache =
    typeof caches !== 'undefined'
      ? ((caches as unknown as { default?: Cache }).default ?? null)
      : null;
  const cacheKey = new Request(catalogUrl);
  if (cache) {
    const hit = await cache.match(cacheKey);
    if (hit?.ok) {
      const data = (await hit.json()) as Record<string, Record<string, unknown>>;
      publicCatalogMemory = { at: now, data };
      return data;
    }
  }

  const res = await fetch(catalogUrl, {
    headers: { Accept: 'application/json', 'Cache-Control': 'no-cache' },
  });
  if (res.ok) {
    const data = (await res.json()) as Record<string, Record<string, unknown>>;
    publicCatalogMemory = { at: now, data };
    if (cache) {
      const toStore = new Response(JSON.stringify(data), {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'Cache-Control': 'public, max-age=300',
        },
      });
      await cache.put(cacheKey, toStore).catch(() => {});
    }
    return data;
  }

  const full = await loadAmfiCatalog();
  const data = publicCatalogLookup(full);
  publicCatalogMemory = { at: now, data };
  return data;
}

export async function loadAmfiCatalog(): Promise<Record<string, CatalogRow>> {
  const now = Date.now();
  if (catalogMemory && now - catalogMemory.at < CATALOG_TTL_MS) {
    return catalogMemory.data;
  }

  const { base } = await resolveHoldingsBase();
  const catalogUrl = `${base}/catalog/amfi-lookup.json`;

  // Vercel Edge / Workers expose caches.default; DOM CacheStorage typings omit it.
  const cache =
    typeof caches !== 'undefined'
      ? ((caches as unknown as { default?: Cache }).default ?? null)
      : null;
  const cacheKey = new Request(catalogUrl);
  if (cache) {
    const hit = await cache.match(cacheKey);
    if (hit?.ok) {
      const data = (await hit.json()) as Record<string, CatalogRow>;
      catalogMemory = { at: now, data };
      return data;
    }
  }

  const res = await fetch(catalogUrl, {
    headers: { Accept: 'application/json', 'Cache-Control': 'no-cache' },
  });
  if (!res.ok) {
    throw new Error(`catalog fetch failed: ${res.status}`);
  }
  const data = (await res.json()) as Record<string, CatalogRow>;
  catalogMemory = { at: now, data };
  if (cache) {
    const toStore = new Response(JSON.stringify(data), {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'public, max-age=300',
      },
    });
    await cache.put(cacheKey, toStore).catch(() => {});
  }
  return data;
}

function buildIsinIndex(
  catalog: Record<string, CatalogRow>,
): Record<string, { amfi: string; row: CatalogRow }> {
  const out: Record<string, { amfi: string; row: CatalogRow }> = {};
  for (const [amfi, row] of Object.entries(catalog)) {
    const isin = pickIsin(row.isin, row.nav);
    if (isin && !out[isin]) out[isin] = { amfi, row };
  }
  return out;
}

export async function lookupCatalogByIsin(
  isin: string,
): Promise<{ amfi: string; row: CatalogRow } | null> {
  const normalized = normalizeFundIsin(isin);
  if (!normalized) return null;
  const catalog = await loadAmfiCatalog();
  const catalogAt = catalogMemory?.at ?? 0;
  if (!isinIndexMemory || isinIndexMemory.at !== catalogAt) {
    isinIndexMemory = { at: catalogAt, data: buildIsinIndex(catalog) };
  }
  return isinIndexMemory.data[normalized] ?? null;
}

export async function loadHoldingsFilings(): Promise<{
  filings: HoldingsFiling[];
  generated_at?: string;
}> {
  const now = Date.now();
  if (filingsMemory && now - filingsMemory.at < CATALOG_TTL_MS) {
    return filingsMemory.data;
  }
  const { base } = await resolveHoldingsBase();
  const url = `${base}/catalog/filings.json`;
  const res = await fetch(url, {
    headers: { Accept: 'application/json', 'Cache-Control': 'no-cache' },
  });
  if (!res.ok) {
    throw new Error(`filings fetch failed: ${res.status}`);
  }
  const data = (await res.json()) as {
    filings?: HoldingsFiling[];
    generated_at?: string;
  };
  const normalized = {
    generated_at: data.generated_at,
    filings: Array.isArray(data.filings) ? data.filings : [],
  };
  filingsMemory = { at: now, data: normalized };
  return normalized;
}
