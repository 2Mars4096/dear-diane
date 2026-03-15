# 36-2: Ingredient Provenance & Market Readiness

**Parent:** [36-recipe-distillation-spec](36-recipe-distillation-spec.md)
**Status:** completed
**Goal:** Define the ingredient ledger so every recipe version can explain exactly which papers, notes, and acquisition paths contributed to it, and what changed between versions.

## Tasks
- [x] 1. Define the ingredient record schema
  - [x] 1-1. Core identity: `paper_id`, title, authors, year
  - [x] 1-2. Storage paths: PDF path, note path, configured root identifiers
  - [x] 1-3. Acquisition source: university proxy, direct URL, manual import, etc.
- [x] 2. Define inclusion lifecycle
  - [x] 2-1. Included / excluded / removed / deferred statuses
  - [x] 2-2. Inclusion and exclusion reasons
  - [x] 2-3. Which recipe versions each ingredient influenced
- [x] 3. Define version diffs
  - [x] 3-1. Added ingredients
  - [x] 3-2. Removed ingredients
  - [x] 3-3. Ingredients whose role changed between versions
- [x] 4. Define publishable provenance
  - [x] 4-1. Human-readable ledger section in `recipe.md`
  - [x] 4-2. Machine-readable manifest for marketplace/export use
  - [x] 4-3. Redaction rules if a future source should not be published verbatim
- [x] 5. Connect provenance to the acquisition workflow
  - [x] 5-1. Preserve source metadata from [36-5-paper-acquisition-workflow](36-5-paper-acquisition-workflow.md)
  - [x] 5-2. Preserve benchmark references from [36-1-recipe-training-lifecycle](36-1-recipe-training-lifecycle.md)

## Decisions
- Ingredient provenance is mandatory, not optional metadata.
- Version diffs are first-class so the eventual recipe market can show what changed.
- Provenance should be readable by both humans and automated tooling.

## Notes
- Keep the ledger stable enough that a future recipe marketplace can render it directly.
