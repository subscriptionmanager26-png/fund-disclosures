import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdirSync,
  writeFileSync,
  rmSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { randomBytes } from "node:crypto";
import {
  attachAvailableAsOf,
  assertNoPhantomAsOfLinks,
  scanExistingAsOfDirs,
} from "./asof-portfolios.mjs";

function tempOutDir() {
  const dir = join(tmpdir(), `asof-test-${randomBytes(6).toString("hex")}`);
  mkdirSync(join(dir, "portfolios/asof/2026-07-31"), { recursive: true });
  mkdirSync(join(dir, "portfolios/asof/2026-08-31"), { recursive: true });
  writeFileSync(
    join(dir, "portfolios/asof/2026-07-31/122639.json"),
    '{"portfolio_id":"122639"}',
  );
  writeFileSync(
    join(dir, "portfolios/asof/2026-08-31/109740.json"),
    '{"portfolio_id":"109740"}',
  );
  return dir;
}

test("attachAvailableAsOf ignores phantom dates injected into asOfMap", () => {
  const outDir = tempOutDir();
  try {
    const catalog = {
      "122640": {
        amfi_code: "122640",
        portfolio_id: "122639",
        parent_amfi: "122639",
        has_holdings: true,
        available_as_of: ["2026-07-31"],
        latest_as_of: "2026-07-31",
      },
    };
    const asOfMap = scanExistingAsOfDirs(outDir, catalog);
    // Bug that caused the incident: stamp every portfolio with partial sync date.
    for (const dates of asOfMap.values()) dates.add("2026-08-31");

    const fixed = attachAvailableAsOf(catalog, asOfMap, { outDir });
    assert.deepEqual(fixed["122640"].available_as_of, ["2026-07-31"]);
    assert.equal(fixed["122640"].latest_as_of, "2026-07-31");

    const phantom = assertNoPhantomAsOfLinks(outDir, fixed);
    assert.equal(phantom.ok, true);
  } finally {
    rmSync(outDir, { recursive: true, force: true });
  }
});

test("assertNoPhantomAsOfLinks catches catalog dates without files", () => {
  const outDir = tempOutDir();
  try {
    const badCatalog = {
      "122640": {
        amfi_code: "122640",
        portfolio_id: "122639",
        has_holdings: true,
        available_as_of: ["2026-08-31", "2026-07-31"],
        latest_as_of: "2026-08-31",
      },
    };
    const phantom = assertNoPhantomAsOfLinks(outDir, badCatalog);
    assert.equal(phantom.ok, false);
    assert.equal(phantom.phantom.length, 1);
    assert.equal(phantom.phantom[0].as_of, "2026-08-31");
  } finally {
    rmSync(outDir, { recursive: true, force: true });
  }
});
