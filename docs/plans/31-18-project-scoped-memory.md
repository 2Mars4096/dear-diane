# 31-18: Project-Scoped Memory

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Wire the existing `MemoryScope.PROJECT` and `MemoryItem.metadata` so that memories stored during a project conversation are automatically tagged with that project's ID, and retrieval filters them by the active project — like workspace-level rules that activate by context.

## Problem

`MemoryScope.PROJECT` exists as an enum value, and `MemoryItem.metadata` is a free dict that can hold `project_id`. But neither the store nor the retrieve path uses them:

- **Store side:** `store_preference()`, `store_fact()`, `store_principle()`, and all extraction paths hardcode `scope=USER` or `scope=GLOBAL`. Nothing tags items with the active `project_id`.
- **Retrieve side:** `retrieve()` iterates all non-archived items of each type. It never filters by project. A `PROJECT`-scoped fact for project A competes equally against one for project B.

This means project-specific information (file paths, style rules, domain conventions) bleeds across projects. Users who tell DAN "this project uses INFORMS style" and separately "this project uses arXiv style" get both surfaced everywhere.

## Design

Context-activated, not manually tagged. The `ProjectContextResolver` already identifies which project a message belongs to. That resolved `project_id` flows into both store and retrieve — memories activate automatically when their project is active, like Cursor workspace rules.

### Retrieve filter

`retrieve()` gains an optional `project_id: str | None` parameter:
- `PROJECT`-scoped items whose `metadata["project_id"]` does not match → **skipped**
- `USER`/`GLOBAL`/`SESSION`/`WORKFLOW`-scoped items → **unfiltered** (always visible, like always-applied rules)

Since memory retrieval and context resolution run in parallel, the initial retrieval runs without project context. After context is resolved, a lightweight project-scoped supplement query adds any project-specific memories that the initial query missed.

### Store tagging

All memory store paths in the concierge gain access to the active `project_id`:
- `_store_memory_candidates()` and its sub-methods tag items with `metadata={"project_id": ...}` and `scope=PROJECT`
- `_try_extract_preferences()` and `_try_memory_extraction()` tag extracted items likewise
- Correction-driven stores (`store_preference`, `store_principle`) tag with the active project

Episode memories stay `SESSION`-scoped (they're ephemeral). Failure patterns stay `USER`-scoped (they're cross-project learning). Only facts, preferences, and principles get project-scoped when stored during a project conversation.

## Tasks

- [x] 1. **Add `project_id` filter to `MemoryKernel.retrieve()`**
  - [x] 1-1. Add `project_id: str | None = None` parameter to `retrieve()` and `retrieve_by_task()`
  - [x] 1-2. In the candidate loop, skip `PROJECT`-scoped items with mismatching `metadata["project_id"]`
  - [x] 1-3. Boost `PROJECT`-scoped items with matching `project_id` (add 0.3 score bonus)

- [x] 2. **Pass `project_id` from runtime into retrieval**
  - [x] 2-1. Add `project_id` param to `_retrieve_memory_context()`
  - [x] 2-2. After context resolution in `_process_inner`, supplement memory context with project-scoped query
  - [x] 2-3. Inject project-scoped memories into goal context alongside general memories

- [x] 3. **Tag stored memories with project context**
  - [x] 3-1. Add `project_id` param to `_store_memory_candidates()` and all sub-methods
  - [x] 3-2. Facts and preferences from `_try_extract_preferences()` get `scope=PROJECT` + `metadata.project_id`
  - [x] 3-3. Facts and preferences from `_try_memory_extraction()` get `scope=PROJECT` + `metadata.project_id`
  - [x] 3-4. Correction-driven `store_preference()`/`store_principle()` get `scope=PROJECT` + `metadata.project_id`
  - [x] 3-5. Episode memories stay `SESSION`-scoped; failure patterns stay `USER`-scoped

- [x] 4. **Convenience helpers on `MemoryKernel`**
  - [x] 4-1. `store_fact()`, `store_preference()`, `store_principle()` accept optional `project_id` — sets `scope=PROJECT` and `metadata["project_id"]` when provided

- [x] 5. **Tests**
  - [x] 5-1. Unit: `retrieve()` with `project_id` filters out other projects' `PROJECT`-scoped items
  - [x] 5-2. Unit: `retrieve()` without `project_id` still returns all items (backward compat)
  - [x] 5-3. Unit: `USER`/`GLOBAL` items always returned regardless of `project_id`
  - [x] 5-4. Unit: `store_fact(project_id=...)` sets correct scope and metadata
  - [x] 5-5. Integration: end-to-end project-scoped memory (INFORMS vs arXiv isolation, file path retrieval)

## Decisions

- **No new fields on `Project` model** — `MemoryItem.metadata["project_id"]` is sufficient
- **No manual config commands** — project scope activates automatically by context
- **Supplement pattern for parallel prep** — initial memory retrieval runs without project context; a lightweight second query adds project-specific items after context is resolved

## Notes

- Total change is ~100 lines across `memory_kernel.py` and `runtime.py`, plus tests
- Backward compatible: all new parameters are optional with `None` defaults
