# 18: Phase 10 — Token Optimization

**Status:** in-progress
**Goal:** Minimize token consumption and maximize cost-efficiency across workflow executions through smart context assembly, provider caching, agent-directed context architecture, and token analytics.
**Filename note:** Some sub-plan files keep legacy slugs for link stability (`18-1-prompt-compression.md`, `18-3-context-window-management.md`); plan titles/sections reflect the updated scope.

## Motivation

Agentic workflows are token-hungry. A multi-department workflow with loops, fan-outs, and orchestrator LLM calls can consume millions of tokens per run. Budget-aware model selection (15-3) picks cheaper models when appropriate, but the largest savings come from *sending fewer tokens in the first place*.

Four levers of optimization, each a sub-plan:

1. **Smart context assembly** — load only what's needed, when it's needed (18-1)
2. **Caching** — avoid redundant LLM calls entirely (18-2)
3. **Agent-directed context architecture** — agents control what's in their context; structured state lives externally (18-3)
4. **Analytics** — visibility into where tokens are spent and where waste occurs (18-4)

## Design Philosophy

**No brute-force truncation or caps.** Blindly cutting tokens (head/tail/middle_out) destroys information and degrades output quality. Instead, this phase relies on three principles:

1. **Agent-controlled context (MemGPT pattern).** LLM nodes get memory/retrieval tools and decide what to load into their context. The agent pulls precisely what it needs rather than having the system dump everything in.
2. **Externalized structured state (Beads pattern).** States, statuses, metadata, intermediate results — anything that can be stored locally as structured data, is. Agents access it through tools (search, read, query) rather than having it bulk-loaded into context. Context carries intent and instructions; data lives on disk.
3. **Mechanical format efficiency.** What *does* enter the context is optimized mechanically: schemas pruned of unused fields, payloads serialized compactly, tool definitions loaded JIT. No LLM intelligence needed — just fewer wasted bytes.

Summarization (LLM-based compression) is acceptable because the agent or system makes an *intelligent* decision about what to compress. Hard token caps that trigger blind cutting are not.

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
| `CompactionRule` | `models/context.py` (used by `models/control_flow.py`) | Shared compaction model already used by `WhileLoopNode` / `ForEachNode` / `AgentTeamNode` | Needs token-aware runtime behavior and richer policy knobs |
| `ContextEdge` | `models/edges.py` + context declarations | Typed context passing via `context_key` + `mode` with declared read/write sets | Always passes full payloads; no reference mode or token-aware transport policy |
| `RetryPolicy` | `models/nodes.py` | Retry on failure with backoff | No caching of successful results |
| Provider implementations | `providers/anthropic_provider.py`, `openai_provider.py`, `google_provider.py` | Standard API calls with usage extraction | No provider-level prompt caching integration yet |
| LogPanel / DanNode | `editor/src/components/` | Per-node token + cost badges and run summary totals already present | No token source decomposition, heatmap, or waste recommendations |
| Run persistence | `server/run_manager.py`, `server/run_store.py` | Persisted run totals + per-node usage (`node_usage`) | No detailed token breakdown model or optimization report |
| Long-chain memory (14-3) | `engine/memory_pipeline.py`, `models/context.py` | `ShortTermMemory` buffer with token-aware eviction, `ConsolidationPipeline`, `MemoryPolicyConfig`, `MemoryItem` + retrieval via RAG vector stores | Already optimizes tokens by consolidating raw history into compressed memory — but operates independently of prompt assembly; no coordination with context selection/deferment, caching, or budget allocation |
| Session memory (14-1) | `engine/context_runtime.py`, `server/chat_store.py` | `MemoryStore` protocol, `session_id`, cross-run KV persistence | Enables cross-run state reuse but not coordinated with memoization cache or token budgets |
| RAG / knowledge retrieval (9-1) | `rag/`, `executors/rag.py` | `EmbeddingProvider`, `VectorStore` (Memory/FAISS/Chroma), `Indexer`, `RAGExecutor`, `RAGOperator` node type | Retrieves relevant chunks instead of passing full documents — inherently token-efficient but not integrated with prompt budget allocation or context selection/deferment |
| `estimate_tokens()` | `utils/tokens.py` | Shared tiktoken/heuristic counter (extracted from `chat_manager.py` in 14-3) | Available for reuse across all token optimization tasks |

## Integration with Memory & RAG Systems

Memory and RAG are the most powerful token optimization mechanisms available: they transform "pass everything inline" into "store once, retrieve what's needed." But currently these systems operate independently of smart context assembly, caching, and budget allocation. This phase must wire them together.

### Key Integration Points

