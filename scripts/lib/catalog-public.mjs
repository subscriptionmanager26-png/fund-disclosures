/**
 * Slim public catalog rows for GET /api/v1/catalog and catalog/amfi-public.json.
 * Full pipeline fields stay in catalog/amfi-lookup.json.
 */
export function publicCatalogRow(row, code) {
  const amfi = String(row?.amfi_code ?? code ?? "").trim();
  return {
    amfi_code: amfi || String(code),
    parent_name: row?.parent_name ?? null,
    available_as_of: Array.isArray(row?.available_as_of)
      ? row.available_as_of
      : [],
    latest_as_of: row?.latest_as_of ?? null,
  };
}

export function publicCatalogFromLookup(catalog) {
  const out = {};
  for (const [code, row] of Object.entries(catalog || {})) {
    if (!row || typeof row !== "object") continue;
    out[code] = publicCatalogRow(row, code);
  }
  return out;
}
