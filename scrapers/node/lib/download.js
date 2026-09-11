import { createWriteStream, existsSync } from "node:fs";
import { mkdir, writeFile, copyFile } from "node:fs/promises";
import { join } from "node:path";
import { pipeline } from "node:stream/promises";
import { Readable } from "node:stream";
import { httpFetch, fetchBuffer } from "./http.js";

/**
 * @param {object} args
 * @param {string} args.root
 * @param {'monthly'|'fortnightly'} args.type
 * @param {string} args.period Disclosure folder key (YYYY-MM-DD or legacy YYYY-MM)
 * @param {string} args.amcId
 * @param {string} args.url
 * @param {string} [args.filename]
 * @param {string} [args.localPath]
 * @param {boolean} [args.dryRun]
 */
export async function downloadDisclosureFile(args) {
  const {
    root,
    type,
    period,
    amcId,
    url,
    filename,
    localPath,
    dryRun = false,
  } = args;

  let name =
    filename ||
    (() => {
      if (url.startsWith("file:")) {
        return decodeURIComponent(
          url.replace(/^file:\/\//, "").split("/").pop() || "file.bin",
        );
      }
      try {
        const u = new URL(url);
        // NJ etc.: viewfile.php?file=Actual-Name.xlsx
        const qFile = u.searchParams.get("file");
        if (qFile) return decodeURIComponent(qFile);
        return decodeURIComponent(u.pathname.split("/").pop() || "file.bin");
      } catch {
        return "file.bin";
      }
    })();
  // If caller passed a title without extension, keep Spreadsheet ext from URL.
  if (!/\.(xlsx?|xlsb|csv|zip|pdf)$/i.test(name) && /^https?:/i.test(url)) {
    try {
      const u = new URL(url);
      const qFile = u.searchParams.get("file") || "";
      const pathBase = decodeURIComponent(u.pathname.split("/").pop() || "");
      const extMatch = /\.(xlsx?|xlsb|csv|zip|pdf)(\?|$)/i.exec(
        qFile || pathBase,
      );
      if (extMatch) name = `${name}.${extMatch[1].toLowerCase()}`;
    } catch {
      /* ignore */
    }
  }
  const safe = name.replace(/[^\w.\-()+ ]+/g, "_");
  const outDir = join(root, "data/disclosures", type, period, amcId);
  const outPath = join(outDir, safe);

  if (dryRun) {
    return { outPath, skipped: true, bytes: 0, status: "dry_run" };
  }

  await mkdir(outDir, { recursive: true });

  const fromDisk =
    localPath ||
    (url.startsWith("file://") ? url.replace(/^file:\/\//, "") : null);
  if (fromDisk) {
    if (!existsSync(fromDisk)) {
      return { outPath, skipped: true, bytes: 0, status: "missing_local" };
    }
    await copyFile(fromDisk, outPath);
    return { outPath, skipped: false, bytes: undefined, status: "ok_local" };
  }

  let res, buf;
  let lastErr = null;
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      ({ res, buf } = await fetchBuffer(url, {
        headers: { referer: new URL(url).origin + "/" },
      }));
      lastErr = null;
      break;
    } catch (err) {
      lastErr = err;
      if (attempt < 2) {
        await new Promise((r) => setTimeout(r, 1000));
      }
    }
  }

  if (lastErr) {
    return {
      outPath,
      skipped: true,
      bytes: 0,
      status: `error: ${lastErr.message || lastErr}`,
    };
  }

  if (!res || !res.ok) {
    return {
      outPath,
      skipped: true,
      bytes: 0,
      status: `http_${res ? res.status : "unknown"}`,
    };
  }

  await writeFile(outPath, buf);
  return { outPath, skipped: false, bytes: buf.length, status: "ok" };
}

export async function downloadDisclosureStream(args) {
  const { root, type, period, amcId, url, filename, dryRun = false } = args;
  const name =
    filename ||
    decodeURIComponent(new URL(url).pathname.split("/").pop() || "file.bin");
  const safe = name.replace(/[^\w.\-()+ ]+/g, "_");
  const outDir = join(root, "data/disclosures", type, period, amcId);
  const outPath = join(outDir, safe);
  if (dryRun) return { outPath, skipped: true, bytes: 0, status: "dry_run" };

  await mkdir(outDir, { recursive: true });
  const res = await httpFetch(url, {
    headers: { referer: new URL(url).origin + "/" },
  });
  if (!res.ok) {
    return { outPath, skipped: true, bytes: 0, status: `http_${res.status}` };
  }
  const file = createWriteStream(outPath);
  await pipeline(Readable.fromWeb(res.body), file);
  return { outPath, skipped: false, bytes: undefined, status: "ok" };
}

export function ensureDirPath(...parts) {
  return join(...parts);
}
