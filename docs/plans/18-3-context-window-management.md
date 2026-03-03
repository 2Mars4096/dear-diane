# 18-3: Context Window Management

**Parent:** [18-token-optimization](18-token-optimization.md)
**Status:** not-started
**Goal:** Prevent context overflow and optimize token allocation through configurable truncation strategies, conversation windowing, loop compaction, and cross-node token budget distribution.

## Motivation

Context windows have hard limits (4K–2M tokens depending on model), and even within limits, longer contexts degrade attention quality and increase cost linearly. Current behavior is naive: all inputs are concatenated into the prompt without regard for budget. Loops accumulate full history across iterations. There is no mechanism to distribute a run-level token budget across nodes.

This sub-plan introduces structured management of what goes into each LLM call and how token budgets are allocated across a workflow.

## Existing Infrastructure

| Component | What exists | Gap |
|---|---|---|
| `CompactionRule` on `AgentTeamNode` | `max_messages`, `summary_model`, `summary_prompt` for team conversation compaction | Scoped to teams only; not available for general loops or standalone nodes |
| `max_iterations` on loop nodes | Bounds iteration count, indirectly bounds context growth | No explicit token-aware compaction between iterations |
| `ContextEdge.context_type` | `global`, `local`, `pass_down`, `emit_up` scope control | Controls information flow direction but not volume |
| `run_budget` on `EngineConfig` (15-3) | Per-run cost budget with enforcement modes | Cost budget, not token budget — no per-node allocation |
| `max_input_tokens` (proposed in 18-1) | Per-node input token cap | Needs truncation strategies to enforce meaningfully |

## Tasks

- [ ] 1. Per-node truncation policies
  - [ ] 1-1. Add `TruncationPolicy` model: `strategy` (Literal: `"error"`, `"head"`, `"tail"`, `"middle_out"`, `"priority"`), `max_tokens: int | None`, `preserve_system: bool` (default True — never truncate system prompt)
  - [ ] 1-2. Add `truncation_policy: TruncationPolicy | None` field to `LLMOperator`. When set, applies before prompt assembly.
  - [ ] 1-3. Implement `TruncationEngine` in `src/dan/engine/token_optimization.py`:
    - `head`: keep first N tokens of each input
    - `tail`: keep last N tokens (useful for conversation history — recent messages matter most)
    - `middle_out`: keep first K and last K tokens, drop middle (preserves start context + recent state)
    - `priority`: sort inputs by priority metadata, keep highest-priority inputs until budget is filled
  - [ ] 1-4. Priority metadata: add optional `priority: int` (default 0) to input port declarations. System prompt implicitly has highest priority. Ports with explicit priority are kept in descending order.
  - [ ] 1-5. Emit `TRUNCATION_APPLIED` event: node_id, strategy, tokens_before, tokens_after, inputs_truncated (list of port names)

- [ ] 2. Conversation history windowing
  - [ ] 2-1. Add `HistoryWindow` model: `max_messages: int | None`, `max_tokens: int | None`, `keep_system: bool` (default True), `summarize_dropped: bool` (default False), `summary_model: str | None`
  - [ ] 2-2. Add `history_window: HistoryWindow | None` field to `LLMOperator` — applies when the node receives conversation-style message lists
  - [ ] 2-3. Windowing logic: keep system message (if `keep_system`) + optional summary of dropped messages + last N messages within budget
  - [ ] 2-4. When `summarize_dropped=True`, generate a one-shot summary of dropped messages using `summary_model` (defaults to cheapest available). Summary prefixed as a system/user message: "Summary of earlier conversation: ..."
  - [ ] 2-5. Generalize `CompactionRule` from `AgentTeamNode` to use `HistoryWindow` — `CompactionRule` becomes a thin wrapper or alias for backward compatibility

- [ ] 3. Loop context compaction
  - [ ] 3-1. Add `LoopCompaction` model: `strategy` (Literal: `"none"`, `"sliding_window"`, `"summarize_every"`, `"diff_only"`, `"keep_last"`), strategy-specific config fields
  - [ ] 3-2. Add `loop_compaction: LoopCompaction | None` field to `WhileGateNode` and `ForEachNode`
  - [ ] 3-3. Strategy implementations:
    - `none`: current behavior — all iteration results accumulate (default for backward compat)
    - `sliding_window(n)`: keep only last N iteration results in context; drop earlier iterations
    - `summarize_every(n)`: after every N iterations, LLM-summarize accumulated results into a compact representation that replaces them
    - `diff_only`: only pass the delta between current and previous iteration (useful for iterative refinement loops like review-revise)
    - `keep_last`: only keep the most recent iteration's output; discard all prior iterations
  - [ ] 3-4. Compaction runs between iterations (after one iteration completes, before the next begins). Cost of summarization calls tracked in `CostTracker`.
  - [ ] 3-5. Emit `LOOP_COMPACTION_APPLIED` event: loop_node_id, iteration, strategy, tokens_before, tokens_after

