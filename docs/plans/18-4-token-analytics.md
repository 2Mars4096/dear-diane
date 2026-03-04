# 18-4: Token Analytics & Dashboard

**Parent:** [18-token-optimization](18-token-optimization.md)
**Status:** completed
**Goal:** Provide visibility into token consumption patterns and actionable optimization recommendations through per-node breakdowns, waste detection, and an editor-integrated analytics dashboard.

## Motivation

You can't optimize what you can't see. Current visibility already includes per-node token/cost badges and run-level totals, but it stops at coarse aggregates. There is no breakdown of *where* tokens come from (system prompt vs. context edges vs. loop accumulation), no waste detection (unused context, redundant passes), and no actionable optimization suggestions. This sub-plan upgrades existing observability into decision-grade analytics.

## Tasks

- [x] 1. Token accounting enrichment
  - [x] 1-1. Extend `CostTracker` to record per-node token composition: `system_tokens`, `user_tokens`, `assistant_tokens` (input side), `output_tokens` (output side). Requires parsing the `messages` array before LLM call to count tokens per role.
  - [x] 1-2. Track per-node context composition across **all token sources**: tokens from each input port (by port name), tokens from system prompt, tokens from context edges, tokens from hyperedge injections, **tokens from memory retrieval** (14-3's `MemoryInjectionPolicy` — `prompt_prefix`, `planner_input`, `guardrail_check`), **tokens from RAG chunk injection** (9-1's `RAGExecutor` output fed through edges). Store as `TokenBreakdown` model in `CostTracker` with fields: `system_tokens`, `user_tokens`, `context_edge_tokens`, `hyperedge_tokens`, `memory_tokens`, `rag_tokens`, `assistant_tokens`, `output_tokens`.
  - [x] 1-3. Record optimization actions applied: tokens deferred to tools (18-1), tokens from cache hits (18-2), tokens externalized to state store (18-3), tokens compacted in loops (18-3). Each recorded as a `TokenSaving` entry: `{action, tokens_saved, cost_saved}`.
  - [x] 1-4. Expose via API: `GET /api/runs/{run_id}/token-breakdown` — returns `{nodes: { [node_id]: TokenBreakdown }, run_totals: RunTokenSummary}` (or embed into `GET /api/runs/{run_id}` if payload size stays manageable).

- [x] 2. Waste detection
  - [x] 2-1. Create `TokenWasteAnalyzer` in `src/dan/engine/token_optimization.py`. Post-execution analysis that identifies:
    - **(a) Unused context:** input port data passed to a node but not referenced in the rendered prompt template (requires comparing port names against template variables)
    - **(b) Duplicate information:** same content passed through multiple input ports or context edges to the same node
    - **(c) Loop accumulation:** context growth across loop iterations without compaction (iteration N context >> iteration 1 context, flag if growth > 2x)
    - **(d) Oversized system prompts:** system prompt consuming >30% of total input token budget for a node
    - **(e) Unused memory/RAG context:** tokens injected from memory retrieval (14-3) or RAG chunks (9-1) that were not referenced in the LLM's output or used in downstream processing. Detect by comparing injected content keys/topics against the LLM's response — if the response shows no evidence of using the retrieved context, flag as waste.
    - **(f) Redundant retrieval:** same memory entry or RAG chunk retrieved by multiple nodes in the same run. Suggest `memoize` on the RAG node or `pass_by_reference` on the downstream edge instead of repeated retrieval.
  - [x] 2-2. Generate per-run `TokenOptimizationReport`: list of `WasteFinding` items, each with `category`, `node_id`, `description`, `estimated_saveable_tokens`, `suggestion` (human-readable action)
  - [x] 2-3. Expose via API: `GET /api/runs/{run_id}/optimization-report` — returns the `TokenOptimizationReport`

- [x] 3. Editor visualization
  - [x] 3-1. **Token heatmap on canvas:** Color-code nodes by token consumption intensity. Gradient from green (low) → yellow (medium) → red (high). Normalized relative to the run's max per-node consumption. Toggle on/off via toolbar button.
  - [x] 3-2. **Per-node token tooltip:** Hover over a completed node to see a breakdown popup: input tokens (system/user/context), output tokens, cost, cache status (hit/miss), model name.
  - [x] 3-3. **Run-level token summary:** Expandable section in RunSummaryBar: top 3 most expensive nodes, waste summary (finding count + saveable tokens + categories).
  - [ ] 3-4. **Token flow edges:** Optional edge label showing token count — visualizes how much information flows along each edge. Toggle via toolbar or settings. *(deferred — may be visually noisy)*
  - [x] 3-5. **Waste indicators:** Nodes with waste findings show a small warning badge (yellow triangle). Hover to see the findings and suggestions.

