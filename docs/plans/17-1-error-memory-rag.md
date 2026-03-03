# 17-1: Error Memory & Prompt Augmentation (Tier 1)

**Parent:** [17-self-evolving-orchestrator](17-self-evolving-orchestrator.md)
**Status:** in-progress
**Goal:** Capture run errors at completion, index them into a dedicated RAG collection, and retrieve relevant past failures to inject as prompt context before LLM decision points — enabling the system to learn from past mistakes via retrieval-augmented generation.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `RunStore.save_summary()` | `server/run_store.py` | Persists `errors: {node_id: error_message}`, `status`, `node_statuses` at run completion | Errors stored as flat JSON; not indexed for semantic search |
| `RunStore.append_event()` | `server/run_store.py` | Appends every `EngineEvent` as JSONL including `NODE_FAILED` events with error data | Raw event stream; no post-processing or indexing |
| `RunStore.load_events(event_type=...)` | `server/run_store.py` | Filter persisted events by type, node_id | Read-only query; no vector embedding |
| `RunResult.errors` | `engine/scheduler.py:46–54` | `dict[str, str]` — `node_id → error_message` | Flat strings; no structured classification |
| `Indexer.create_index()` / `add_documents()` | `rag/indexer.py` | Chunk → embed → store in VectorStore; configurable chunking | Available but not wired to run errors |
| `VectorStore.query()` | `rag/stores/__init__.py` | Semantic search with top_k + metadata filters | Available; needs `workflow_id` filter for scoping |
| `OrchestratorExecutor` | `executors/control_flow.py:606–1024` | `orchestrator_prompt`/`orchestrator_model` declared on node but **executor never calls LLM** — pure event-processing loop | No LLM call → no direct injection point in orchestrator |
| `LLMExecutor.execute()` | `executors/llm.py` | Has `rendered_prompt`, raw `inputs`, and full `ExecutionContext` right before provider call | **Primary Tier 1 injection point**: prompt-time retrieval can run here with full context |
| `HyperedgeResolver.apply_pre_prompt()` | `engine/hyperedge_runtime.py:162–223` | Injects static hyperedge content into LLM messages | Useful for composition order, but does not currently accept runtime input/context for retrieval |
| `Hyperedge` model | `models/hyperedges.py:30–98` | JSON-serializable static `content` + selectors | Keep static for persistence; avoid callable fields on persisted models in Tier 1 |
| `SKILL_LIBRARY` | `server/skill_library.py` | Prompt-prefix injection by node tags | **Pattern reference**: error context injection follows same mechanism |
| `EngineConfig` | `engine/executor.py` | Feature flags: `memory_enabled`, `boundary_enforcement`, etc. | **Flag pattern** for `error_memory_enabled` |

## Tasks

- [x] 1. Error capture pipeline
  - [x] 1-1. Define `ErrorRecord` model in new `engine/error_memory.py`: `run_id: str`, `workflow_id: str`, `node_id: str`, `node_type: str`, `error_message: str`, `error_category: ErrorCategory` (enum: `llm_failure`, `tool_failure`, `validation_failure`, `timeout`, `schema_mismatch`, `condition_failure`, `unknown`), `input_snapshot: dict | None` (truncated inputs that caused the error), `upstream_node_ids: list[str]` (what fed the failing node), `timestamp: float`, `severity: Literal["warning", "error", "fatal"]`.
  - [x] 1-2. Add `extract_error_records()` function in `error_memory.py`: takes `RunRecord` + event list → returns `list[ErrorRecord]`. Extracts from `RunResult.errors` dict and `NODE_FAILED` events. Classifies `error_category` via heuristics (regex on error messages + node_type mapping: tool_operator → tool_failure, llm_operator → llm_failure, gate → condition_failure, etc.). Truncates `input_snapshot` to configurable max chars (default 2000).
  - [x] 1-3. Wire into `RunManager._enrich_and_persist()`: after enriching the run record, call `extract_error_records(record, events)`. Pass result to error indexing (task 2-4).

