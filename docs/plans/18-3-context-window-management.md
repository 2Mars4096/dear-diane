# 18-3: Agent-Directed Context Architecture

**Parent:** [18-token-optimization](18-token-optimization.md)
**Status:** in-progress
**Goal:** Move structured state out of context and into local storage, give agents explicit tools to page context in/out (MemGPT pattern), apply intelligent loop compaction, and provide advisory token budgets — without any brute-force truncation.
**Filename note:** File slug is retained for link stability (`18-3-context-window-management.md`); scope is Agent-Directed Context Architecture.

## Motivation

Context windows have hard limits (4K–2M tokens depending on model), and even within limits, longer contexts degrade attention quality and increase cost linearly. Current behavior is naive: all inputs are concatenated into the prompt without regard for budget. Loops accumulate full history across iterations. Structured metadata that could live on disk is inlined into prompts.

The solution is not to truncate what doesn't fit, but to **architect what goes in**: externalize structured state to local storage, let agents pull context on demand via tools, and apply intelligent compaction (summarization, diffing) where the system can do it without information loss.

## Existing Infrastructure

| Component | What exists | Gap |
|---|---|---|
| `CompactionRule` (`models/context.py`) | Shared compaction model (`strategy`, `window_size`, `max_tokens`) already referenced by loop/team control-flow nodes | Runtime behavior is not consistently token-aware; needs richer strategy controls and executor wiring |
| `max_iterations` on loop nodes | Bounds iteration count, indirectly bounds context growth | No explicit token-aware compaction between iterations |
| `ContextEdge` + context declarations | `context_key` / `mode` with explicit read/write sets and boundary contracts | Controls data access and flow direction, but not token volume or agent-directed loading policy |
| `run_budget` on `EngineConfig` (15-3) | Per-run cost budget with enforcement modes | Cost budget, not token budget — no per-node allocation |
| `target_input_tokens` (proposed in 18-1) | Per-node advisory token budget | Guides context assembly; no brute-force enforcement |

## Tasks

- [x] 1. Externalized structured state (Beads pattern)
  - [x] 1-1. **Principle:** Structured data (loop counters, iteration statuses, intermediate results, node metadata, task dependencies, configuration snapshots) should be persisted locally on disk and accessed via tools — never bulk-loaded into LLM context. Context carries *intent and instructions*; structured data lives in storage.
  - [x] 1-2. Create `StateStore` abstraction in `src/dan/engine/state_store.py` *(new)*: local filesystem-backed (default) or pluggable backend. API: `write(scope, key, value)`, `read(scope, key)`, `query(scope, filter)`, `list_keys(scope)`. Scope = `run_id` / `session_id` / `workflow_id`. Values are typed JSON — Pydantic-serializable.
  - [ ] 1-3. **Automatic state externalization:** The scheduler/executor automatically writes structured state to `StateStore` instead of passing it inline through edges: (a) loop iteration metadata (counter, status, timing), (b) `ForEach` item status tracking, (c) agent team turn history metadata, (d) node execution summaries (status, token usage, cost — already in `CostTracker`, now also in `StateStore` for agent access).
  - [x] 1-4. **Agent access via `read_state` tool (18-1 task 9):** LLM nodes with `agent_context_tools=True` can read specific state entries on demand. The system injects a brief state summary (e.g., "Loop iteration 5/10, 3 succeeded, 1 failed") into context — the agent calls `read_state("iteration_4_result")` only if it needs details.
  - [x] 1-5. **Typed state schemas:** Define Pydantic models for common state types: `LoopIterationState`, `TeamTurnState`, `NodeExecutionSummary`. These are the structured "beads" that agents query — not free-text memory entries.
  - [x] 1-6. Emit `STATE_EXTERNALIZED` event: node_id, scope, keys_written, tokens_saved (vs. inline passing)

- [x] 2. Agent-directed conversation history management
  - [x] 2-1. **No silent message dropping.** Older messages are not deleted from the agent's reach — they are moved to memory/storage and remain accessible via context tools (18-1 task 9). The agent always knows what history exists and can pull it back.
  - [x] 2-2. Add `HistoryPolicy` model: `inline_recent: int | None` (number of recent messages to include inline — advisory), `summarize_older: bool` (default True), `summary_model: str | None`, `keep_system: bool` (default True)
  - [x] 2-3. Add `history_policy: HistoryPolicy | None` field to `LLMOperator` — applies when the node receives conversation-style message lists
  - [x] 2-4. **History assembly:** System message (always inline) + summary of older messages (if `summarize_older`) + recent N messages inline + a manifest noting "N older messages available via `search_context()`". The agent sees enough to continue and can recover any older message on demand.
  - [ ] 2-5. When `summarize_older=True`, generate a summary using `summary_model` (defaults to cheapest available). **Reuse the LLM summarization callback from 14-3's `ConsolidationPipeline`**. Persist the summary as a `MemoryItem(entry_type=distilled_fact)`.
  - [x] 2-6. **Older messages to memory:** Messages not included inline are persisted to 14-3's `ShortTermMemory` as `MemoryItem(entry_type=raw_event)`. They remain retrievable via `MemoryQuery` or context tools. Nothing is permanently lost.
  - [ ] 2-7. Share history/compaction primitives with `CompactionRule` so team/loop/node behavior stays consistent

