# 17: Phase 9D — Self-Evolving Orchestrator

**Status:** completed
**Goal:** Enable orchestrators to learn from past failures by persisting error memories, reflecting on them to extract causal principles, and injecting retrieved lessons into future decisions — ultimately adapting behavior via self-generated hyperedge rules and runtime parameter mutations.

## Motivation

DAN orchestrators currently have no memory of past mistakes. Each run starts from zero knowledge — if a department fails due to a specific input pattern, the orchestrator will route the same way again. This is a fundamental gap between DAN and adaptive systems.

Three observations motivate this plan:

1. **Run artifacts already capture errors.** 13-1 persists `RunResult.errors` (node_id → error message) and `NODE_FAILED` events in append-only JSONL. But this data is only available for human inspection — no automated retrieval or injection into future runs.

2. **RAG infrastructure exists.** Phase 6 built a full embedding pipeline (`EmbeddingProvider` → `Indexer` → `VectorStore`) and `RAGExecutor`. Phase 9A added persistent memory (`MemoryStore`, `ShortTermMemory`, `ConsolidationPipeline`). The pieces for "index past errors → retrieve relevant ones → inject into context" are all present but not wired together.

3. **Hyperedge runtime is operational.** `HyperedgeResolver` with `apply_pre_prompt()`, `apply_post_output()`, `apply_tool_call()`, and `apply_validation()` hooks is integrated into scheduler/executor. `AddHyperedge` mutations can programmatically add rules to graphs. Self-generated guardrails can be injected as first-class graph constructs, not ad-hoc prompt patches.

Three tiers of capability, each building on the last:

- **Tier 1 (Prompt Augmentation):** Capture errors → embed → store in vector index → retrieve relevant failures at decision points → inject as prompt context. *Works with existing infrastructure NOW.*
- **Tier 2 (Reflection Node):** Dedicated post-run analysis that distills raw errors into structured causal principles ("when X, avoid Y because Z"). *Benefits from deeper run history (13-2).*
- **Tier 3 (Self-Generating Rules):** Reflection outputs become executable adaptations: hyperedge rules for prompt/tool guidance plus runtime parameter mutations for node-level tuning. The orchestrator adapts its behavior graph without mutating persisted graph JSON. *Requires stable hyperedge runtime (already partially landed).*

## Existing Infrastructure (baseline)

