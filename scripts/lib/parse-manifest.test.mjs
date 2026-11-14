import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  amcsToParse,
  loadManifest,
  parserFingerprint,
  sliceDigests,
  updateManifest,
  writeManifest,
} from "./parse-manifest.mjs";

function withTmp(fn) {
  const dir = mkdtempSync(join(tmpdir(), "manifest-"));
  try {
    return fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function put(root, rel, content) {
  const p = join(root, rel);
  mkdirSync(join(p, ".."), { recursive: true });
  writeFileSync(p, content);
}

test("slice digests change only for the AMC whose file changed", () =>
  withTmp((root) => {
    put(root, "data/disclosures/monthly/2026-10-31/sbi/a.xlsx", "sbi-v1");
    put(root, "data/disclosures/monthly/2026-10-31/hdfc/b.xlsx", "hdfc-v1");
    const d1 = sliceDigests(root, "monthly", "2026-10-31");
    put(root, "data/disclosures/monthly/2026-10-31/hdfc/b.xlsx", "hdfc-v2");
    const d2 = sliceDigests(root, "monthly", "2026-10-31");
    assert.equal(d1.sbi, d2.sbi);
    assert.notEqual(d1.hdfc, d2.hdfc);
    assert.deepEqual(sliceDigests(root, "monthly", "2026-09-30"), {});
  }));

test("amcsToParse: no manifest → all; unchanged → none; new/changed → those only; code change → all", () => {
  const digests = { sbi: "s1", hdfc: "h1", choice: "c1" };
  const empty = { version: 1, fingerprint: null, slices: {} };
  assert.deepEqual(amcsToParse(empty, "fp1", "monthly/2026-10-31", digests), {
    amcs: ["choice", "hdfc", "sbi"],
    reason: "no_manifest",
  });
  const m = updateManifest(empty, "fp1", [{ key: "monthly/2026-10-31", digests, amcs: ["sbi", "hdfc"] }], "2026-11-14");
  assert.deepEqual(amcsToParse(m, "fp1", "monthly/2026-10-31", digests).amcs, ["choice"]); // never recorded
  assert.deepEqual(amcsToParse(m, "fp1", "monthly/2026-10-31", { sbi: "s1", hdfc: "h1" }), {
    amcs: [],
    reason: "unchanged",
  });
  assert.deepEqual(amcsToParse(m, "fp1", "monthly/2026-10-31", { sbi: "s2", hdfc: "h1" }).amcs, ["sbi"]);
  assert.equal(amcsToParse(m, "fp2", "monthly/2026-10-31", { sbi: "s1" }).reason, "code_changed");
});

test("updateManifest keeps prior AMCs, drops old months, resets on code change", () => {
  let m = { version: 1, fingerprint: "fp1", slices: { "monthly/2026-05-31": { x: "1" }, "monthly/2026-10-31": { a: "1" } } };
  m = updateManifest(m, "fp1", [{ key: "monthly/2026-10-31", digests: { b: "2" }, amcs: ["b"] }], "2026-11-14");
  assert.deepEqual(m.slices["monthly/2026-10-31"], { a: "1", b: "2" });
  assert.equal(m.slices["monthly/2026-05-31"], undefined); // older than 4 months
  const reset = updateManifest(m, "fp2", [], "2026-11-14");
  assert.deepEqual(reset.slices, {});
});

test("manifest round-trips through the holdings repo", () =>
  withTmp((dir) => {
    assert.equal(loadManifest(dir).fingerprint, null);
    writeManifest(dir, { version: 1, fingerprint: "fp", slices: { "monthly/2026-10-31": { a: "1" } } });
    assert.equal(loadManifest(dir).slices["monthly/2026-10-31"].a, "1");
  }));

test("parser fingerprint is stable and content-sensitive outside git", () =>
  withTmp((root) => {
    put(root, "parsers/p.py", "v1");
    const a = parserFingerprint(root, ["parsers"]);
    assert.equal(parserFingerprint(root, ["parsers"]), a);
    put(root, "parsers/p.py", "v2");
    assert.notEqual(parserFingerprint(root, ["parsers"]), a);
  }));
