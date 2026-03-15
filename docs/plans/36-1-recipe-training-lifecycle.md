# 36-1: Recipe Training Lifecycle

**Parent:** [36-recipe-distillation-spec](36-recipe-distillation-spec.md)
**Status:** completed
**Goal:** Define the resumable furnace-session lifecycle so recipe training can ingest papers in batches, checkpoint safely, resume later, and retain benchmark history.

## Tasks
- [x] 1. Define the furnace session model
  - [x] 1-1. Session identity: `session_id`, `corpus_id`, `recipe_id`, `status`, `created_at`, `updated_at`
  - [x] 1-2. Queue state: pending / ingested / extracted / skipped / deferred paper IDs
  - [x] 1-3. Current phase pointer: normalize / extract / aggregate / infer / project
- [x] 2. Define recipe checkpoint artifacts
  - [x] 2-1. Checkpoint identity: `checkpoint_id`, `batch_index`, `recipe_version`, `created_at`
  - [x] 2-2. Persist batch membership, token/cost summary, and benchmark refs
  - [x] 2-3. Record what changed from the previous checkpoint
- [x] 3. Define resume semantics
  - [x] 3-1. Resume after clean stop without re-ingesting finished papers
  - [x] 3-2. Resume after partial failure with idempotent recovery
  - [x] 3-3. Allow user-approved continue / stop / retest after each batch
- [x] 4. Map lifecycle to the existing workflow engine
  - [x] 4-1. Engine run checkpoints stay run-level; furnace checkpoints stay domain-level
  - [x] 4-2. Define the store interface the workflow uses to load/save session state
  - [x] 4-3. Define how benchmark results are attached back to recipe versions
- [x] 5. Define observability
  - [x] 5-1. Session status summary for the UI `Training` surface
  - [x] 5-2. Batch-by-batch event log and resume history

## Decisions
- Engine checkpoints and furnace checkpoints are related but not the same artifact.
- Batch boundaries are first-class because the user wants to test after every increment.
- Resume must be idempotent; re-running should not silently duplicate ingredients.

## Notes
- This plan should stay aligned with [29-1-unified-memory-kernel](29-1-unified-memory-kernel.md) and [31-15-learning-evolution-optimization](31-15-learning-evolution-optimization.md).
