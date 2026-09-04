#!/usr/bin/env node
/**
 * Default output dir for holdings sync scripts.
 * Monorepo: parser lives in pipeline/ inside fund-holdings-data → write to repo root.
 * Standalone: clone/publish via .tmp/fund-holdings-data.
 */
import { existsSync } from "node:fs";
import { join } from "node:path";

export function defaultHoldingsOutDir(parserRoot) {
  const monorepoRoot = join(parserRoot, "..");
  if (
    existsSync(join(monorepoRoot, "catalog")) ||
    existsSync(join(monorepoRoot, "portfolios"))
  ) {
    return monorepoRoot;
  }
  return join(parserRoot, ".tmp/fund-holdings-data");
}
