# 19-1: Workflow Experience Memory

**Parent:** [19-meta-orchestrator](19-meta-orchestrator.md)
**Status:** completed
**Goal:** Build a global-scoped memory layer that indexes past workflows with structured experience summaries — what they did, what worked, what failed, what principles were learned — and provides RAG-based similarity search so the planner can discover and learn from relevant prior work.

**Execution Note:** This plan is the canonical implementation owner for deferred 9D bridge plan [`17-5-workflow-experience-summaries`](17-5-workflow-experience-summaries.md). Do not create parallel implementations.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `GraphStore` | `server/graph_store.py` | `list_graphs()`, `get_graph()`, stores workflow JSON | No metadata about *how* a workflow performed — just the graph structure |
| `RunStore` | `server/run_store.py` | Per-workflow run summaries (success/failure, errors, tokens, cost, elapsed) and event logs | Raw run data; no aggregation or distillation across runs |
| `PrincipleStore` (9D) | `engine/error_memory.py` | Workflow-scoped causal principles from reflection | Principles exist but are not part of a unified experience summary; workflow-scoped only |
| `ErrorMemoryIndex` (9D) | `engine/error_memory.py` | RAG over past errors, per-workflow | Error-specific; no positive experience or workflow-level metadata |
| `MemoryStore` (9A) | `engine/memory_store.py` | Cross-run KV persistence with GLOBAL/WORKFLOW/SESSION scopes | Storage available; no experience-specific schema |
| `EmbeddingProvider` / `VectorStore` | `rag/` | Full embedding + vector search infrastructure | Available; needs a new collection for experience summaries |
| `SKILL_LIBRARY` | `server/skill_library.py` | Skills tagged with metadata (name, description, tags) | Pattern for metadata-rich indexed objects |

## Tasks

- [x] 0. Reuse deferred 17-5 artifacts (no duplicate implementation)
  - [x] 0-1. Keep `17-5` as bridge spec only; implement all concrete work in this file.
  - [x] 0-2. Reuse the existing `engine/experience.py` module target and schema vocabulary from `17-5` where possible.
  - [x] 0-3. Keep API route family consistent with `17-5` (`/api/experiences/*`) to preserve compatibility.

- [x] 1. `WorkflowExperience` model
  - [x] 1-1. Define `WorkflowExperience` in `engine/experience.py`:
    - `workflow_id: str` — links to GraphStore
    - `name: str` — human-readable workflow name
    - `description: str` — what this workflow does (auto-generated from graph structure + node names)
    - `tags: list[str]` — domain tags (e.g., `["paper_writing", "informs", "geopolitics"]`)
    - `node_types_used: list[str]` — which node types appear (e.g., `["llm_operator", "tool_operator", "for_each"]`)
    - `tools_used: list[str]` — tool IDs used by tool nodes
    - `skills_applied: list[str]` — hyperedge skill names
    - `run_count: int` — total runs
    - `success_count: int` — successful runs
    - `last_run_at: float | None`
    - `avg_elapsed_seconds: float | None`
    - `avg_total_cost: float | None`
    - `success_patterns: list[str]` — distilled from successful runs (e.g., "parallel section writing with 3 departments works well")
    - `failure_patterns: list[str]` — distilled from failed runs (e.g., "CRSP merge_asof fails on pre-1990 dates")
    - `principles: list[dict]` — top causal principles from 9D reflection (condition/action/reason/confidence)
    - `created_at: float`
    - `updated_at: float`
  - [x] 1-2. Add `extract_experience_from_graph()`: takes a `Graph` → produces initial `WorkflowExperience` with structural metadata (node_types_used, tools_used, skills_applied, description auto-generated from node names and graph topology).

- [x] 2. Experience consolidation pipeline
  - [x] 2-1. Add `consolidate_experience()` function: given a `WorkflowExperience` + list of `RunRecord` snapshots + list of `CausalPrinciple` → updates the experience with run statistics, success/failure pattern distillation, and top principles.
  - [x] 2-2. For success/failure pattern distillation: use an LLM call (optional, gated by config flag `experience_llm_distillation_enabled`) to summarize recurring patterns across runs. Fallback: simple heuristic extraction (most common error messages, most frequently failing nodes, average metrics).
  - [x] 2-3. Wire consolidation into `RunManager._enrich_and_persist()`: after every N runs (configurable `experience_consolidation_interval`, default 5), trigger experience refresh for the workflow. Also trigger on first successful run and first failed run.