- [ ] 3. Loop context compaction
  - [x] 3-1. Extend existing `CompactionRule` (`models/context.py`) with token-aware knobs needed at runtime (e.g., `summarize_every_n`, `summary_model`, optional `target_tokens`, `require_persistent_recall: bool = True`), while preserving backward compatibility for current fields.
  - [x] 3-2. Reuse existing `compaction_rule` field on `GateNode` (`gate_mode="while"`), `WhileLoopNode`, `ForEachNode`, and `AgentTeamNode` instead of introducing a second loop-compaction model.
  - [ ] 3-3. Strategy implementations aligned to existing `CompactionStrategy` values:
    - `none`: current behavior — accumulated state preserved (default for backward compat)
    - `sliding_window`: keep only the last `window_size` iteration payloads **only when guaranteed persistent recall is enabled** (older payloads written to memory/state stores and retrievable via tools)
    - `summarize`: periodically summarize accumulated payloads (via `summarize_every_n`) and replace with compact representation
    - `diff_based`: pass only iteration deltas for refinement-style loops
    - `keep_last`: keep only the newest iteration payload **only when guaranteed persistent recall is enabled** (older payloads written to memory/state stores and retrievable via tools)
  - [ ] 3-4. Compaction runs between iterations (after one iteration completes, before the next begins). Cost of summarization calls tracked in `CostTracker`. **Memory pipeline coordination:** Compacted iteration content is written to 14-3's `ShortTermMemory` buffer as `MemoryItem` entries, and the `ConsolidationPipeline` decides whether to promote them to long-term memory. **State externalization coordination:** Iteration metadata (counter, status, timing, result summary) is written to `StateStore` (task 1) — agents can query specific iteration results without loading the full history. Nothing is discarded — it's transferred to memory and state stores where it remains searchable.
  - [ ] 3-5. **Shared compaction runtime with 14-3:** The `ShortTermMemory` buffer (`engine/memory_pipeline.py`) already implements `sliding_window`, `keep_last`, and `diff_based` compaction strategies with token-aware eviction. Loop compaction should delegate to this implementation rather than creating a parallel one. The `summarize` strategy uses the same LLM callback. This unification means a single compaction implementation serves loops, teams, windowing, and long-chain memory.
  - [x] 3-6. Emit `LOOP_COMPACTION_APPLIED` event: loop_node_id, iteration, strategy, tokens_before, tokens_after, items_persisted_to_memory
  - [x] 3-7. **Safety gate for lossy compaction:** If strategy is `sliding_window` or `keep_last`, runtime validation must require persistent recall to be active (`require_persistent_recall=True` + successful writes to `ShortTermMemory` and `StateStore`). Otherwise, fail validation and suggest `summarize`/`diff_based` as safe defaults.

