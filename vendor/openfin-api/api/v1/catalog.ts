export const config = {
  runtime: 'edge',
};

import {
  HOLDINGS_CORS,
  jsonResponse,
  loadAmfiPublicCatalog,
} from '../_lib/fundHoldingsCdn.js';
import { trackOpenFinApiRequest } from '../_lib/openfinApiUsage.js';

/**
 * GET /api/v1/catalog
 * Public AMFI lookup — scheme identity and availability (no raw pipeline fields).
 */
export default async function handler(request: Request) {
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: HOLDINGS_CORS });
  }
  if (request.method !== 'GET') {
    return jsonResponse({ error: 'Method not allowed' }, 405);
  }

  try {
    const catalog = await loadAmfiPublicCatalog();
    trackOpenFinApiRequest({ endpoint: 'catalog', method: 'GET', status: 200 });
    return jsonResponse(catalog, 200, {
      'Cache-Control':
        'public, max-age=60, s-maxage=120, stale-while-revalidate=300',
    });
  } catch (err) {
    trackOpenFinApiRequest({ endpoint: 'catalog', method: 'GET', status: 502 });
    return jsonResponse(
      {
        error: 'Could not load catalog',
        detail: err instanceof Error ? err.message : String(err),
      },
      502,
    );
  }
}