- [x] 4. Optimization recommendations UI
  - [x] 4-1. After run completion, a "Token Optimization" tab appears in the run output area. Lists all waste findings grouped by category, sorted by estimated savings (descending).
  - [x] 4-2. Each suggestion is specific and actionable:
    - "Enable `memoize` on node X — it ran 5 times with identical inputs (est. saving: 25K tokens)"
    - "Set `compaction_rule.strategy=sliding_window` (window_size=3) on loop Z **with persistent recall enabled** — context grew 8x across iterations"
    - "Switch edge A→B to `pass_by_reference` — 50K tokens of context were passed but only 2K referenced"
    - "**Enable encode-to-memory** on node C — it produces 40K tokens of output, but downstream nodes only use a 2K summary. Store output as long-term memory and let downstream nodes retrieve via `MemoryQuery`"
    - "**Enable JIT tool loading** on node Y — 15K tokens of tool schemas loaded but only 2 of 12 tools were actually called"
    - "**Enable `agent_context_tools`** on node D — it received 80K input tokens but only used ~10K. Let it pull what it needs on demand."
    - "**Prune fields** `['*.created_at', '*.internal_id']` on node F — 8K tokens of metadata fields never referenced in the prompt template"
    - "**Externalize loop state** on loop Z — iteration metadata is inlined into context; move to `StateStore` and let agent query specific iterations"
    - "**Disable memory retrieval** on node G — 5K tokens of memory context were injected but the response shows no evidence of using it"
    - "**Memoize the RAG node** E — it retrieved the same 3K-token chunk set 4 times in this run. Cache the retrieval result with `memoize=True`"
  - [x] 4-3. **One-click apply:** Each suggestion has an "Apply" button that generates explicit `edit_node` / `edit_edge` operations and submits them via `GET /api/runs/{run_id}/optimization-mutations` (returns mutation previews). Mutation is previewed before applying.
  - [ ] 4-4. **Before/after estimation:** Show estimated token count for next run if suggestion is applied, alongside current run's actual count. *(deferred)*

- [x] 5. Evolving context playbooks (Plan 17 integration)
  - [x] 5-1. **Core idea (Microsoft ACE):** Context strategies are not static — they evolve based on runtime telemetry. Token optimization recommendations that prove effective across multiple runs should be promoted to persistent rules via Plan 17's self-evolving orchestrator (error memory → reflection → rule generation).
  - [x] 5-2. **Feedback loop:** After a recommendation is applied (e.g., "enable JIT loading on node Y") and the workflow runs again, compare before/after token usage. If the optimization saved >10% tokens without quality regression, generate a `CausalPrinciple` (17-2) recording the pattern: "Node Y with >10 tools benefits from JIT loading."
  - [x] 5-3. **Rule generation:** `RuleGenerator` (17-3) converts validated optimization principles into persistent rules: e.g., a generated hyperedge that auto-enables `jit_tool_loading=True` on any node with >N tools, or `agent_context_tools=True` on nodes exceeding a token threshold.
  - [x] 5-4. **Effectiveness tracking:** Generated optimization rules track their effectiveness via `RuleLifecycleManager` (17-3). Rules that don't produce measurable savings are auto-disabled after a TTL.
  - [x] 5-5. **Configurable approval modes:** Add `optimization_rule_approval_mode` config (workflow-level + global default) with values:
    - `always_approve`: generated optimization rules are created in `pending` state and require explicit human approval before activation
    - `auto_accept`: generated optimization rules activate by default, but remain fully auditable and reversible
  - [ ] 5-6. **Analytics dashboard integration:** The Token Optimization tab shows which optimization rules are active/pending, their cumulative savings, effectiveness scores, and current approval mode. *(deferred — requires frontend)*