- [ ] 4. Token budget allocation
  - [ ] 4-1. Add `token_budget: int | None` to `EngineConfig` — total input token budget for the entire run (distinct from `run_budget` which is cost-based)
  - [ ] 4-2. Create `TokenBudgetAllocator` in `src/dan/engine/token_optimization.py`:
    - `equal`: divide budget equally across all LLM nodes
    - `proportional`: allocate based on historical usage (from prior run or estimate)
    - `priority`: allocate based on node priority metadata (critical nodes get more budget)
    - `adaptive`: start with equal allocation, redistribute dynamically based on actual usage
  - [ ] 4-3. Allocated budgets flow into each node's `max_input_tokens` (coordinated with 18-1 task 1). If a node already has an explicit `max_input_tokens`, use `min(allocated, explicit)`.
  - [ ] 4-4. Dynamic redistribution (adaptive mode): when a node completes using less than its allocation, surplus is redistributed to remaining pending nodes
  - [ ] 4-5. Emit `BUDGET_ALLOCATED` event: node_id, allocated_tokens, source (initial allocation or redistribution)

- [ ] 5. Tests
  - [ ] 5-1. Truncation unit tests: each strategy (head, tail, middle_out, priority), preserve_system invariant, priority ordering
  - [ ] 5-2. Windowing unit tests: message count limit, token limit, summary generation, keep_system flag
  - [ ] 5-3. Loop compaction integration tests: sliding window across 10 iterations (verify only last N visible), summarize_every compaction, diff_only delta computation
  - [ ] 5-4. Budget allocation tests: equal/proportional/priority/adaptive distribution, dynamic redistribution, interaction with explicit max_input_tokens
  - [ ] 5-5. Backward compat: nodes/loops without truncation/compaction/budget settings behave identically

## Primary Files

- `src/dan/engine/token_optimization.py` — `TruncationEngine`, `TokenBudgetAllocator`, `LoopCompaction` strategies
- `src/dan/models/nodes.py` — `TruncationPolicy`, `HistoryWindow` models; `truncation_policy`, `history_window` fields on `LLMOperator`
- `src/dan/models/control_flow.py` — `LoopCompaction` model; `loop_compaction` field on `WhileGateNode`, `ForEachNode`; `CompactionRule` backward compat alias
- `src/dan/engine/executor.py` — `token_budget` on `EngineConfig`
- `src/dan/executors/llm.py` — apply truncation and windowing before prompt assembly
- `src/dan/executors/control_flow.py` — loop compaction in `WhileGateExecutor`, `ForEachExecutor`, `AgentTeamExecutor`
- `src/dan/engine/events.py` — `TRUNCATION_APPLIED`, `LOOP_COMPACTION_APPLIED`, `BUDGET_ALLOCATED` events
- `src/dan/engine/scheduler.py` — budget allocator initialization, redistribution on node completion

## Decisions

- (filled in during execution)

## Notes

- **Loop compaction is the single highest-impact optimization in this phase.** A 10-iteration review-revise loop currently accumulates all 10 iterations in context — easily 100K+ tokens. `sliding_window(2)` or `diff_only` can reduce this by 80%+.
- **`middle_out` truncation is inspired by Anthropic's recommendation** for long-context usage: models attend best to the beginning and end of context. Dropping the middle preserves the most salient information.
- **Conversation windowing generalizes what `CompactionRule` does for teams.** The migration path is to make `CompactionRule` a thin wrapper around `HistoryWindow`, keeping backward compatibility while exposing the general mechanism.
- **Token budget allocation is approximate.** Tokenizer counts vary by model (GPT-4 vs. Claude vs. Gemini use different tokenizers). Allocation should use estimates and leave 10–20% headroom.
- **Adaptive redistribution requires topological awareness** — the allocator needs to know which nodes are downstream (pending) and which are done. This information is available from the scheduler's execution state.