| Component | Location | What exists | Relevance to 9D |
|---|---|---|---|
| `OrchestratorNode` | `models/control_flow.py:308–365` | Teams, `orchestrator_prompt` (unused), `orchestrator_model` (unused), completion conditions, event-driven loop | **Tier 1 context**: the orchestrator is the natural recipient of error-augmented prompts, but the executor currently never calls the LLM |
| `OrchestratorExecutor` | `executors/control_flow.py:606–1024` | Spawns teams concurrently, processes event queue, captures `orchestrator_log` including `NODE_FAILED` events | **Gap**: no LLM call — `orchestrator_prompt`/`orchestrator_model` are declared but never used; executor is a pure event-processing loop |
| `RunStore` | `server/run_store.py` | Filesystem persistence: `{run_id}.json` (summary with `errors` dict), `{run_id}.events.jsonl` (full event stream) | **Tier 1 data source**: `RunResult.errors`, `NODE_FAILED` events, per-node usage |
| `RunRecord` | `server/run_manager.py:33–98` | `errors`, `node_statuses`, `total_tokens`, `total_cost`, `node_usage`, `elapsed_seconds` | **Tier 1 data source**: structured error + cost metadata per run |
| `EmbeddingProvider` | `rag/__init__.py` | Protocol + OpenAI/Local providers, `EmbeddingRegistry` | **Tier 1 embedding**: embed error descriptions for vector search |
| `Indexer` | `rag/indexer.py` | `create_index()`, `add_documents()` with chunking + batch embedding | **Tier 1 indexing**: index error records as documents |
| `VectorStore` | `rag/stores/__init__.py` | Protocol + memory/faiss/chroma backends, `query()` with top_k + metadata filters | **Tier 1 retrieval**: semantic search over past errors |
| `RAGExecutor` | `executors/rag.py` | Embed query → vector search → chunk retrieval | **Tier 1 pattern**: reusable retrieval logic |
| `MemoryStore` | `engine/memory_store.py` | Cross-run KV persistence (GLOBAL/WORKFLOW/SESSION scopes), atomic writes | **Tier 2 storage**: persist distilled principles across runs |
| `ShortTermMemory` | `engine/memory_pipeline.py` | Buffer + compaction strategies (sliding_window, keep_last, diff_based, summarize) | **Tier 2 pipeline**: compact raw errors before reflection |
| `ConsolidationPipeline` | `engine/memory_pipeline.py` | Short-term → long-term transfer with configurable thresholds | **Tier 2 pipeline**: promote principles to long-term memory |
| `Hyperedge` | `models/hyperedges.py:30–96` | Full model: types (skill/guardrail/style/override), hooks (pre_prompt/tool_call/post_output/validation), attachment scopes, precedence | **Tier 3 target**: self-generated rules become hyperedges |
| `HyperedgeResolver` | `engine/hyperedge_runtime.py:29–374` | `resolve()`, `apply_pre_prompt()`, `apply_post_output()`, `apply_tool_call()`, `apply_validation()` — integrated into scheduler + executor | **Tier 3 runtime**: resolver already handles dynamic hyperedge injection |
| `AddHyperedge` mutation | `server/graph_mutator.py:117–132` | Programmatic hyperedge CRUD via mutation pipeline | **Tier 3 API**: create rules from reflection output |
| `SKILL_LIBRARY` | `server/skill_library.py` | Prompt-injection skills keyed by node tags, `inject_as: "system"` | **Tier 1 pattern**: skill injection mechanism — error context follows the same pattern |
| `BoundaryContract` (14-2) | `models/context.py` | `reads_global`/`writes_global` whitelists, signal specs | **Tier 2 signal path**: reflection results as upward signals |

## Sub-Plans

| # | Sub-Plan | Scope | Primary Files |
|---|----------|-------|---------------|
| [17-1](17-1-error-memory-rag.md) | Error Memory & Prompt Augmentation (Tier 1) | Error capture pipeline, RAG indexing of run errors, retrieval at decision points, prompt injection | `engine/error_memory.py` (new), `server/run_manager.py`, `rag/indexer.py`, `executors/llm.py` |
| [17-2](17-2-reflection-node.md) | Reflection Node (Tier 2) | Post-run reflection executor, causal principle extraction, structured memory persistence, reflection scheduling | `models/nodes.py`, `executors/reflection.py` (new), `engine/error_memory.py`, `server/run_manager.py` |
| [17-3](17-3-self-generating-rules.md) | Self-Generating Rules (Tier 3) | Principle → hyperedge conversion (prompt/tool guidance), runtime parameter mutations, rule lifecycle, effectiveness tracking, safety bounds | `engine/rule_generator.py` (new), `engine/scheduler.py`, `engine/events.py`, `server/app.py` |
| [17-4](17-4-observability-event-wiring.md) | Observability & Event Wiring | Wire all 11 self-evolving `EventType` values into emission sites for debugging, UI visibility, and audit trails | `server/run_manager.py`, `executors/llm.py`, `engine/scheduler.py` |
| [17-5](17-5-workflow-experience-summaries.md) | Workflow Experience Summaries | Bridge spec only (archived/deferred): workflow-local summary + export payload contract. Marked completed as handoff; Phase 11 (`19-1`) owns all implementation/indexing/reuse work. | `engine/experience.py` (new), `server/run_manager.py`, `server/app.py` |

## Dependencies / Sequencing

1. **Tier 1 (17-1) can start immediately.**
   - 9A memory system: **done** (14-\*).
   - 13-1 run observability: **partially landed** — run summaries, event logs, and query APIs are all available. Task 2-3 (deep failure context) is not done but the existing `RunResult.errors` + `NODE_FAILED` events provide sufficient error signal.
   - RAG subsystem: **done** (Phase 6).

