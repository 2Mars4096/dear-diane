# 18-1: Smart Context Assembly

**Parent:** [18-token-optimization](18-token-optimization.md)
**Status:** in-progress
**Goal:** Assemble minimal, high-quality prompts by loading only what's needed (JIT), formatting it efficiently (schema pruning), and giving agents tools to pull additional context on demand — without brute-force truncation or hard caps.
**Filename note:** File slug is retained for link stability (`18-1-prompt-compression.md`); scope is Smart Context Assembly.

## Motivation

Input tokens are the primary cost driver in agentic workflows. A node that receives outputs from 5 upstream nodes, a system prompt, tool schemas, and shared context can easily exceed 50K input tokens — even when only a fraction is relevant to the current task. The solution is not to cut tokens after assembly, but to assemble smarter: load less upfront, format it compactly, and let the agent pull more if it needs it.

## Tasks

- [x] 0. Prerequisite: generic tool-calling loop support in `LLMOperator`
  - [x] 0-1. Extend `LLMExecutor` to support tool-aware completion loops for `LLMOperator` (model emits tool calls -> executor runs tools -> tool results appended -> model continues), not just plain text completion.
  - [x] 0-2. Align provider interfaces with tool-calling needs: update `LLMProvider` protocol and provider implementations to accept tool schemas and structured message payloads (`list[dict[str, Any]]` where needed), while preserving backward compatibility for text-only calls.
  - [x] 0-3. Reuse existing runtime tool-call events (`TOOL_CALL_STARTED`, `TOOL_CALL_RESULT`) in generic LLM execution path so analytics and observability stay consistent.
  - [x] 0-4. Backward compatibility: nodes without tools behave identically to current behavior; tool loop activates only when tools are configured/enabled.
  - [x] 0-5. This prerequisite must land before task 7 (JIT schema loading), task 9 (agent context tools), and 18-3 agent-directed context architecture.

- [x] 1. Advisory token budget (no hard caps)
  - [x] 1-1. Add `target_input_tokens: int | None` field to `LLMOperator` — advisory budget that guides context assembly decisions, **not** a hard cap that triggers truncation
  - [x] 1-2. When estimated input would exceed `target_input_tokens`, the system prefers: (a) JIT loading — defer non-essential context to tool-accessible storage, (b) reference-based passing — pass refs instead of inline, (c) summarization — compress free-text inputs. The agent is never silently cut off.
  - [x] 1-3. If input still exceeds the advisory budget after smart assembly, emit a `TOKEN_BUDGET_ADVISORY` warning event (informational, never blocks execution). The agent proceeds with full context — the budget is guidance, not enforcement.
  - [x] 1-4. Reuse existing `dan.utils.tokens.estimate_tokens()` (extracted in 14-3) for token counting. Extend with provider-specific overrides where available (Anthropic/Google SDKs); the current tiktoken + `len/4` fallback covers most cases

- [x] 2. Relevance-based context selection (not pruning)
  - [x] 2-1. Create `ContextSelector` in `src/dan/engine/token_optimization.py` — scores each input port's content by relevance to the node's prompt template and decides what to **include inline** vs. **defer to tool access**
  - [x] 2-2. Scoring heuristics (stackable, configurable weights): (a) keyword overlap between input content and prompt template variables, (b) recency — newer inputs score higher, (c) declared dependency — inputs referenced in the prompt template score highest, (d) content length penalty — very long inputs scored lower per-token, (e) **embedding similarity via RAG infrastructure** — when `EmbeddingProvider` from 9-1 is available, embed the prompt template and each input via the same pipeline used for `RAGExecutor`, score by cosine similarity. More accurate than keyword overlap for semantic relevance, but adds latency (embedding call); use keyword overlap as the zero-latency default, embedding as opt-in upgrade
  - [x] 2-3. **Selection, not deletion:** Low-scoring inputs are not dropped — they are deferred to tool-accessible storage (artifact store or memory). The agent receives a manifest listing deferred items with summaries, and can retrieve any of them on demand via context tools (task 9). Nothing is permanently removed from the agent's reach.
  - [x] 2-4. Emit `CONTEXT_DEFERRED` event: node_id, inputs_inline, inputs_deferred (list of port names + summary), tokens_inline, tokens_deferred

