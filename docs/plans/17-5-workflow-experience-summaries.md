# 17-5: Workflow Experience Summaries

**Parent:** [17-self-evolving-orchestrator](17-self-evolving-orchestrator.md)
**Status:** completed (bridge spec only; execution deferred to Phase 11 / 19-1)
**Goal:** Each workflow accumulates a structured "experience summary" — a local record of what it does, what worked, what failed, and what principles were learned — and exposes an export-ready payload for future Phase 11 meta-orchestrator global reuse.

**Execution Note:** This file remains as a bridge specification created during Phase 9D extension. Execution tracking and implementation details now live in [`19-1-workflow-experience-memory`](19-1-workflow-experience-memory.md).
**Execution Guard:** Do **not** implement from this file. Treat all task checklists below as archival handoff context only. Use `19-1` as the canonical implementation source.

## Motivation

When a user asks "help me write a paper on X in AER format," the system should be able to recall: "I've written papers before — let me check what I learned." This requires:

1. **Knowing what workflows exist** and what they do (not just their graph topology, but their purpose and track record).
2. **Producing reusable summary artifacts** so Phase 11 can later perform global semantic discovery ("find workflows related to paper writing").
3. **Learning from past experience** — not just error avoidance (17-1/17-2/17-3), but success patterns, effective tool choices, and workflow structure decisions.

Currently, DAN stores run-level data (run summaries, events, errors, principles) but has no **workflow-level** summary that aggregates experience across runs. A meta-orchestrator would need to scan every run of every workflow to understand what happened — expensive and unstructured.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `RunStore` | `server/run_store.py` | Per-run summaries and events | No cross-run aggregation per workflow |
| `RunRecord.snapshot()` | `server/run_manager.py` | `errors`, `node_statuses`, `total_tokens`, `total_cost`, `elapsed_seconds` | Per-run only; no workflow-level rollup |
| `PrincipleStore` (17-2) | `engine/error_memory.py` | Persisted causal principles per workflow | Available but not aggregated into a summary |
| `RuleLifecycleManager` (17-3) | `engine/rule_generator.py` | Generated rules with effectiveness scores | Available but not summarized |
| `ErrorMemoryIndex` (17-1) | `engine/error_memory.py` | Vector index of errors per workflow | Available for error pattern extraction |
| `MemoryStore` | `engine/memory_store.py` | Cross-run KV persistence (GLOBAL/WORKFLOW/SESSION) | **Storage target** for experience summaries |
| `ConsolidationPipeline` (14-3) | `engine/memory_pipeline.py` | Short-term → long-term memory transfer | Available for periodic experience consolidation |
| `Graph` model | `models/graph.py` | Nodes, edges, entry/exit points, hyperedges | Structural description available; no semantic description |
| `EmbeddingProvider` + `VectorStore` | `rag/` | Full embedding + vector search infrastructure | Available for experience index |

## Tasks

- [ ] 1. `WorkflowExperience` model
  - [ ] 1-1. Define `WorkflowExperience` model in new `engine/experience.py`:
    - `workflow_id: str` — unique identifier.
    - `name: str` — human-readable workflow name (from `Graph.metadata` or inferred from node names).
    - `description: str` — auto-generated semantic description of what this workflow does (e.g., "Writes academic papers with literature review, drafting, and LaTeX compilation").
    - `node_types: list[str]` — distinct node types used (e.g., `["llm_operator", "tool_operator", "for_each", "gate"]`).
    - `tool_names: list[str]` — tools used (extracted from `ToolOperator` nodes).
    - `skill_names: list[str]` — hyperedge skills attached (from `graph.hyperedges`).
    - `total_runs: int` — count of completed runs.
    - `success_rate: float` — `successful_runs / total_runs`.
    - `common_errors: list[dict]` — top-K most frequent error patterns (category + message template + count).
    - `learned_principles: list[dict]` — top-K highest-confidence principles (condition + action + confidence).
    - `active_rules: int` — count of active generated rules.
    - `avg_cost: float | None` — average run cost.
    - `avg_duration: float | None` — average run duration.
    - `first_run_at: float | None` — timestamp of first run.
    - `last_run_at: float | None` — timestamp of most recent run.
    - `updated_at: float` — when this summary was last refreshed.
    - `tags: list[str]` — user-assigned or auto-generated tags for discovery (e.g., `["paper_writing", "latex", "literature_review"]`).
  - [ ] 1-2. Add `auto_describe_workflow(graph: Graph) -> str` function: walks the graph topology and generates a natural-language description. Heuristic-based (not LLM): lists node types, tools, entry→exit flow, loop structures, team compositions. Example output: `"3-stage pipeline: literature review (ForEach + RAG), draft writing (LLM with INFORMS skill), LaTeX compilation (tool_operator)."` Falls back to LLM-based description when heuristic output is too generic (configurable).

- [ ] 2. Experience aggregation
  - [ ] 2-1. Implement `ExperienceAggregator` class in `engine/experience.py`:
    - `refresh(workflow_id) -> WorkflowExperience` — scan `RunStore` summaries + `PrincipleStore` + `RuleLifecycleManager` + `ErrorMemoryIndex` for the given workflow, compute all fields, persist to `MemoryStore`.
    - `get(workflow_id) -> WorkflowExperience | None` — load persisted experience summary.
    - `list_all() -> list[WorkflowExperience]` — load all workflow experience summaries.
  - [ ] 2-2. Wire into `RunManager._enrich_and_persist()`: after all 17-1/17-2/17-3 processing, trigger an incremental experience refresh. Incremental = update counters and timestamps without re-scanning all historical runs. Full refresh on first run or via explicit API call.
  - [ ] 2-3. Common error extraction: group errors by `(node_id, error_category)`, count occurrences, keep top-10 by frequency. Template the error message (replace run-specific details with placeholders).
  - [ ] 2-4. Principle ranking: load principles from `PrincipleStore`, sort by confidence descending, keep top-10.