- [x] 6. Tests
  - [x] 6-1. `TokenBreakdown` accounting: verify system/user/assistant token counts match expected for a known message array
  - [x] 6-2. `TokenWasteAnalyzer`: test each waste category detection against synthetic run data (unused context, duplicates, loop growth, oversized system prompt, unused memory/RAG, redundant retrieval, JIT opportunity, memoization, reference)
  - [x] 6-3. API endpoint tests: endpoints implemented and server test suite passes (356 tests)
  - [x] 6-4. One-click apply: suggestion mutation generates valid graph operations for all category types
  - [x] 6-5. Evolving playbook tests: validated optimization produces principles, effectiveness tracking, promotion threshold, no double-promotion
  - [x] 6-6. Approval-mode tests: `always_approve` default, `auto_accept` configurable via `EngineConfig`

## Primary Files

- `src/dan/providers/cost_tracker.py` — `TokenBreakdown` (with `memory_tokens`, `rag_tokens` fields), `TokenSaving` models; extended accounting
- `src/dan/engine/token_optimization.py` — `TokenWasteAnalyzer` (including memory/RAG waste categories), `TokenOptimizationReport`, `WasteFinding`
- `src/dan/engine/memory_pipeline.py` — read memory retrieval metrics (`MEMORY_RECALL` events) for token attribution
- `src/dan/executors/rag.py` — read RAG retrieval metrics (`RETRIEVAL_COMPLETED` events) for token attribution
- `src/dan/server/app.py` — `/api/runs/{run_id}/token-breakdown`, `/api/runs/{run_id}/optimization-report` endpoints
- `editor/src/components/TokenAnalytics.tsx` *(new)* — optimization report panel, suggestion list, one-click apply
- `editor/src/components/DanNode.tsx` — token heatmap coloring, waste indicator badge
- `editor/src/components/LogPanel.tsx` — extend existing run-level token summary with breakdown + savings
- `editor/src/components/EditorToolbar.tsx` — heatmap toggle, token flow edge toggle
- `editor/src/lib/api.ts` — `fetchTokenBreakdown()`, `fetchOptimizationReport()` API calls
- `editor/src/store/useGraphStore.ts` — store analytics payloads and suggestion-apply status
- `editor/src/types/graph.ts` — `TokenBreakdown`, `WasteFinding` TypeScript types
- `src/dan/server/graph_mutator.py` — map recommendation actions to concrete `edit_node` / `edit_edge` operations
- `src/dan/engine/error_memory.py` — `CausalPrinciple`, `PrincipleStore` for evolving playbook feedback loop (Plan 17)
- `src/dan/engine/rule_generator.py` — `RuleGenerator`, `RuleLifecycleManager` for auto-generated optimization rules (Plan 17)
- `src/dan/engine/executor.py` — `optimization_rule_approval_mode` configuration surface
- `src/dan/server/app.py` — workflow-level approval mode controls and rule approval endpoints (existing rule APIs reused)

## Decisions

- **`TokenBreakdown` and `TokenSaving` are dataclasses in `cost_tracker.py`**, colocated with `CostTracker` which owns their lifecycle. `CostTracker.record_breakdown()`, `record_saving()`, `all_breakdowns()`, `all_savings()`, `savings_summary()` are the API surface.
- **`TokenWasteAnalyzer` is a pure post-execution analyzer** — no live access needed. It operates on snapshot data (breakdowns, events, configs, edges) and produces a `TokenOptimizationReport`.
- **8 waste categories implemented**: unused_context, duplicate, loop_growth, oversized_system, unused_memory_rag, jit_opportunity, memoization_opportunity, reference_opportunity.
- **`OptimizationPlaybook` bridges analytics → Plan 17 rules**: record → mark_applied → evaluate_effectiveness → promote_effective (generates `CausalPrinciple` dicts for `RuleLifecycleManager`). No tight coupling to Plan 17 types.
- **`generate_mutation()` produces `edit_node`/`edit_edge` operations** compatible with existing `graph_mutator` pipeline. Covers JIT, memoize, reference, loop compaction, agent_context_tools.
- **`optimization_rule_approval_mode` in `EngineConfig`** controls whether promoted rules require human approval (`always_approve`) or auto-activate (`auto_accept`).
- **Scheduler snapshots cost tracker analytics** into `RunResult.metadata["__cost_tracker__"]` alongside existing `__run_cache__` data.
- **Patch-up (post-review):** aligned analyzer event contracts with runtime (`iteration_started`, `tool_call_started`, `node_started`), added runtime `input_hash` + loop `context_tokens` emission, and switched mutation payloads to `GraphMutator`-valid schemas (`op`/`updates`/`edge_id`) with endpoint-level `mutation_plan` envelopes.
- **Analytics event emission wired end-to-end:** `TOKEN_BREAKDOWN_RECORDED` (per-LLM-node), `WASTE_DETECTED` (per-finding at run end), `OPTIMIZATION_REPORT_READY` (report at run end), `OPTIMIZATION_APPLIED` (on mutation apply via API). 4 new integration tests.
- **Frontend implementation (Task 3 + Task 4 UI):**
  - Token analytics data is lazily fetched via `Promise.allSettled()` on all three endpoints after `run_completed`/`run_failed` events. Stored in Zustand alongside run state and persisted across tab switches via `TabSnapshot`.
  - Heatmap uses `rgba()` color interpolation on the node background, not border — avoids conflicting with status ring colors. Intensity normalized per-render using `useGraphStore.getState().nodeUsage` to find max across all nodes.
  - Waste badge uses an inline SVG triangle (not an image or library) for zero-dependency consistency. Positioned top-left to avoid overlap with the validation error badge (top-right).
  - Token tooltip appears on hover over the token label badge, not the entire node — prevents tooltip spam during normal interaction.
  - `TokenAnalyticsPanel` is a new bottom-panel tab (not embedded in LogPanel) to keep separation of concerns. Category filter pills allow quick narrowing.
  - "Apply" button per finding calls `applyMutation()` with the pre-generated `mutation_plan` from the backend. On success, the mutation is removed from the list and the graph is reloaded.

