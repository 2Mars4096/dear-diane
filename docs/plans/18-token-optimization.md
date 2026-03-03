# 18: Phase 10 — Token Optimization

**Status:** not-started
**Goal:** Minimize token consumption and maximize cost-efficiency across workflow executions through prompt compression, provider caching, context window management, and token analytics.

## Motivation

Agentic workflows are token-hungry. A multi-department workflow with loops, fan-outs, and orchestrator LLM calls can consume millions of tokens per run. Budget-aware model selection (15-3) picks cheaper models when appropriate, but the largest savings come from *sending fewer tokens in the first place*.

Four levers of optimization, each a sub-plan:

1. **Prompt compression** — shorten prompts before they hit the LLM (18-1)
2. **Caching** — avoid redundant LLM calls entirely (18-2)
3. **Context window management** — smart truncation and windowing when context is too large (18-3)
4. **Analytics** — visibility into where tokens are spent and where waste occurs (18-4)

## Relationship to 15-3 (Dynamic Model Selection)

15-3 handles *which model* to route each call to — budget-aware switching, cascade fallback, capability matching. This phase handles *what goes into each call* — regardless of which model receives it. They are complementary:

- **15-3** picks a cheaper model when budget runs low → reduces cost per token
- **Phase 10** reduces token count per call → reduces cost per call regardless of model
- **Together:** the full cost optimization stack

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `CostTracker` | `providers/cost_tracker.py` | Per-node/per-run cost accumulation from 15-3 | Tracks cost but not token *composition* (system vs. user vs. context) |
| `estimate_cost()` | `providers/costs.py` | Static cost table + estimation | No optimization suggestions, no waste detection |
| `CompactionRule` | `models/control_flow.py` | Team conversation compaction in `AgentTeamNode` | Scoped to teams only; not generalized to all loops/nodes |
| `ContextEdge` | `models/edges.py` | Typed context passing with `context_type` enum | Always passes full content; no reference mode, no truncation |
| `RetryPolicy` | `models/nodes.py` | Retry on failure with backoff | No caching of successful results |
| Provider implementations | `providers/anthropic.py`, etc. | Standard API calls | No cache control headers, no prompt caching hints |
| LogPanel / DanNode | `editor/src/components/` | Duration badges, per-node token counts in logs | No cost badges, no token heatmap, no optimization UI |
| Long-chain memory (14-3) | `models/context.py` | Encoding/consolidation/retrieval pipeline | Handles information recall, not token optimization |

## Sub-Plans

| # | Sub-Plan | Scope | Primary Impact |
|---|----------|-------|----------------|
| [18-1](18-1-prompt-compression.md) | Prompt Compression & Context Pruning | Automatic prompt shortening, context relevance scoring, reference-based passing, per-node input budgets | Reduce input tokens per call |
| [18-2](18-2-caching-layer.md) | Caching Layer | Provider prompt caching, node result memoization, semantic response cache | Eliminate redundant calls entirely |
| [18-3](18-3-context-window-management.md) | Context Window Management | Truncation strategies, conversation windowing, loop compaction, token budget allocation | Prevent context overflow, distribute budget |
| [18-4](18-4-token-analytics.md) | Token Analytics & Dashboard | Per-node token breakdown, waste detection, optimization recommendations, editor visualization | Visibility and actionable insights |

## Dependencies / Sequencing

1. **18-1 and 18-2 are independent.** Can be built in parallel.
2. **18-3 builds on 18-1.** Context window management uses the pruning primitives from 18-1.
3. **18-4 comes last.** Analytics requires the other systems to emit metrics.
4. **15-3 (cost tracking) is prerequisite.** Token analytics (18-4) extends `CostTracker`. Budget allocation (18-3) coordinates with `run_budget`.
5. **14-3 (long-chain memory) is related but independent.** Memory system handles information recall; token optimization handles cost reduction. They may share compaction primitives.
6. **16-1 (agent teams) coordinates with 18-3.** Team conversation compaction should use the generalized windowing system instead of its own `CompactionRule`.

## Success Criteria

- Measurable token reduction (target: 30–50% on typical multi-node workflows) without quality degradation
- Provider caching enabled for supported providers (Anthropic `cache_control`, OpenAI auto-cache, Gemini `cached_content`)
- Node result memoization available for deterministic re-runs
- Per-node and per-run token analytics visible in the editor
- Truncation policies configurable per node with sensible defaults
- Token budget allocation distributable across workflow nodes

## Decisions

- (filled in during execution)

## Notes

- The backlog item "Optimize token usage" is promoted to this phase; the backlog item "Manager vs worker node distinction" (token/node budget caps) is related — token budget allocation (18-3 task 4) partially addresses the token cap aspect.
- The backlog item "Lightweight skills (prompt injection)" was superseded when 9B (hyperedges) landed.
- Provider caching (18-2 task 1) is near-zero implementation cost for significant savings — should be prioritized.
- Loop compaction (18-3 task 3) is the single highest-impact optimization for iterative workflows.