- [ ] 3. System prompt deduplication *(deferred — partially complete; prefix extraction (3-2) remains)*
  - [x] 3-1. Detect repeated system prompt prefixes across sequential LLM calls in the same execution scope (e.g., loop iterations, team turns)
  - [ ] 3-2. Extract shared prefix into a reusable block that can be marked for provider-level caching (coordinates with 18-2 task 1) *(deferred — depends on 18-2 provider caching coordination; low priority after cache hints landed)*
  - [x] 3-3. Track metrics: unique vs. duplicated system prompt tokens per run

- [ ] 4. Reference-based context passing (memory-integrated) *(deferred — partially complete; core ref-passing landed, two-tier resolution and encode-to-memory remain)*
  - [x] 4-1. Add `pass_by_reference: bool` option to `ContextEdge` — when true, pass an artifact reference instead of inline content
  - [x] 4-2. Receiving node resolves reference on demand (lazy loading) — only fetches full content if the prompt template references the variable
  - [ ] 4-3. For large upstream outputs (>N tokens, configurable threshold), emit a validation suggestion: "Consider reference-based passing for edge X→Y" *(deferred — validation integration; Phase 10 stretch)*
  - [x] 4-4. **Two-tier reference resolution:** (a) artifact store (Layer 4) for run-scoped versioned artifacts — implemented: `create_reference`/`resolve_reference` in `context_runtime.py`, scheduler stores large outputs (>`pass_by_reference_threshold_tokens`, default 2000) in ArtifactStore and passes ref dict; `_resolve_input_references` resolves before execution. (b) long-term memory via `VectorStore` (14-3) for cross-run semantic retrieval — deferred; memory mirroring remains policy-gated.
  - [ ] 4-5. **Encode-to-memory pattern:** For nodes producing very large outputs (>N tokens), optionally encode the output into 14-3's `ShortTermMemory` buffer as a `MemoryItem` with `source_node_id` and `entry_type=artifact_summary`. Downstream nodes can then use `MemoryQuery(intent=task_recall)` to retrieve a summary instead of receiving the full output. This transforms "push all" into "push summary + pull on demand" — the highest-leverage token optimization for multi-hop workflows *(deferred — requires memory pipeline integration and cross-node coordination; Phase 10 stretch)*

- [x] 5. Input summarization gate (with memory pipeline integration)
  - [x] 5-1. Optional `summarize_inputs: bool | SummarizationConfig` field on `LLMOperator` — when true, long free-text inputs are summarized before prompt injection
  - [x] 5-2. `SummarizationConfig`: `model` (defaults to cheapest available), `max_summary_tokens` (budget for the summary itself), `preserve_structured` (keep JSON/dict fields verbatim, only summarize free text), `persist_to_memory: bool` (default False — when True, summaries are written to `ShortTermMemory` for future retrieval)
  - [x] 5-3. Summarization runs as a lightweight LLM call before the main node execution — cost tracked separately in `CostTracker`. **Share the LLM summarization path with 14-3's `ConsolidationPipeline.summarize()` callback** to avoid parallel implementations. Both use the same pattern: batch text → LLM call → compressed representation.
  - [x] 5-4. When `persist_to_memory=True`, the generated summary is stored as a `MemoryItem(entry_type=distilled_fact)` in 14-3's `ShortTermMemory`. This enables downstream nodes to recall the summary via `MemoryQuery` even if the original edge data was deferred or reference-passed.
  - [x] 5-5. Emit `INPUT_SUMMARIZED` event: node_id, input_port, tokens_before, tokens_after, model_used, persisted_to_memory