- [x] 2. RAG indexing of errors
  - [x] 2-1. Add `ErrorMemoryIndex` class in `engine/error_memory.py`: wraps `Indexer` + `VectorStore` with error-specific APIs. Accepts `EmbeddingProvider` and `VectorStoreConfig` at init. Collection naming: `dan_errors_{workflow_id}`.
  - [x] 2-2. Implement `ErrorMemoryIndex.index_errors(workflow_id, errors: list[ErrorRecord])`: constructs document text from each `ErrorRecord` as structured description (`"Node '{node_id}' ({node_type}) failed with {error_category}: {error_message}. Upstream: {upstream_node_ids}. Input context: {input_snapshot}"`), embeds via `Indexer`, stores with full `ErrorRecord` fields as metadata. Deduplication uses deterministic document IDs (`error:{run_id}:{node_id}`) so backends can upsert/replace without metadata pre-scan.
  - [x] 2-3. Implement `ErrorMemoryIndex.query_similar(workflow_id, context: str, top_k: int = 5) -> list[dict]`: embeds context string → queries per-workflow collection → returns ranked results with text, score, and metadata.
  - [x] 2-4. Implement `ErrorMemoryIndex.clear(workflow_id)` and `ErrorMemoryIndex.stats(workflow_id) -> dict` (count, collection existence).
  - [x] 2-5. Wire into `RunManager`: after error capture (task 1-3), call `error_memory_index.index_errors()` if `error_memory_enabled=True` and errors are non-empty.
  - [x] 2-6. Initialize `ErrorMemoryIndex` in `RunManager.__init__()` (or lazy-init on first use) using engine config's embedding provider and store config. Default backend: `memory` (no external deps); `faiss` or `chroma` for production via `EngineConfig.error_memory_backend`.

- [x] 3. Retrieval and prompt injection
  - [x] 3-1. Implement `ErrorContextProvider` in `engine/error_memory.py`: callable that, given `(node, rendered_prompt, inputs, context)`, queries `ErrorMemoryIndex` with a summary of the node's current task context, formats results into a prompt section, and returns it as a string.
  - [x] 3-2. Format retrieved errors as structured prompt context: `"## Relevant Past Failures\n\nThe following errors occurred in previous runs of this workflow and may be relevant:\n\n"` followed by numbered entries, each with: error category, node type, error message, and a one-line recommendation derived from the error. Limit total to `error_memory_max_tokens` (default 500 tokens).
  - [x] 3-3. Inject error-memory context directly in `LLMExecutor.execute()`: after rendering the prompt and before provider call, build an additional `system` message from `ErrorContextProvider` and prepend it to `messages` when non-empty.
  - [x] 3-4. Scope injection by selectors: apply only when node tags intersect `error_memory_target_tags` **or** node type is in `error_memory_target_types` (default `["llm_operator"]`). Keep orchestrator nodes out of scope because `OrchestratorExecutor` is non-LLM.
  - [x] 3-5. Preserve composition order with hyperedges: error-memory context is inserted first as a system message, then `HyperedgeResolver.apply_pre_prompt()` runs so user-defined hyperedges still compose deterministically.

- [x] 4. Configuration and API
  - [x] 4-1. Add `EngineConfig` fields: `error_memory_enabled: bool = False`, `error_memory_backend: str = "memory"`, `error_memory_max_tokens: int = 500`, `error_memory_top_k: int = 5`, `error_memory_target_tags: list[str] = ["error_aware"]`, `error_memory_target_types: list[str] = ["llm_operator"]`.
  - [x] 4-2. Add REST endpoints in `app.py`: `GET /api/errors/{workflow_id}` (list indexed errors with pagination), `DELETE /api/errors/{workflow_id}` (clear error memory for a workflow), `GET /api/errors/{workflow_id}/search?q={query}` (semantic search for debugging/inspection).
  - [x] 4-3. Server startup: initialize `ErrorMemoryIndex` in `RunManager` if enabled; initialize embedding provider from existing `EngineConfig` embedding settings (reuse RAG config).

- [x] 5. Testing and validation
  - [x] 5-1. Unit tests: `ErrorRecord` model validation, `extract_error_records()` classification heuristics (cover all `ErrorCategory` values), input snapshot truncation.
  - [x] 5-2. Unit tests: `ErrorMemoryIndex` CRUD — `index_errors`, `query_similar`, `clear`, `stats`, deduplication behavior.
  - [x] 5-3. Unit tests: `ErrorContextProvider` prompt formatting, token limit enforcement, empty-results handling.
  - [ ] 5-4. Integration test: run a workflow that fails → verify errors are extracted and indexed → run `query_similar` with related context → verify relevant errors returned with scores.
  - [ ] 5-5. Integration test: LLM-executor prompt injection — run with `error_memory_enabled=True` → verify targeted LLM nodes receive error-memory context as an additional system message before provider call.
  - [ ] 5-6. Regression test: workflow fails due to a specific pattern → error indexed → on next run, LLM receives error context in prompt. (Full behavioral validation that the LLM *changes* its output requires mock LLM; defer to Tier 2 quality tests.)

