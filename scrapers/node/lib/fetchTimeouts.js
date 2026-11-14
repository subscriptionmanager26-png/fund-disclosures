/** Wall clock for adapter.listFiles (pagination + slow HTML hubs), not per HTTP request. */
export function resolveListTimeoutMs(env = process.env) {
  const list = Number(env.FETCH_LIST_TIMEOUT_MS);
  if (Number.isFinite(list) && list > 0) return Math.max(60_000, list);
  const legacy = Number(env.FETCH_TIMEOUT_MS);
  if (Number.isFinite(legacy) && legacy > 0) return Math.max(60_000, legacy);
  return 900_000;
}