- [ ] 3. Phase-11 handoff payload (no global reuse in 9D)
  - [ ] 3-1. Implement `ExperienceExportPayload` builder in `engine/experience.py`: normalize `WorkflowExperience` into a stable JSON payload intended for Phase 11 ingestion (`workflow_id`, description, tags, error patterns, principles, active rules, success metrics).
  - [ ] 3-2. Add `ExperienceAggregator.export(workflow_id) -> dict` that returns the normalized payload.
  - [ ] 3-3. Persist latest export payload under workflow scope (`workflow_experience:latest_export`) for local debugging and future migration.
  - [ ] 3-4. Document compatibility contract with [19-1-workflow-experience-memory](19-1-workflow-experience-memory.md) so Phase 11 can ingest without schema churn.

- [ ] 4. API endpoints
  - [ ] 4-1. `GET /api/experiences` — list all workflow experience summaries (paginated, sortable by `last_run_at`, `success_rate`, `total_runs`).
  - [ ] 4-2. `GET /api/experiences/{workflow_id}` — get full experience summary for a specific workflow.
  - [ ] 4-3. `POST /api/experiences/{workflow_id}/refresh` — force a full experience refresh (re-scans all historical runs).
  - [ ] 4-4. `GET /api/experiences/{workflow_id}/export` — retrieve normalized export payload for Phase 11 meta-orchestrator ingestion.
  - [ ] 4-5. `PATCH /api/experiences/{workflow_id}/tags` — add/remove user tags for a workflow's experience (complements auto-generated tags).

- [ ] 5. Auto-tagging and description
  - [ ] 5-1. Implement `auto_tag_workflow(graph: Graph, experience: WorkflowExperience) -> list[str]`: extract tags from node types, tool names, skill names, and frequent error categories. Example: a workflow using `save_paper` tool + `informs_latex_style` skill → tags `["paper_writing", "latex", "informs"]`.
  - [ ] 5-2. Optionally enrich with LLM-based tagging: after N runs (configurable threshold), call an LLM with the experience summary to generate richer semantic tags. Gate behind `EngineConfig.experience_llm_tagging: bool = False`.

- [ ] 6. Testing
  - [ ] 6-1. Unit tests: `WorkflowExperience` model validation, `auto_describe_workflow()` on various graph topologies, `auto_tag_workflow()` extraction heuristics.
  - [ ] 6-2. Unit tests: `ExperienceAggregator` — refresh from mock `RunStore`/`PrincipleStore`/`RuleLifecycleManager`, incremental vs full refresh.
  - [ ] 6-3. Unit tests: export payload shape/versioning and backward-compatible field guarantees.
  - [ ] 6-4. Integration test: run a workflow 3 times (2 fail, 1 succeed) → verify experience summary has correct `total_runs=3`, `success_rate≈0.33`, `common_errors` populated, `learned_principles` from reflection.
  - [ ] 6-5. Export test: create summaries for 3 workflows → verify all exports conform to schema and are consumable by a mock Phase 11 ingestor.

## Primary Files

| File | Changes |
|------|---------|
| `engine/experience.py` (new) | `WorkflowExperience`, `ExperienceAggregator`, `ExperienceExportPayload`, `auto_describe_workflow`, `auto_tag_workflow` |
| `server/run_manager.py` | Wire experience refresh into `_enrich_and_persist()` |
| `engine/executor.py` | `EngineConfig` fields: `experience_refresh_enabled`, `experience_llm_tagging` |
| `server/app.py` | REST endpoints for experience CRUD + export |

## Decisions

- **Experience is a derived artifact, not a primary data source.** It's computed from existing run data, principles, and rules — never the source of truth. If a run is deleted, the experience can be recomputed.
- **Heuristic description first, LLM enrichment optional.** `auto_describe_workflow` uses graph topology analysis. LLM-based description is opt-in and cost-bounded (one call per workflow, not per run).
- **Experience is workflow-scoped in Phase 9D.** Global discovery/reuse is deferred to Phase 11 (`19-1`); 9D produces export-ready payloads only.
- **Incremental updates by default.** After each run, only counters and timestamps are updated. Full refresh (re-scanning all runs) is manual or triggered by the consolidation pipeline.
- **Tags are both auto-generated and user-curated.** Auto-tags come from graph analysis; users can add/remove tags via the API. Both types are indexed.

## Notes

- This is the **bridge between 9D and the future meta-orchestrator.** 9D standardizes workflow-local summaries and export payloads; Phase 11 adds global indexing and cross-workflow retrieval.
- The `WorkflowExperience` model is deliberately rich — it captures both structural information (node types, tools, skills) and behavioral information (success rate, errors, principles). This dual view is what enables the meta-orchestrator to decide: "this workflow's structure is a good starting point, and its learned principles should be carried forward."
- The experience description + tags become the "workflow resume" — the first thing the meta-orchestrator reads when deciding whether to reuse, adapt, or build from scratch.
- Consider integration with the editor: a "Workflow Intelligence" panel showing the experience summary, success rate trend, top principles, active rules, and similar workflows. This makes the learning loop visible to human users, not just the meta-orchestrator.
