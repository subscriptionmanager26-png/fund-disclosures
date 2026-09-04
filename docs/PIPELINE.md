# Fund disclosures month-end pipeline

> **Repos:** Parser lives on both `kushagra-agarwal-a/fund-disclosures` and `subscriptionmanager26-png/fund-disclosures`. Holdings publish **only** to `kushagra-agarwal-a/fund-holdings-data`. See [ARCHITECTURE.md](./ARCHITECTURE.md).

Canonical layout:

```text
registry/     AMC registry, parser families, shortcode map, fixtures
scrapers/     Node fetch CLI + Python AMC fetchers
parsers/      Excel → holdings
amfi/         NAVAll / as-of / populate-scheme parents
matching/     disclosure↔AMFI match, incremental new parents, Excel export
qc/           allocation / holdings compare
exports/      mapping snapshots (+ exports/baseline freeze)
data/         disclosures, parsed, amfi dumps (mostly gitignored)
```

## Month-end loop

From repo root (`fund-disclosures/`):

```bash
# 1) Fetch AMC packs for a period
npm run fetch -- --type=monthly --period=2026-07
npm run fetch -- --type=fortnightly --period=2026-07

# 2) Parse holdings (family parsers)
# Resume is ON by default: re-running after a kill only parses missing/stale files.
# Use --force to re-parse everything.
npm run parse:amc -- --type=monthly --period=2026-07
npm run parse:check -- --type=monthly --period=2026-07
# or fixtures smoke:
npm run parse:amc:fixtures

# 2b) Enrich + sync gate on completeness (enrich refuses partial AMCs by default)
npm run holdings:enrich
# Override only if intentional: npm run holdings:enrich -- --allow-incomplete

# 2c) Guard pinned shortcode/alias fixes (HDINCF, SILVRFOF, INDEX, …)
npm run holdings:assert-locks

# 3) Refresh AMFI universe
npm run amfi:catalog          # NAVAll → data/amfi/
npm run amfi:asof -- --asof=31-Jul-2026
npm run amfi:parents          # populate-scheme active parents

# 4) Match disclosures ↔ AMFI (reuse shortcodes)
npm run amfi:match:reuse
npm run holdings:assert-locks   # refuse if pinned HDINCF/SILVRFOF/INDEX/… drifted
npm run amfi:new-parents      # diff new parents vs map; proposals JSON
npm run amfi:coverage

# 5) Export mapping workbook
npm run export:mapping

# 6) Rebuild browser catalog + persist registry to origin (never leave local)
npm run holdings:catalog
npm run registry:persist
```

Pinned shortcode/alias locks live in `registry/holdings_mapping_locks.json`.
`npm run holdings:assert-locks` and `npm run registry:persist` both enforce them before push.

## Mapping grain

- **Parent** = AMFI scheme / populate-scheme id (e.g. `1450`)
- **Plan** = NAVAll Scheme Code (Direct/Regular × Growth/IDCW)
- **Disclosure** = AMC pack fund/sheet (often keyed by shortcode)

Maps:

- `data/sources/disclosure_to_amfi_global_mapping.json`
- `data/sources/amfi_navall_to_disclosure_global_mapping.json`
- `registry/disclosure_shortcode_map.json` (canonical; `data/sources/` is a symlink)

Baseline freeze: `exports/baseline/`.

## Secrets

- `EDELWEISS_API_SECRET` — required for Edelweiss statutory API fetch
- `GH_TOKEN` / `GITHUB_TOKEN` — required for `holdings:sync*` `--push`
- Holdings API object storage: `B2_KEY_ID`, `B2_APPLICATION_KEY` (see `holdings-browser/.env.example`)

## Data safety

Sync scripts **block pushes** that shrink on-disk `portfolios/asof/*` trees or remove
`available_as_of` links from the published catalog (`scripts/lib/holdings-guard.mjs`).
Use `--allow-regression` only for intentional cleanups.

Never copy a fresh `holdings-browser` catalog over the data repo without running
`holdings:refresh-filings` afterward — historical as-of links come from on-disk trees.

## Compat

Old entrypoints under `src/` and `scripts/` are thin shims to the new layout.
