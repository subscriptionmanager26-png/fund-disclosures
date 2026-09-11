const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";

/**
 * @param {string} url
 * @param {RequestInit & { timeoutMs?: number, insecure?: boolean }} [opts]
 */
export async function httpFetch(url, opts = {}) {
  const envTimeout = Number(process.env.FETCH_TIMEOUT_MS);
  const defaultTimeout =
    Number.isFinite(envTimeout) && envTimeout > 0 ? envTimeout : 45_000;
  const { timeoutMs = defaultTimeout, headers, insecure, signal, ...rest } = opts;
  const prev = process.env.NODE_TLS_REJECT_UNAUTHORIZED;
  if (insecure) process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";
  try {
    const fetchSignal = signal || AbortSignal.timeout(timeoutMs);
    const res = await fetch(url, {
      ...rest,
      headers: {
        "user-agent": UA,
        accept: "*/*",
        ...headers,
      },
      signal: fetchSignal,
    });
    return res;
  } finally {
    if (insecure) {
      if (prev === undefined) delete process.env.NODE_TLS_REJECT_UNAUTHORIZED;
      else process.env.NODE_TLS_REJECT_UNAUTHORIZED = prev;
    }
  }
}

export async function fetchText(url, opts = {}) {
  const envTimeout = Number(process.env.FETCH_TIMEOUT_MS);
  const timeoutMs =
    Number.isFinite(envTimeout) && envTimeout > 0 ? envTimeout : 45_000;
  const controller = new AbortController();
  const timer = setTimeout(
    () => controller.abort(new Error(`fetchText timeout of ${timeoutMs}ms exceeded`)),
    timeoutMs,
  );
  try {
    const res = await httpFetch(url, { ...opts, signal: controller.signal, timeoutMs });
    const text = await res.text();
    return { res, text, url: res.url };
  } finally {
    clearTimeout(timer);
  }
}

export async function fetchBuffer(url, opts = {}) {
  const envTimeout = Number(process.env.FETCH_TIMEOUT_MS);
  const timeoutMs =
    Number.isFinite(envTimeout) && envTimeout > 0 ? envTimeout : 45_000;
  const controller = new AbortController();
  const timer = setTimeout(
    () => controller.abort(new Error(`fetchBuffer timeout of ${timeoutMs}ms exceeded`)),
    timeoutMs,
  );
  try {
    const res = await httpFetch(url, { ...opts, signal: controller.signal, timeoutMs });
    const buf = Buffer.from(await res.arrayBuffer());
    return { res, buf, url: res.url };
  } finally {
    clearTimeout(timer);
  }
}

export function absUrl(href, base) {
  try {
    return new URL(href, base).href;
  } catch {
    return null;
  }
}