2. **Tier 2 (17-2) benefits from but does not strictly require 13-2.**
   - 13-2 (debug workbench) adds checkpoint portals and variable inspector — useful for richer reflection input, but Tier 2 can work with 13-1's event logs.
   - 14-3 long-chain memory: **done** — `ConsolidationPipeline` available for principle compaction.

3. **Tier 3 (17-3) requires stable hyperedge runtime.**
   - Hyperedge models + resolver + scheduler integration: **already implemented** (`models/hyperedges.py`, `engine/hyperedge_runtime.py`, scheduler hooks, executor hooks).
   - `AddHyperedge` mutation: **exists**.
   - Gap: Tier 3 needs a programmatic path from reflection output → well-formed `Hyperedge` with correct attachment scope — this is new logic, not blocked by missing infrastructure.

4. **Recommended sequence: 17-1 → 17-2 → 17-3** (serial). Each tier produces output consumed by the next. However, the models/schemas for all three tiers can be designed upfront.

## Success Criteria

- Past run errors are automatically indexed into a dedicated RAG collection at run completion.
- Before LLM decision points, relevant past errors are retrieved and injected as prompt context.
- Orchestrator demonstrably avoids repeating a failure pattern after encountering it once (measurable via regression test).
- Reflection node produces structured causal principles from raw error data (Tier 2).
- Reflection output can be promoted to hyperedge rules that modify future node behavior (Tier 3).
- All tiers are opt-in via configuration flags; existing workflows are unaffected.
- Error memory and principle retrieval remain workflow-scoped in 9D; global reuse is deferred to Phase 11.

## Decisions

- **Tier 1 is prompt augmentation, not architectural change.** Retrieved errors are injected as additional prompt context via the hyperedge `pre_prompt` hook. No new node types, no new execution patterns — just a retrieval→injection pipeline wired into existing hooks.
- **Error indexing happens at run completion, not during execution.** Post-run indexing is simpler, avoids mid-execution RAG writes, and ensures the full error context (including downstream cascading failures) is captured.
- **One RAG collection per workflow in 9D.** Cross-workflow/global reuse is explicitly deferred to Phase 11 (`19-1`).
- **Tier 2 is a new node type (`ReflectionNode`), not a post-run script.** Making reflection a graph-level construct means it can be composed, scheduled, and observed like any other node.
- **Tier 3 uses runtime adaptation, not persistent graph mutation.** Hyperedge rules are injected at runtime; parameter fixes are applied via temporary runtime graph mutation. The persisted graph JSON stays clean.
- **Safety bounds are non-negotiable.** Self-modifying behavior requires: max rules per workflow, rule TTL/expiry, human approval gate (optional), auto-disable on regression, and audit trail of all generated rules.

## Notes

- This bridges three previously independent subsystems (RAG, memory, hyperedges) into a closed learning loop. It is one of the most architecturally interesting features in the roadmap.
- **Critical finding:** the existing `OrchestratorExecutor` does NOT call an LLM — `orchestrator_prompt` and `orchestrator_model` are declared on the model but never used. The executor is a pure event-processing loop that spawns teams and monitors completion. Tier 1 must target the LLM nodes *within* departments (or any node tagged `error_aware`) rather than the orchestrator executor itself. See 17-1 decision on injection point.
- The `orchestrator_log` in the executor already captures all events including `NODE_FAILED` — useful signal for post-run error indexing.
- Phase 14 top-level plan already noted (line 63): "This phase is the core enabler for self-evolving orchestrator Tier 1 behavior."
- The EvoAgentX survey (backlog) may surface relevant patterns for self-improvement. Consider reviewing before 17-2 design is finalized.
- A team-matching bug exists in `OrchestratorExecutor` (~line 958–972): `sk == layer[0]` never matches because `layer[0]` is the orchestrator node ID, not the sub-graph key. This doesn't block 9D but affects orchestrator observability and should be tracked in bugs.md.
