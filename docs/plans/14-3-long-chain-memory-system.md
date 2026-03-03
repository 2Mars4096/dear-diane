# 14-3: Long-Chain Memory System

**Parent:** [14-memory-cross-run-state](14-memory-cross-run-state.md)
**Status:** completed
**Goal:** Implement a practical short-term/long-term memory pipeline (encode, consolidate, retrieve) so long workflows can recall distant context reliably without overloading prompts — by activating existing compaction models and reusing RAG infrastructure.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `CompactionRule` | `models/context.py:54` | `CompactionStrategy` enum (`sliding_window`, `keep_last`, `summarize`, `diff_based`, `none`); fields: `strategy`, `window_size`, `max_tokens` | **Partially active** — `WhileLoopExecutor._apply_compaction()` (`control_flow.py:307–324`) implements `keep_last`/`sliding_window`/`diff_based`; `summarize` is declared but **unimplemented** (the main gap this plan fills); `CompositeNode.compaction_rule` field exists but `CompositeExecutor` ignores it |
| `compact_history()` | `server/chat_manager.py` | 4-phase sliding window for chat context: system kept, recent N in full, older truncated (first+last sentence), oldest dropped. Token-budget-aware via `estimate_tokens()` | Chat-only; not available to engine execution |
| `estimate_tokens()` | `server/chat_manager.py` | Uses `tiktoken` (cl100k_base) with `len//4` fallback | Chat-scoped utility; could be extracted to shared module |
| `MODEL_CONTEXT_WINDOWS` | `server/chat_manager.py` | Lookup table for 20 models; `DAN_CHAT_MAX_CONTEXT_RATIO` (0.8), `DAN_CHAT_RECENT_MESSAGES` (10) | Chat config only |
| `EmbeddingProvider` | `rag/__init__.py` | Protocol with `embed(texts, model)` → `EmbeddingResult`; OpenAI and local providers | Reusable for memory embedding |
| `VectorStore` | `rag/stores/__init__.py:50–65` | Protocol: `add`, `query`, `delete_by_ids`, `list_collections`, `create_collection`, `delete_collection`, `count`; `DocumentRecord` with metadata fields | Reusable; metadata filtering supports `run_id`/`session_id` if added |
| `MemoryVectorStore` | `rag/stores/memory.py` | In-memory cosine similarity; O(n) scan | No persistence; fine for small memory sets |
| `FAISSVectorStore` | `rag/stores/faiss_store.py` | FAISS index with `persist_directory`; metadata sidecar | Persistent; reusable for long-term memory |
| `ChromaVectorStore` | `rag/stores/chroma_store.py` | Chroma PersistentClient with native metadata filtering | Persistent; best metadata query support |
| `Indexer` | `rag/indexer.py` | `create_index`, `add_documents`, `delete_index`; batch embedding + chunking | Reusable for memory consolidation pipeline |
| `RAGExecutor` | `executors/rag.py` | Embed query → vector search → chunk retrieval; per-collection store caching | Could be extended for memory retrieval intent |
| Session memory (14-1) | `docs/plans/14-1-session-conversation-memory.md` | `MemoryStore` protocol, `session_id`, cross-run KV persistence | Prerequisite — provides durable entries to consolidate |
| Boundary contracts (14-2) | `docs/plans/14-2-context-scoping-boundaries.md` | `BoundaryContract`, `ContextProjection`, scope-aware APIs | Prerequisite — controls where recalled memory can be injected |

## Tasks

- [ ] 1. Define memory taxonomy and activate policy defaults
  - [ ] 1-1. Define `MemoryEntry` model with typed `entry_type` enum: `raw_event`, `distilled_fact`, `task_state`, `artifact_summary`, `failure_lesson`, `routing_hint`. Each type has a standardized metadata schema.
  - [ ] 1-2. Extend `CompactionRule` activation: `WhileLoopExecutor` already implements `keep_last`/`sliding_window`/`diff_based` — verify compatibility with `MemoryEntry` model and add `summarize` strategy (LLM-based consolidation). Activate `CompositeNode.compaction_rule` in `CompositeExecutor` (currently ignored). Wire into executor checkpoint hooks for both composite and loop executors.
  - [ ] 1-3. Define short-term memory policy defaults: window size (per-agent configurable), decay strategy (recency-weighted), eviction priority (raw_event < distilled_fact < failure_lesson), per-agent token budget cap.
  - [ ] 1-4. Define long-term memory policy defaults: promotion criteria (survive N iterations or cross-run boundary), retention tiers (active/archived/deleted), provenance fields (`source_run_id`, `consolidation_run_id`, `original_entry_ids`).
  - [ ] 1-5. Formalize the agreed memory policy defaults (previously tracked in `todo.md`, now captured here) into a `MemoryPolicyConfig` Pydantic model with env-var overrides (`DAN_MEMORY_*`). Defaults to encode:
    - DAN-native memory first; external context-db adapters later
    - Canonical layers: `L0=TOC/index`, `L1=abstract/overview`, `L2=detailed payload + artifact refs`
    - Lifespan: memory at all scope levels plus cross-run persistence on checkpoint/resume
    - Message semantics: source emits, target receives
    - Rule model: deterministic global rules + node-specific rules
    - Retrieval: contingent + rule-based, balanced determinism/recall
    - Retrieval budget: retrieve `20` → rerank `8` → inject `4`; cap ~`35%` of prompt budget
    - Consolidation triggers: at checkpoint + context pressure (soft `85–90%`, hard `95%`)
    - Sticky-write approval required; timeout escalates to parent
    - Parallel conflict: hybrid aggregator (generic default + per-key reducers)
    - Memory hygiene: forbid chain-of-thought persistence; store only project-useful memory
    - Global schema: `id`, `scope`, `type`, `summary`, `payload_ref`, `tags`, `confidence`, `provenance`, `created_at`, `ttl`, `approval_status`
    - TTL defaults: `profile=365d`, `preferences=180d`, `entities=365d`, `events=90d`, `cases=365d`, `patterns=730d`
    - All defaults are starting points; tune via telemetry, retrieval quality, cost/latency

