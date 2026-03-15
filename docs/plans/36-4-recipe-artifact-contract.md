# 36-4: Recipe Artifact Contract

**Parent:** [36-recipe-distillation-spec](36-recipe-distillation-spec.md)
**Status:** completed
**Goal:** Define the source-of-truth `recipe.md` contract and the derived `skill.md` projection so recipe outputs are versioned, benchmarkable, and deployable.

## Tasks
- [x] 1. Define `recipe.md` frontmatter
  - [x] 1-1. Identity: `recipe_id`, version, corpus_id, created_at, updated_at
  - [x] 1-2. Provenance: ingredient manifest ref, checkpoint refs, benchmark refs
  - [x] 1-3. Deployment metadata: compatibility, tags, publish status
- [x] 2. Define required sections
  - [x] 2-1. Ingredients, Training History, Checkpoints, Change Log
  - [x] 2-2. Domain Thesis, Core Concepts, Association Vectors
  - [x] 2-3. Methods, Rhetorical Taste, Writing Rules, Anti-Patterns
- [x] 3. Define generation rules
  - [x] 3-1. Compile `recipe.md` from memory + ledger + benchmark artifacts
  - [x] 3-2. Preserve links back to evidence and ingredient provenance
  - [x] 3-3. Define stable ordering so version diffs are readable
- [x] 4. Define `skill.md` projection
  - [x] 4-1. Keep only the highest-value runtime guidance
  - [x] 4-2. Strip long evidence appendices and training logs
  - [x] 4-3. Preserve back-reference to `recipe_id` and source version
- [x] 5. Define versioning rules
  - [x] 5-1. Patch/minor/major bump semantics
  - [x] 5-2. Which artifact changes require a new checkpoint
  - [x] 5-3. How benchmarks attach to recipe versions

## Decisions
- `recipe.md` is the master artifact.
- `skill.md` is derived from `recipe.md`, not edited independently.
- The contract should be stable enough for future marketplace publishing.

## Notes
- This plan should stay aligned with `editor/src/lib/marketplace/recipeModel.ts` so frontend and backend describe the same artifact shape.