- [x] 4. Advisory token budget (guidance, not enforcement)
  - [ ] 4-1. Add `token_budget: int | None` to `EngineConfig` — advisory total input token budget for the entire run (distinct from `run_budget` which is cost-based). **This is a planning signal, not a hard cap.** It guides context assembly decisions (how aggressively to defer inputs, when to suggest JIT loading) but never blocks execution.
  - [x] 4-2. Create `TokenBudgetAdvisor` in `src/dan/engine/token_optimization.py`: computes per-node advisory budgets using strategies:
    - `proportional`: allocate based on historical usage (from prior run or estimate)
    - `priority`: allocate based on node priority metadata
    - `adaptive`: start proportional, adjust based on actual usage
  - [x] 4-3. Advisory budgets flow into each node's `target_input_tokens` (coordinated with 18-1 task 1). When a node's assembled context exceeds its advisory budget, the system prefers deferral and JIT loading over any form of cutting. If it still exceeds after smart assembly, execution proceeds with a warning — the agent gets the context it needs.
  - [ ] 4-4. **Unified budget awareness across all token sources.** A node's effective input comes from multiple sources: (a) direct edge data, (b) system prompt, (c) context edge injections, (d) hyperedge injections, (e) memory retrieval (14-3's `MemoryInjectionPolicy`), (f) RAG chunk retrieval (9-1's `RAGExecutor`). The advisor accounts for all sources when computing advisory allocations. Memory/RAG reservation should be token-based (percentage + telemetry), not count-based.
  - [x] 4-5. Dynamic reallocation (adaptive mode): when a node completes using less than its advisory allocation, surplus is redistributed to remaining pending nodes
  - [x] 4-6. Emit `BUDGET_ADVISORY` event: node_id, advisory_tokens, actual_tokens, sources_breakdown

- [ ] 5. Tests
  - [ ] 5-1. `StateStore` unit tests: write/read/query/list_keys, scope isolation, typed serialization, concurrent access
  - [ ] 5-2. State externalization integration tests: loop iteration metadata written to `StateStore`, agent can `read_state()` to access specific iterations
  - [x] 5-3. History policy tests: recent messages inline, older messages persisted to memory, summary generation, agent can retrieve older messages via context tools
  - [ ] 5-4. Loop compaction integration tests: sliding window across 10 iterations, summarize compaction cadence (`summarize_every_n`), diff-based delta computation, compacted content queryable in memory and `StateStore`
  - [x] 5-5. Lossy-compaction safety tests: `sliding_window` / `keep_last` fail validation when persistent recall guarantees are not active; pass only when memory/state persistence and retrieval checks succeed
  - [x] 5-6. Advisory budget tests: proportional/priority/adaptive advisory computation, dynamic reallocation, advisory warnings emitted (never execution blocked)
  - [ ] 5-7. Backward compat: nodes/loops without state externalization, history policy, or budget settings behave identically

## Primary Files

- `src/dan/engine/state_store.py` *(new)* — `StateStore` protocol and `FileSystemStateStore` implementation; typed state schemas (`LoopIterationState`, `TeamTurnState`, `NodeExecutionSummary`)
- `src/dan/engine/token_optimization.py` — `TokenBudgetAdvisor`, compaction helpers
- `src/dan/engine/memory_pipeline.py` — reuse `ShortTermMemory` buffer for history/compaction persistence; share compaction strategies and `ConsolidationPipeline` summarization callback
- `src/dan/models/nodes.py` — `HistoryPolicy` model; `history_policy` field on `LLMOperator`
- `src/dan/models/context.py` — extend `CompactionRule` and (if needed) `CompactionStrategy` for token-aware loop behavior
- `src/dan/models/control_flow.py` — wire enhanced `compaction_rule` usage across `GateNode`, `WhileLoopNode`, `ForEachNode`, `AgentTeamNode`
- `src/dan/engine/executor.py` — `token_budget` on `EngineConfig` (advisory)
- `src/dan/executors/llm.py` — history assembly with summary + manifest; advisory budget awareness
- `src/dan/executors/control_flow.py` — compaction runtime delegating to `ShortTermMemory` buffer; state externalization to `StateStore`
- `src/dan/engine/events.py` — `STATE_EXTERNALIZED`, `LOOP_COMPACTION_APPLIED`, `BUDGET_ADVISORY` events
- `src/dan/engine/scheduler.py` — budget advisor initialization, state store lifecycle, reallocation on node completion

## Decisions

- `HistoryPolicy`/`history_policy` landed for conversation-style inputs; older messages are summarized and persisted as `raw_event` memory entries rather than silently dropped.
- Lossy loop compaction (`sliding_window`/`keep_last`) now enforces persistent-recall safety gates at runtime for `WhileLoopExecutor`.
- Advisory token budgeting is runtime-wired through `TokenBudgetAdvisor` in scheduler, emitting `BUDGET_ADVISORY` pre/post execution and feeding `target_input_tokens` when unset.

## Notes

- **No brute-force truncation anywhere in this plan.** No head/tail/middle_out strategies. All context management is either (a) agent-directed (the agent decides what to load via tools), (b) intelligent compaction (summarization, diffing — the system makes a smart decision), or (c) externalization (structured data moves to disk, agent queries it).
- **Externalized structured state is the highest-impact architectural change.** Inspired by Beads: loop metadata, iteration results, team turn history — these are structured data that belong in a queryable store, not inlined into prompts. A 10-iteration loop currently dumps all 10 iteration outputs into context. With externalization, context carries only a summary + the agent can `read_state("iteration_7_result")` if needed.
- **Loop compaction remains the single highest-impact runtime optimization.** `sliding_window(2)` or `diff_based` can reduce loop context by 80%+. This is smart compaction — it summarizes or diffs rather than cutting.
- **`sliding_window` / `keep_last` are conditionally allowed.** They are only acceptable when guaranteed persistent recall is active (memory + state persistence with retrieval checks). Without this guarantee, they are treated as unsafe and should fail validation.
- **Agent-directed history is MemGPT for conversations.** Older messages move to memory (not deleted), a summary stays inline, and the agent can search/retrieve any older message via context tools. The agent controls its own recall.
- **Advisory budgets guide without blocking.** The budget advisor helps context assembly decide how aggressively to defer inputs and use JIT loading. It never prevents execution. If the agent needs all the context, it gets all the context — with a telemetry warning for the analytics dashboard.
- **`StateStore` complements `MemoryStore`.** `MemoryStore` (14-1) stores free-form session memory for cross-run continuity. `StateStore` stores typed, structured execution state for within-run agent access. They serve different purposes: memory is for recall, state is for structured queries. Both are local-first.
- **Single compaction implementation across all contexts.** 14-3's `ShortTermMemory` buffer already implements the core compaction strategies with token-aware eviction. Loop compaction and conversation history management should delegate to this implementation.