- [ ] 6. (Deferred to Phase 11) Cross-workflow error memory
  - [ ] 6-1. Keep Tier 1 in Phase 9D strictly workflow-scoped (`dan_errors_{workflow_id}` only).
  - [ ] 6-2. Move global collections / cross-workflow retrieval design to [19-1-workflow-experience-memory](19-1-workflow-experience-memory.md) under Phase 11.
  - [ ] 6-3. When Phase 11 starts, add migration notes for collection naming and retrieval-scope compatibility.

## Primary Files

| File | Changes |
|------|---------|
| `engine/error_memory.py` (new) | `ErrorRecord`, `ErrorCategory`, `extract_error_records()`, `ErrorMemoryIndex`, `ErrorContextProvider` |
| `server/run_manager.py` | Wire error capture + indexing into `_enrich_and_persist()`, `ErrorMemoryIndex` initialization |
| `engine/executor.py` | `EngineConfig` fields for error memory |
| `executors/llm.py` | Prompt-time retrieval injection before provider call and before hyperedge pre_prompt composition |
| `server/app.py` | REST endpoints for error memory |
| `rag/indexer.py` | (no changes — reused as-is) |
| `rag/stores/` | (no changes — reused as-is) |

## Decisions

- **Injection via `LLMExecutor` prompt assembly, not `OrchestratorExecutor` modification.** Prompt-time retrieval needs rendered prompt + node inputs + execution context, all available in `LLMExecutor.execute()`. Hyperedge pre-prompt still composes afterward for deterministic ordering.
- **Per-workflow error collections.** Errors are workflow-scoped to prevent cross-contamination. A node failure in a paper-writing workflow shouldn't influence routing in a data-analysis workflow. Cross-workflow/global reuse is deferred to Phase 11.
- **Post-run indexing only.** Mid-execution indexing adds complexity (concurrent writes, partial error cascades) and the full error picture is only available after the run completes. Trade-off: first run after a new failure type won't benefit until the next run.
- **Error classification is heuristic for Tier 1.** Simple regex + node_type mapping. LLM-based classification is a Tier 2 responsibility (the reflection node does deep analysis). Tier 1 prioritizes speed and zero additional LLM cost.
- **The existing `OrchestratorExecutor` never calls an LLM.** Rather than modifying the orchestrator's execution loop (a separate concern — see backlog "async loop design" and 16-5), Tier 1 targets LLM nodes *within* departments and any node tagged `error_aware`. The tag mechanism lets users control scope.
- **Tier 1 retrieval is injected in `LLMExecutor`, not via callable fields on `Hyperedge`.** `Hyperedge` remains static and JSON-serializable; error-memory context uses an additional system message assembled at execution time with full node/input context.

## Notes

- 13-1 task 2-3 (deep failure context: traceback, input snapshot, partial outputs) is not yet done. Tier 1 works with `RunResult.errors` (flat error string per node) + `NODE_FAILED` event data. When 2-3 lands, error records will automatically get richer input snapshots.
- The `SKILL_LIBRARY` in `skill_library.py` is the direct precedent for this feature — it maps node tags to prompt-prefix text blocks. `ErrorContextProvider` follows the same pattern but generates content dynamically from the RAG index.
- Consider indexing *successful recovery patterns* alongside errors — runs where a node initially failed but succeeded on retry (identifiable from event stream: `NODE_FAILED` followed by `NODE_COMPLETED` for the same node). This is a natural Tier 2 enhancement.
- The `OrchestratorExecutor` team-matching bug (~line 958–972 in `executors/control_flow.py`) doesn't block Tier 1 but should be tracked.
- Memory backend choice: `memory` (MemoryVectorStore) works for development and small error sets. For production workflows with hundreds of runs, `faiss` (FAISSVectorStore) provides persistent indexing with fast retrieval. The `EngineConfig.error_memory_backend` field maps to `VectorStoreConfig.backend`.