| Integration | Plans involved | What it enables |
|---|---|---|
| **Embedding-based relevance scoring** | 18-1 (selection) ← 9-1 (RAG) | Context selection uses `EmbeddingProvider` for semantic similarity scoring instead of keyword heuristics |
| **Encode-to-memory pattern** | 18-1 (reference passing) ← 14-3 (memory) | Large upstream outputs stored as long-term memory entries; downstream nodes retrieve via semantic search instead of receiving full inline content |
| **Compaction-to-memory pipeline** | 18-3 (loop compaction) → 14-3 (memory) | When compaction consolidates content, persist it to long-term memory via `ConsolidationPipeline` — nothing is permanently lost |
| **Unified prompt budget** | 18-3 (budget allocation) ↔ 14-3 (memory retrieval) ↔ 9-1 (RAG) | Token budget allocation accounts for memory injection tokens and RAG chunk tokens — all sources compete for the same context window |
| **Memory-aware cache coordination** | 18-2 (memoization) ↔ 14-1 (session memory) | Cross-run memoized results stored as session memory entries; session memory changes invalidate dependent caches |
| **Memory/RAG token decomposition** | 18-4 (analytics) ← 14-3 + 9-1 | Token accounting tracks memory-sourced and RAG-sourced tokens separately; waste detection flags unused retrieved context |
| **Summarization sharing** | 18-1 (input summarization), 18-3 (history windowing) ↔ 14-3 (`summarize` compaction) | All three use LLM-based summarization — share the same pipeline (`ConsolidationPipeline`'s LLM summarization callback) to avoid parallel implementations |

### Design Principle

**Memory/RAG are not alternatives to token optimization — they are token optimization.** The role of this phase is to (a) connect the existing memory/RAG systems into the prompt assembly pipeline, (b) provide coordination so that context selection/deferment, caching, compaction, and retrieval work together under a unified budget, and (c) measure the token impact of each system so the analytics dashboard can recommend the best optimization path.

## Sub-Plans

| # | Sub-Plan | Scope | Primary Impact |
|---|----------|-------|----------------|
| [18-1](18-1-prompt-compression.md) | Smart Context Assembly | JIT tool/schema loading, schema/format pruning, reference-based passing, agent context tools, relevance-based selection, summarization | Assemble minimal, high-quality prompts |
| [18-2](18-2-caching-layer.md) | Caching Layer | Provider prompt caching, node result memoization, semantic response cache | Eliminate redundant calls entirely |
| [18-3](18-3-context-window-management.md) | Agent-Directed Context Architecture | Agent-controlled memory paging, externalized structured state, loop compaction, advisory token budgets | Agents load what they need; data lives on disk |
| [18-4](18-4-token-analytics.md) | Token Analytics & Dashboard | Per-node token breakdown, waste detection, optimization recommendations, evolving context playbooks | Visibility, actionable insights, self-improving context |

## Dependencies / Sequencing

1. **18-1 has a hard prerequisite: generic tool-calling loop for `LLMOperator`.**  
   Agent-directed context loading (MemGPT pattern), JIT schema loading, and context tools depend on `LLMOperator` being able to invoke tools in-loop (not just return plain text). This prerequisite lands first.
2. **18-2 can run in parallel with the 18-1 prerequisite.**  
   Provider caching and memoization are largely independent of agent context-tool support.
3. **18-3 builds on 18-1 context tools.**  
   Agent-directed context architecture (externalized state + on-demand loading) depends on `read_state` / `search_context` style tools from 18-1.
4. **18-4 comes last.** Analytics requires the other systems to emit metrics.
5. **15-3 (cost tracking) is prerequisite.** Token analytics (18-4) extends `CostTracker`. Budget allocation (18-3) coordinates with `run_budget`.
6. **14-3 (long-chain memory) is tightly integrated.** Memory/RAG are the highest-leverage token optimization tools — they replace inline content passing with compressed, retrievable storage. This phase wires the existing memory pipeline (`ShortTermMemory`, `ConsolidationPipeline`) and RAG infrastructure (`EmbeddingProvider`, `VectorStore`) into prompt assembly, context selection/deferment, and advisory budgeting systems. See "Integration with Memory & RAG Systems" above.
7. **16-1 (agent teams) coordinates with 18-3.** Agent-team compaction already uses `CompactionRule`; the work here is to make runtime compaction/token accounting semantics consistent across teams, loops, and plain LLM nodes.

## Success Criteria

- Measurable token reduction (target: 30–50% on typical multi-node workflows) without quality degradation
- Provider caching enabled for supported providers (Anthropic `cache_control`, OpenAI auto-cache, Gemini `cached_content`)
- Node result memoization available for deterministic re-runs
- Per-node and per-run token analytics visible in the editor
- Agent-controlled context paging: LLM nodes can pull context on demand via memory/retrieval tools
- Structured state (loop status, intermediate results, metadata) persisted locally and accessed via tools — never bulk-loaded into prompts
- JIT tool/schema loading: tool definitions loaded on demand, not pre-loaded into every prompt
- Memory/RAG integrated into prompt assembly pipeline: embedding-based selection, compaction-to-memory persistence, unified budget across all token sources
- No brute-force truncation anywhere in the pipeline — all context reduction is agent-directed or mechanically lossless
- If `sliding_window` / `keep_last` compaction is used, it is only valid with guaranteed persistent recall (memory/state persistence + retrievability)
- Evolving optimization rules support configurable approval modes: `always_approve` (human gate required) or `auto_accept` (apply by default)

## Decisions

- Wave 2 implementation landed in runtime code: cache subsystem (`NodeResultCache`, `SemanticCache`), smart context assembly wiring in `LLMExecutor`, and advisory token budget plumbing in scheduler/control-flow.
- No hard-cap truncation was introduced; all reductions are deferral/summarization/reference-based with retrieval paths preserved.
- Wave 3 completed 18-4: token breakdown accounting (`TokenBreakdown`, `TokenSaving`), waste analyzer (8 categories), optimization report API, evolving playbook bridge to Plan 17 rules, `OptimizationPlaybook` with effectiveness tracking and principle promotion. Frontend visualization: token heatmap, tooltips, waste badges, enhanced run summary, Optimizations tab with one-click apply. Remaining deferred: token flow edges, before/after estimation, rule dashboard.

## Notes

- The backlog item "Optimize token usage" is promoted to this phase; the backlog item "Manager vs worker node distinction" (token/node budget caps) is related — token budget allocation (18-3 task 4) partially addresses the token cap aspect.
- The backlog item "Lightweight skills (prompt injection)" was superseded when 9B (hyperedges) landed.
- Provider caching (18-2 task 1) is near-zero implementation cost for significant savings — should be prioritized.
- Loop compaction (18-3 task 3) is the single highest-impact optimization for iterative workflows.
