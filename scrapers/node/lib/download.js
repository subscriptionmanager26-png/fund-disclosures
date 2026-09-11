/**
 * Download disclosure files with integrity checks.
 * Rejects truncated ZIP archives (common ABSL failure mode) and retries once.
 */
import { createWriteStream, existsSync } from "node:fs";
import { mkdir, writeFile, copyFile, unlink } from "node:fs/promises";
import { join } from "node:path";
import { pipeline } from "node:stream/promises";
import { Readable } from "node:stream";
import { httpFetch, fetchBuffer } from "./http.js";

/** True when buffer looks like a complete ZIP (local header + EOCD). */
export function isCompleteZipBuffer(buf) {
  if (!buf || buf.length < 22) return false;
  if (buf[0] !== 0x50 || buf[1] !== 0x4b) return false;
  // End of central directory signature PK\x05\x06 — usually near the end.
  // Allow for a short zip comment (scan last 64KiB+22).
  const scanFrom = Math.max(0, buf.length - 65557);
  for (let i = buf.length - 22; i >= scanFrom; i--) {
    if (
      buf[i] === 0x50 &&
      buf[i + 1] === 0x4b &&
      buf[i + 2] === 0x05 &&
      buf[i + 3] === 0x06
    ) {
      return true;
    }
  }
  return false;
}

function looksLikeZipName(name) {
  return /\.zip$/i.test(name || "");
}

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
    // Staging copies can still be truncated ABSL zips — never promote them.
    if (looksLikeZipName(safe)) {
      const { readFile } = await import("node:fs/promises");
      const buf = await readFile(outPath);
      if (!isCompleteZipBuffer(buf)) {
        try {
          await unlink(outPath);
        } catch {
          /* ignore */
        }
        return { outPath, skipped: true, bytes: 0, status: "truncated_zip" };
      }
    }
    return { outPath, skipped: false, bytes: undefined, status: "ok_local" };
  }

  const maxAttempts = looksLikeZipName(safe) ? 2 : 2;
  let lastStatus = "error";
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    let res, buf;
    try {
      ({ res, buf } = await fetchBuffer(url, {
        headers: { referer: new URL(url).origin + "/" },
      }));
    } catch (err) {
      lastStatus = `error: ${err.message || err}`;
      if (attempt < maxAttempts) {
        await new Promise((r) => setTimeout(r, 1000));
      }
      continue;
    }

    if (!res || !res.ok) {
      lastStatus = `http_${res ? res.status : "unknown"}`;
      continue;
    }

    const contentLength = Number(res.headers?.get?.("content-length") || 0);
    if (contentLength > 0 && buf.length < contentLength) {
      lastStatus = "truncated_content_length";
      continue;
    }

    if (looksLikeZipName(safe) && !isCompleteZipBuffer(buf)) {
      lastStatus = "truncated_zip";
      // Do not leave a corrupt zip on disk for the parser to trip over.
      if (existsSync(outPath)) {
        try {
          await unlink(outPath);
        } catch {
          /* ignore */
        }
      }
      continue;
    }

    await writeFile(outPath, buf);
    return { outPath, skipped: false, bytes: buf.length, status: "ok" };
  }

  return {
    outPath,
    skipped: true,
    bytes: 0,
    status: lastStatus,
  };
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

  // Stream path: validate zip after write when applicable.
  if (looksLikeZipName(safe)) {
    const { readFile } = await import("node:fs/promises");
    const buf = await readFile(outPath);
    if (!isCompleteZipBuffer(buf)) {
      try {
        await unlink(outPath);
      } catch {
        /* ignore */
      }
      return { outPath, skipped: true, bytes: 0, status: "truncated_zip" };
    }
  }
  return { outPath, skipped: false, bytes: undefined, status: "ok" };
}

export function ensureDirPath(...parts) {
  return join(...parts);
}