- [ ] 6. Prompt template analysis *(deferred — partially complete; PromptAnalyzer utility landed, validation/editor integration remain)*
  - [x] 6-1. Build `PromptAnalyzer` utility — tokenizes prompt templates and identifies: (a) unused variables (declared in inputs but not referenced in template), (b) verbose boilerplate (repeated instruction patterns), (c) estimated token count per template
  - [ ] 6-2. Integration with validation API: extend `POST /api/graphs/{graph_id}/validate` diagnostics to return token optimization suggestions alongside existing warnings *(deferred — validation API integration; Phase 10 stretch)*
  - [ ] 6-3. Editor integration: show estimated input token count per node in ConfigPanel (pre-execution estimate based on template + typical input sizes) *(deferred — frontend visualization; Phase 10 stretch)*

- [x] 7. JIT tool & schema loading
  - [ ] 7-1. **Problem:** Tool definitions (function schemas, parameter descriptions) and hyperedge metadata can consume 20–40% of context window when loaded statically. In MCP-heavy workflows, this grows further. *(deferred — problem statement only; solution implemented in 7-2..7-4)*
  - [x] 7-2. **Discovery-based loading:** Instead of injecting all tool schemas into every LLM call, inject only a lightweight tool catalog (name + one-line description, ~10 tokens per tool). The agent requests full schemas for specific tools when it decides to use them.
  - [x] 7-3. Implement `ToolSchemaResolver` in `src/dan/engine/token_optimization.py`: maintains a registry of full tool schemas, serves them on demand via a built-in `get_tool_schema(tool_name)` tool call. The LLM executor injects this resolver as an implicit tool when JIT loading is enabled.
  - [x] 7-4. Add `jit_tool_loading: bool` field to `LLMOperator` (default False). When enabled, prompt assembly uses the catalog-only strategy. For nodes with ≤3 tools, JIT adds overhead — default to inline loading for small tool sets.
  - [x] 7-5. **Hyperedge JIT:** Apply the same pattern to hyperedge definitions — inject a summary of active hyperedges, not full rule bodies. Full rules loaded on demand via `load_hyperedge(name)` tool.
  - [x] 7-6. Emit `JIT_SCHEMA_LOADED` event: node_id, tool_name, tokens_saved (vs. static loading)

- [ ] 8. Schema & format pruning *(deferred — partially complete; PayloadPruner and format compaction landed, auto field relevance remains)*
  - [ ] 8-1. **Problem:** Structural characters (braces, quotes, keys) account for ~40% of JSON token spend. Metadata fields (`created_at`, `internal_id`, `updated_by`) are often irrelevant to the LLM's task. *(deferred — problem statement only; solution implemented in 8-2..8-3)*
  - [x] 8-2. Create `PayloadPruner` in `src/dan/engine/token_optimization.py`: mechanically strips specified fields from structured data before prompt injection. Configurable per-node via `prune_fields: list[str]` on `LLMOperator` (glob patterns supported, e.g. `"*.internal_id"`, `"*.created_at"`).
  - [x] 8-3. **Format compaction:** Optional compact serialization for structured inputs — e.g., YAML instead of JSON (fewer structural tokens), or a minimal key-value format. Configurable via `input_format: Literal["json", "yaml", "compact"]` on `LLMOperator`. Default: `json` (no change). `compact` uses indentation-based format inspired by TOON (Token-Oriented Object Notation).
  - [ ] 8-4. **Automatic field relevance:** `PromptAnalyzer` (task 6) can detect which JSON fields are actually referenced in the prompt template. Fields never referenced across multiple runs are candidates for pruning — surfaced as suggestions in 18-4 analytics. *(deferred — requires multi-run field usage tracking; depends on 18-4 analytics integration)*
  - [x] 8-5. Emit `PAYLOAD_PRUNED` event: node_id, fields_removed, tokens_saved