- [ ] 2. Build short-term memory pipeline
  - [ ] 2-1. Add `ShortTermMemory` buffer abstraction: per-scope (node/agent/graph) typed entry list with configurable capacity. Extends 14-1 `MemoryStore` with in-run buffering semantics.
  - [ ] 2-2. Add write hooks for key execution events: `node_completed` → `raw_event` entry; `tool_call_result` → `raw_event`; `node_failed` → `failure_lesson`; `human_input` → `raw_event`; gate decision → `routing_hint`. Hooks use existing `event_callback` infrastructure.
  - [ ] 2-3. Implement compaction strategies by activating `CompactionRule`:
    - `sliding_window`: keep last N entries per scope, drop oldest.
    - `summarize`: batch older entries, call LLM with summarization prompt, replace batch with single `distilled_fact`. Use `estimate_tokens()` (extracted from chat_manager to shared utility) for budget control.
    - `diff`: store only delta from previous snapshot (for iterative loop state).
  - [ ] 2-4. Add budget controls: token-aware capacity per scope (default from `MemoryPolicyConfig`), priority-based eviction when over budget, per-scope caps independent of global budget.

- [ ] 3. Build consolidation and long-term storage path
  - [ ] 3-1. Define consolidation triggers: configurable via `MemoryPolicyConfig`. Defaults: on while-loop completion (every N iterations), on subgraph completion, on run completion. Trigger fires compaction + promotion.
  - [ ] 3-2. Add consolidation pipeline: collect short-term entries above promotion threshold → apply `summarize` compaction (LLM call) → create `distilled_fact` / `artifact_summary` entries. Cost-aware: skip LLM summarization when entry count is below threshold; use heuristic extraction (first/last sentence, key fields) as cheap fallback.
  - [ ] 3-3. Persist consolidated entries to vector store using existing RAG infrastructure: `Indexer.add_documents()` with memory-specific metadata (`entry_type`, `session_id`, `run_id`, `scope`, `timestamp`). Default store: `FAISSVectorStore` for local; `ChromaVectorStore` for production.
  - [ ] 3-4. Link long-term entries to source runs/sessions/checkpoints: `original_entry_ids`, `consolidation_run_id`, `source_session_id`. Enable audit trail from consolidated memory back to raw events.

- [ ] 4. Build retrieval and injection path
  - [ ] 4-1. Define `MemoryQuery` model with intent enum: `task_recall` (what did we do before?), `error_avoidance` (what went wrong?), `style_consistency` (how did we do it?), `state_resume` (where did we leave off?). Each intent maps to metadata filters + boost weights.
  - [ ] 4-2. Implement hybrid retrieval: semantic similarity (existing `VectorStore.query`) + metadata filters (`entry_type`, `session_id`, `scope`) + recency weighting (exponential decay on `timestamp`). Return ranked `MemoryEntry` list with relevance scores.
  - [ ] 4-3. Add injection policies: `MemoryInjectionPolicy` model controls where recalled memory appears — `prompt_prefix` (injected into LLM system prompt), `planner_input` (fed as structured data to orchestrator), `guardrail_check` (passed to validator nodes). Default: `prompt_prefix` for `error_avoidance` and `style_consistency`; `planner_input` for `task_recall` and `state_resume`.
  - [ ] 4-4. Enforce 14-2 boundary contracts: memory injection respects `reads_global` declarations; memory from a child scope is not injected into a parent unless promoted via sticky signal. Use scope-aware APIs from 14-2.

