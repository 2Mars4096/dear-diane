# 18-4: Token Analytics & Dashboard

**Parent:** [18-token-optimization](18-token-optimization.md)
**Status:** not-started
**Goal:** Provide visibility into token consumption patterns and actionable optimization recommendations through per-node breakdowns, waste detection, and an editor-integrated analytics dashboard.

## Motivation

You can't optimize what you can't see. Current visibility is limited to per-node token counts in the LogPanel and aggregate cost in `CostTracker`. There's no breakdown of *where* tokens come from (system prompt? context edges? loop accumulation?), no detection of waste (unused context, redundant passes), and no actionable suggestions. This sub-plan makes token consumption a first-class observable.

## Tasks

- [ ] 1. Token accounting enrichment
  - [ ] 1-1. Extend `CostTracker` to record per-node token composition: `system_tokens`, `user_tokens`, `assistant_tokens` (input side), `output_tokens` (output side). Requires parsing the `messages` array before LLM call to count tokens per role.
  - [ ] 1-2. Track per-node context composition: tokens from each input port (by port name), tokens from system prompt, tokens from context edges, tokens from hyperedge injections. Store as `TokenBreakdown` model in `CostTracker`.
  - [ ] 1-3. Record optimization actions applied: tokens pruned (18-1), tokens from cache hits (18-2), tokens truncated (18-3). Each recorded as a `TokenSaving` entry: `{action, tokens_saved, cost_saved}`.
  - [ ] 1-4. Expose via API: `GET /api/runs/{run_id}/token-breakdown` — returns `{nodes: {node_id: TokenBreakdown}[], run_totals: RunTokenSummary}`.

- [ ] 2. Waste detection
  - [ ] 2-1. Create `TokenWasteAnalyzer` in `src/dan/engine/token_optimization.py`. Post-execution analysis that identifies:
    - **(a) Unused context:** input port data passed to a node but not referenced in the rendered prompt template (requires comparing port names against template variables)
    - **(b) Duplicate information:** same content passed through multiple input ports or context edges to the same node
    - **(c) Loop accumulation:** context growth across loop iterations without compaction (iteration N context >> iteration 1 context, flag if growth > 2x)
    - **(d) Oversized system prompts:** system prompt consuming >30% of total input token budget for a node
  - [ ] 2-2. Generate per-run `TokenOptimizationReport`: list of `WasteFinding` items, each with `category`, `node_id`, `description`, `estimated_saveable_tokens`, `suggestion` (human-readable action)
  - [ ] 2-3. Expose via API: `GET /api/runs/{run_id}/optimization-report` — returns the `TokenOptimizationReport`

- [ ] 3. Editor visualization
  - [ ] 3-1. **Token heatmap on canvas:** Color-code nodes by token consumption intensity. Gradient from green (low) → yellow (medium) → red (high). Normalized relative to the run's max per-node consumption. Toggle on/off via toolbar button.
  - [ ] 3-2. **Per-node token tooltip:** Hover over a completed node to see a breakdown popup: input tokens (system/user/context), output tokens, cost, cache status (hit/miss), optimization actions applied.
  - [ ] 3-3. **Run-level token summary:** New section in LogPanel (or RunOutputBlock): total input tokens, total output tokens, total cost, cache hit rate, tokens saved by optimizations, waste percentage detected.
  - [ ] 3-4. **Token flow edges:** Optional edge label showing token count — visualizes how much information flows along each edge. Toggle via toolbar or settings.
  - [ ] 3-5. **Waste indicators:** Nodes with waste findings show a small warning badge (yellow triangle). Click to see the findings and suggestions.

- [ ] 4. Optimization recommendations UI
  - [ ] 4-1. After run completion, a "Token Optimization" tab appears in the run output area. Lists all waste findings grouped by category, sorted by estimated savings (descending).
  - [ ] 4-2. Each suggestion is specific and actionable:
    - "Enable `memoize` on node X — it ran 5 times with identical inputs (est. saving: 25K tokens)"
    - "Add `truncation_policy: tail` to node Y — it received 80K input tokens but only used the last 10K"
    - "Set `loop_compaction: sliding_window(3)` on loop Z — context grew 8x across iterations"
    - "Switch edge A→B to `pass_by_reference` — 50K tokens of context were passed but only 2K referenced"
  - [ ] 4-3. **One-click apply:** Each suggestion has an "Apply" button that generates the corresponding graph mutation (add field to node, change edge config) using the existing mutation infrastructure (`graph_mutator.py`). Mutation is previewed before applying.
  - [ ] 4-4. **Before/after estimation:** Show estimated token count for next run if suggestion is applied, alongside current run's actual count.

- [ ] 5. Tests
  - [ ] 5-1. `TokenBreakdown` accounting: verify system/user/assistant token counts match expected for a known message array
  - [ ] 5-2. `TokenWasteAnalyzer`: test each waste category detection against synthetic run data (unused context, duplicates, loop growth, oversized system prompt)
  - [ ] 5-3. API endpoint tests: `/token-breakdown`, `/optimization-report` return correct shapes
  - [ ] 5-4. One-click apply: suggestion mutation generates valid graph operations, preview matches expected diff

## Primary Files

- `src/dan/providers/cost_tracker.py` — `TokenBreakdown`, `TokenSaving` models; extended accounting
- `src/dan/engine/token_optimization.py` — `TokenWasteAnalyzer`, `TokenOptimizationReport`, `WasteFinding`
- `src/dan/server/app.py` — `/api/runs/{run_id}/token-breakdown`, `/api/runs/{run_id}/optimization-report` endpoints
- `editor/src/components/TokenAnalytics.tsx` *(new)* — optimization report panel, suggestion list, one-click apply
- `editor/src/components/DanNode.tsx` — token heatmap coloring, waste indicator badge
- `editor/src/components/LogPanel.tsx` (or `RunOutputBlock.tsx`) — run-level token summary section
- `editor/src/components/EditorToolbar.tsx` — heatmap toggle, token flow edge toggle
- `editor/src/lib/api.ts` — `fetchTokenBreakdown()`, `fetchOptimizationReport()` API calls
- `editor/src/types/graph.ts` — `TokenBreakdown`, `WasteFinding` TypeScript types

## Decisions

- (filled in during execution)

## Notes

- **Token heatmap is the single most impactful UX feature here.** A glance at the canvas immediately tells you where the expensive nodes are. This is analogous to flame graphs for CPU profiling.
- **Waste detection requires lightweight static analysis** of prompt templates — matching `{variable}` placeholders against input port names. This is already partially implemented in the prompt rendering pipeline.
- **One-click apply leverages existing mutation infrastructure.** The `graph_mutator.py` already supports field mutations on nodes and edges. Suggestions generate mutation operations that go through the same preview→apply flow as chat-based mutations.
- **Token flow edge labels may be visually noisy** for complex graphs. Default should be off; enable via a "debug" or "analytics" overlay mode.
- **Before/after estimation is inherently approximate** — actual token counts depend on runtime inputs. The estimate assumes similar input sizes to the current run.
- This sub-plan depends on 18-1, 18-2, and 18-3 having landed (or at least having defined their event types), since analytics tracks the savings from those optimizations. However, the core token accounting (task 1) can start independently — it only requires `CostTracker` from 15-3.