- [x] 9. Agent context tools (MemGPT pattern)
  - [x] 9-1. **Core idea:** Give LLM nodes explicit tools to pull context on demand, so the prompt starts lean and the agent loads what it needs — rather than the system guessing what to include.
  - [x] 9-2. Define built-in context tools (injected as implicit tools when agent-directed context is enabled):
    - `search_context(query: str, scope: str) -> list[ContextItem]` — semantic search across deferred inputs, memory, and artifacts. Uses `EmbeddingProvider` + `VectorStore` from 9-1.
    - `read_context(ref: str) -> str` — fetch a specific deferred input, artifact, or memory entry by reference ID.
    - `read_state(key: str) -> Any` — read a specific structured state entry from local storage (loop counter, intermediate result, node status, etc.).
    - `list_available_context() -> list[ContextManifest]` — list all deferred/available context items with summaries and token estimates.
  - [x] 9-3. Add `agent_context_tools: bool` field to `LLMOperator` (default False). When enabled, the executor injects context tools and defers low-relevance inputs (per task 2) instead of inlining them.
  - [x] 9-4. **Interaction with tool calling:** Context tools are first-class tool calls — they go through the same tool-call loop as user-defined tools. The agent can call `search_context` mid-reasoning to pull in more information, just like it calls web_search or file_read.
  - [x] 9-5. **Graceful degradation:** If context tools are not enabled, all inputs are assembled inline (current behavior). No existing workflow breaks.
  - [x] 9-6. Emit `CONTEXT_TOOL_CALLED` event: node_id, tool_name, ref_or_query, tokens_loaded

- [x] 10. Tests
  - [x] 10-1. Unit tests: `ContextSelector` scoring and selection decisions, `PromptAnalyzer` detection of unused variables and verbose patterns, `SummarizationConfig` validation, `PayloadPruner` field stripping and format compaction
  - [x] 10-2. Integration tests: end-to-end context selection in a multi-node workflow (verify deferred inputs are accessible via context tools), reference-based passing with lazy resolution, summarization gate cost tracking, JIT tool loading with schema resolution
  - [x] 10-3. Backward compat: nodes without `target_input_tokens`, context tools, or JIT loading behave identically to current behavior

## Primary Files

- `src/dan/executors/llm.py` — **prerequisite tool-calling loop** for `LLMOperator`; smart context assembly: selection, JIT loading, schema pruning, context tool injection
- `src/dan/providers/__init__.py` — `LLMProvider` protocol updates for tool schemas + structured messages
- `src/dan/providers/openai_provider.py` — tool-aware provider call path compatibility
- `src/dan/providers/anthropic_provider.py` — tool-aware provider call path compatibility
- `src/dan/providers/google_provider.py` — tool-aware provider call path compatibility
- `src/dan/engine/token_optimization.py` *(new)* — `ContextSelector`, `PromptAnalyzer`, `SummarizationConfig`, `PayloadPruner`, `ToolSchemaResolver`
- `src/dan/models/nodes.py` — `target_input_tokens`, `summarize_inputs`, `jit_tool_loading`, `agent_context_tools`, `prune_fields`, `input_format` fields on `LLMOperator`
- `src/dan/models/edges.py` — `pass_by_reference` on `ContextEdge`
- `src/dan/engine/events.py` — `TOKEN_BUDGET_ADVISORY`, `CONTEXT_DEFERRED`, `INPUT_SUMMARIZED`, `JIT_SCHEMA_LOADED`, `PAYLOAD_PRUNED`, `CONTEXT_TOOL_CALLED` event types
- `src/dan/engine/memory_pipeline.py` — reuse `ShortTermMemory` for encode-to-memory; share `ConsolidationPipeline` summarization callback
- `src/dan/rag/__init__.py` — reuse `EmbeddingProvider` for embedding-based relevance scoring and context tool search
- `src/dan/utils/tokens.py` — reuse existing `estimate_tokens()` (no new token counting utility needed)
- `src/dan/validation/graph.py` — token optimization suggestions in validation output
- `editor/src/components/ConfigPanel.tsx` — token estimate display, JIT/context tool toggles