- [ ] 5. Runtime integration and evaluation
  - [ ] 5-1. Integrate memory retrieval into execution: add `memory_retrieval` hook in `LLMExecutor` (pre-prompt injection) and `OrchestratorExecutor` (planner context). Controlled by `MemoryPolicyConfig.retrieval_enabled` (default: false, opt-in).
  - [ ] 5-2. Extract `estimate_tokens()` from `server/chat_manager.py` to shared `dan.utils.tokens` module. Reuse in both chat compaction and memory budget controls.
  - [ ] 5-3. Add run metrics via engine events: `MEMORY_WRITE` (entry type, scope, size), `MEMORY_RECALL` (query intent, hit count, token cost), `MEMORY_CONSOLIDATION` (entries consolidated, LLM cost). Pipe through existing `event_callback`.
  - [ ] 5-4. Build regression tests: multi-iteration while-loop with memory accumulation, nested composites with scope-isolated memory, cross-run continuity (run1 consolidates → run2 retrieves), failure lesson recall.
  - [ ] 5-5. Add failure-mode tests: memory poisoning (contradictory entries), stale recall (outdated context), runaway growth (budget enforcement), consolidation failure (LLM unavailable → graceful degradation).

- [ ] 6. Documentation and rollout controls
  - [ ] 6-1. Document `MemoryPolicyConfig` defaults and all operator knobs in `docs/architecture.md` §Context Scoping. Include decision rationale for each of the 14 policy defaults.
  - [ ] 6-2. Update `docs/llm-api-guide.md` with memory-related graph/execution parameters: `CompactionRule` usage in builder/markdown, `MemoryQuery` in orchestrator config, retrieval hook configuration.
  - [ ] 6-3. Add feature flags: `DAN_MEMORY_ENABLED` (default: false), `DAN_MEMORY_RETRIEVAL_ENABLED` (default: false), `DAN_MEMORY_CONSOLIDATION_ENABLED` (default: false). Staged rollout: short-term buffering first, then consolidation, then retrieval.
  - [ ] 6-4. Update `docs/changelog.md`, `docs/todo.md`, and plan status as implementation lands.

## Primary Files

- `src/dan/models/context.py` — activate `CompactionRule`; add `MemoryEntry`, `MemoryPolicyConfig`, `MemoryQuery`, `MemoryInjectionPolicy`
- `src/dan/engine/context_runtime.py` — `ShortTermMemory` buffer; compaction execution; consolidation triggers
- `src/dan/engine/scheduler.py` — consolidation hook at checkpoint/completion boundaries
- `src/dan/engine/events.py` — `MEMORY_WRITE`, `MEMORY_RECALL`, `MEMORY_CONSOLIDATION` event types
- `src/dan/executors/llm.py` — memory retrieval pre-prompt injection hook
- `src/dan/executors/control_flow.py` — `OrchestratorExecutor` planner memory injection; `CompositeExecutor` consolidation trigger
- `src/dan/rag/` — reuse `Indexer`, `VectorStore`, `EmbeddingProvider` for memory storage/retrieval
- `src/dan/utils/tokens.py` — extracted `estimate_tokens()` shared utility (from `server/chat_manager.py`)
- `src/dan/server/chat_manager.py` — update to use shared `estimate_tokens`; memory budget integration
- `tests/test_engine/` — memory pipeline, compaction, consolidation, retrieval tests

## Decisions

- **Two-tier memory by design:** short-term for active reasoning (in-run buffer), long-term for compressed durable recall (vector store).
- **Extend `CompactionRule` activation, don't replace it:** `WhileLoopExecutor` already implements `keep_last`/`sliding_window`/`diff_based`. This plan adds the unimplemented `summarize` LLM pipeline and activates compaction in `CompositeExecutor`.
- **Consolidation over accumulation:** promote distilled memory, not full transcript replay. LLM summarization is the default promotion path, with heuristic fallback for cost control.
- **Retrieval is policy-gated:** memory usage must honor 14-2 boundary scopes and injection points. Opt-in via feature flags.
- **Reuse RAG infrastructure:** `VectorStore`, `EmbeddingProvider`, `Indexer` are the storage/retrieval backbone. Memory-specific metadata schema distinguishes memory entries from document chunks.
- **Measure utility, not just storage:** track recall hit rate, stale recall rate, token overhead, and quality delta vs baseline through engine events.

## Notes

- This plan should reuse existing RAG/vector-store infrastructure where practical, but memory semantics remain first-class (not document-RAG-only). `MemoryEntry` metadata schema is what distinguishes memory from documents.
- `compact_history()` in `chat_manager.py` is the proof-of-concept for compaction; the engine-side pipeline generalizes its approach with typed entries and configurable policies. `WhileLoopExecutor._apply_compaction()` is the proof-of-concept for engine-side compaction; this plan extends it with LLM summarization and activates it for composites.
- Reflection/self-evolving features (backlog) can build on this once policy defaults and metrics are stable: failure lessons → reflection node → prompt injection.
- LLM-based summarization in consolidation has cost implications. Default is heuristic extraction; LLM summarization is opt-in via `DAN_MEMORY_CONSOLIDATION_LLM_ENABLED`.
