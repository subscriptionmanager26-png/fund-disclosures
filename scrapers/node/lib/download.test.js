import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { isCompleteZipBuffer } from "./download.js";

describe("isCompleteZipBuffer", () => {
  it("rejects truncated zip with only local header", () => {
    const buf = Buffer.concat([Buffer.from([0x50, 0x4b, 0x03, 0x04]), Buffer.alloc(100)]);
    assert.equal(isCompleteZipBuffer(buf), false);
  });

  it("accepts a minimal zip with EOCD", () => {
    // Empty zip: local nothing + EOCD (22 bytes)
    const eocd = Buffer.from([
      0x50, 0x4b, 0x05, 0x06, // signature
      0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    ]);
    assert.equal(isCompleteZipBuffer(eocd), true);
  });

  it("rejects non-zip magic", () => {
    assert.equal(isCompleteZipBuffer(Buffer.from("not a zip")), false);
  });

  it("rejects empty buffer", () => {
    assert.equal(isCompleteZipBuffer(Buffer.alloc(0)), false);
  });
});