## Decisions

- **Task 0:** Tool-calling loop uses `complete()` (not streaming) when tools are configured, since streaming doesn't support tool_calls in the response. Streaming is preserved for non-tool calls.
- **Task 0:** Tool resolution uses `context.tool_registry` (the existing `ToolRegistry` from `dan.executors.tool`). Tool not found returns error JSON to the model rather than raising — lets the model recover gracefully.
- **Task 0:** Provider message type widened from `list[dict[str, str]]` to `list[dict[str, Any]]` across Protocol and all implementations — this is required for tool result messages which have `tool_call_id` and `tool_calls` fields.
- **Task 0 hardening:** Scheduler now injects the active `ToolExecutor.registry` into `ExecutionContext.tool_registry`, so `LLMExecutor` tool-calling works in normal engine runs (not only in tests that monkeypatch context). Fallback-model retries preserve tool schemas, and provider stream handling tolerates awaitable/coroutine-style `stream()` implementations.
- Wave 2 runtime wiring is centralized in `LLMExecutor`: input assembly now applies context deferral, payload pruning/format compaction, optional summarization, JIT schema resolution, and built-in context tools inside the same tool-calling loop.
- Reference values (`__ref__/uri`) are lazily resolved: only fetched when prompt variables actually reference them; otherwise summaries remain inline and retrievable through context tools.

## Notes

- **Task 0 is mandatory.** JIT schema loading and agent context tools require generic tool-calling loop support in `LLMOperator`. Without that runtime capability, tasks 7 and 9 are design-only and cannot execute.
- **No brute-force truncation.** This plan does not cut tokens. `ContextSelector` *defers* low-relevance inputs to tool-accessible storage; `PayloadPruner` mechanically strips unused fields; JIT loading avoids loading schemas at all. The agent always has access to everything — it just doesn't carry everything in context simultaneously.
- **Agent context tools are the paradigm shift.** Inspired by MemGPT/Letta: the agent controls its own context window by pulling what it needs via tool calls. This is more robust than any system-level heuristic because the agent understands its own task.
- **JIT tool loading is highest-ROI for tool-heavy workflows.** MCP-heavy agents or workflows with many tools can save 20–40% of context window. For simple 1–3 tool nodes, inline loading is fine — JIT overhead (extra tool call to fetch schema) isn't worth it.
- **Schema pruning is mechanical and safe.** Unlike LLM-based compression, field stripping and format compaction are deterministic — no information is lost from the LLM's perspective if the pruned fields aren't referenced in the prompt template.
- LLMLingua-2 style learned compression is a possible future extension but adds a model dependency. Start with the above approaches.
- Summarization gate adds latency (extra LLM call) and cost (summarization tokens). Should be opt-in and only for genuinely large inputs where the savings outweigh the overhead.
- Reference-based passing requires prompt templates that handle lazy resolution — templates referencing a variable that resolves to a ref need to trigger the fetch. This may require a small change to the prompt rendering pipeline.
- `estimate_tokens()` already exists in `dan.utils.tokens` (extracted in 14-3, task 5-2). No new token counting utility is needed.
- **Memory integration rationale:** The encode-to-memory pattern (task 4-5) and summarization persistence (task 5-4) bridge context assembly and long-chain memory. Content deferred or summarized is never permanently lost — it's recallable via memory retrieval or context tools.
- **Dual-write guardrails:** `pass_by_reference` should not blindly write every payload into both artifact and memory stores. Default memory mirroring policy should be `auto` (threshold + reuse heuristics), with explicit opt-in for strict durability requirements.
- **Embedding-based scoring (task 2-2e) latency budget:** A single embedding call typically costs 5–20ms and <100 tokens. For most nodes this is negligible compared to the LLM call it precedes. Batch-embed all inputs in a single call (the `EmbeddingProvider.embed()` protocol already supports batching).