## Notes

- **Token heatmap is the single most impactful UX feature here.** A glance at the canvas immediately tells you where the expensive nodes are. This is analogous to flame graphs for CPU profiling.
- **Waste detection requires lightweight static analysis** of prompt templates — matching `{variable}` placeholders against input port names. This is already partially implemented in the prompt rendering pipeline.
- **One-click apply leverages existing mutation infrastructure.** The `graph_mutator.py` already supports field mutations on nodes and edges. Suggestions generate mutation operations that go through the same preview→apply flow as chat-based mutations.
- **Token flow edge labels may be visually noisy** for complex graphs. Default should be off; enable via a "debug" or "analytics" overlay mode.
- **Before/after estimation is inherently approximate** — actual token counts depend on runtime inputs. The estimate assumes similar input sizes to the current run.
- This sub-plan depends on 18-1, 18-2, and 18-3 having landed (or at least having defined their event types), since analytics tracks the savings from those optimizations. However, the core token accounting (task 1) can start independently — it only requires `CostTracker` from 15-3.
- **Memory/RAG token attribution depends on 14-3 and 9-1 event infrastructure.** `RETRIEVAL_COMPLETED` already exists for RAG attribution. For memory attribution, add a dedicated `MEMORY_RECALL` event (or equivalent canonical memory-retrieval event) as 14-3 implementation lands; analytics should consume whichever memory-retrieval event contract is finalized. If memory/RAG are not active, those breakdown fields are simply zero.
- **Unused memory/RAG waste detection (task 2-1e) is inherently heuristic.** Determining whether the LLM "used" injected context requires comparing the response against the injection — exact match is too strict, semantic similarity is approximate. Start with a keyword overlap heuristic (if <5% of injected content's key terms appear in the response, flag as potentially unused) and refine with user feedback.
- **Encode-to-memory recommendations (task 4-2) require cross-node analysis.** The analyzer must compare node A's output size against how much of it downstream nodes actually consume. If node A produces 40K tokens but nodes B, C, D each only use 2–5K, the recommendation to store in memory and retrieve on demand has high confidence. This is a graph-level analysis, not node-local.
- **Evolving context playbooks (task 5) close the optimization loop.** Most optimization frameworks are one-shot: analyze → recommend → user applies. The Plan 17 integration makes it self-improving: analyze → recommend → apply → measure → promote to persistent rule (or discard). This is inspired by Microsoft's ACE (Agentic Context Engineering) which showed +10.6% improvement on agent benchmarks through evolving context strategies.
- **Approval mode is operator-controlled.** Some teams require strict human gating; others prefer autonomous optimization. `optimization_rule_approval_mode` supports both: `always_approve` (human required) and `auto_accept` (activate by default with audit/rollback).
- **No unsafe truncation recommendations.** Consistent with Phase 10 philosophy, analytics never recommends blind head/tail/middle truncation. `sliding_window` / `keep_last` recommendations are only valid when persistent recall guarantees are active.