- [x] 3. Experience persistence and indexing
  - [x] 3-1. `ExperienceStore` class in `engine/experience.py`:
    - `save_experience(experience: WorkflowExperience)` — persist to MemoryStore at GLOBAL scope with key `experience:{workflow_id}`
    - `load_experience(workflow_id: str) -> WorkflowExperience | None`
    - `list_experiences() -> list[WorkflowExperience]`
    - `delete_experience(workflow_id: str)`
  - [x] 3-2. `ExperienceIndex` class — wraps `EmbeddingProvider` + `VectorStore` for semantic search over experience summaries:
    - `index_experience(experience: WorkflowExperience)` — embed a composite text (name + description + tags + patterns + principles) and store with metadata
    - `search_similar(query: str, top_k: int = 5) -> list[tuple[WorkflowExperience, float]]` — returns experiences ranked by relevance
    - Collection name: `"workflow_experiences"` (global, not per-workflow)
  - [x] 3-3. Auto-index: whenever `save_experience()` is called, also update the vector index.

- [x] 4. Cross-workflow sharing (deferred from 17-1 task 6 and 17-2 task 5)
  - [x] 4-1. Extend `ErrorMemoryIndex` to support optional global-scope queries: add a `scope` parameter to `query_similar()` — `"workflow"` (default, existing behavior) or `"global"` (searches across all workflows). Global scope iterates all `dan_errors_*` collections and merges by score.
  - [x] 4-2. Extend `PrincipleStore.load_principles()` with a `scope` parameter: `"workflow"` (default) or `"global"` (loads from all workflows). De-duplicates by principle id, keeping highest confidence.
  - [x] 4-3. Add `EngineConfig` field: `cross_workflow_learning: bool = False`. When true, `ErrorContextProvider` also queries global-scope errors and principles. Threaded through `LLMExecutor._get_error_memory_context()`.

- [x] 5. REST API
  - [x] 5-1. `GET /api/experiences` — list all workflow experiences with summary stats.
  - [x] 5-2. `GET /api/experiences/{workflow_id}` — detailed experience for a workflow.
  - [x] 5-3. `POST /api/experiences/search` — semantic search over experiences. Body: `{"query": "paper writing workflow", "top_k": 5}`.
  - [x] 5-4. `POST /api/experiences/{workflow_id}/refresh` — force experience consolidation.
  - [x] 5-5. `DELETE /api/experiences/{workflow_id}` — remove experience entry.

- [x] 6. Tests (21 passing)
  - [x] 6-1. Unit: `WorkflowExperience` model validation, `extract_experience_from_graph()` with various graph topologies.
  - [x] 6-2. Unit: `ExperienceStore` CRUD operations.
  - [x] 6-3. Unit: `consolidate_experience()` with mock run records and principles.
  - [ ] 6-4. Integration: run a workflow multiple times → verify experience is auto-consolidated after N runs. (Deferred: requires full engine execution.)
  - [ ] 6-5. Integration: `ExperienceIndex.search_similar()` returns relevant workflows for related queries. (Deferred: requires embedding provider.)
  - [x] 6-6. Unit+integration: cross-workflow principle sharing — global scope queries return principles from multiple workflows, de-duplication by id keeping highest confidence, global ErrorMemoryIndex searches across all collections. (3 tests in `test_integration_llm.py`.)

## Decisions

- **19-1 is the canonical home of deferred Phase 9D experience-summary work.** `17-5` is retained as historical bridge context only; this file owns execution.
- **Experience is global-scoped.** Unlike 9D's per-workflow error memory, experience summaries are stored at global scope so the planner can discover any relevant workflow in the workspace.
- **Consolidation is incremental, not rebuild-from-scratch.** Each consolidation pass updates the existing experience rather than recomputing from all historical runs. This keeps the cost bounded.
- **LLM distillation is optional.** Pattern extraction can work without an LLM (heuristic fallback), but the LLM-based path produces much richer summaries. Gated by config flag.
- **Experience embeds a composite text, not raw graph JSON.** The vector index stores embeddings of `name + description + tags + top patterns + top principles` — optimized for natural-language similarity, not structural graph matching.

## Notes

- This is the foundational data layer for the meta-orchestrator. Without experience memory, the planner has no "past work" to query — it would generate everything from scratch.
- The experience model is intentionally denormalized (includes principles, patterns, stats). This avoids expensive joins at query time and keeps the planner's context window focused.
- Cross-workflow principle sharing (task 4) also benefits 9D directly — it allows error context from one paper-writing workflow to inform another, even before the meta-orchestrator exists.
- Consolidation now tracks `processed_run_ids`, so auto-refresh and manual refresh remain incremental and idempotent.
