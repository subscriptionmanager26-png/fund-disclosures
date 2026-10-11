import test from "node:test";
import assert from "node:assert/strict";
import { resolveListTimeoutMs } from "./lib/fetchTimeouts.js";

test("resolveListTimeoutMs prefers FETCH_LIST_TIMEOUT_MS over FETCH_TIMEOUT_MS", () => {
  assert.equal(
    resolveListTimeoutMs({
      FETCH_LIST_TIMEOUT_MS: "600000",
      FETCH_TIMEOUT_MS: "180000",
    }),
    600_000,
  );
});

test("resolveListTimeoutMs falls back to FETCH_TIMEOUT_MS then 15m default", () => {
  assert.equal(resolveListTimeoutMs({ FETCH_TIMEOUT_MS: "240000" }), 240_000);
  assert.equal(resolveListTimeoutMs({}), 900_000);
});
