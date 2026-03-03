# 18-1: Prompt Compression & Context Pruning

**Parent:** [18-token-optimization](18-token-optimization.md)
**Status:** not-started
**Goal:** Reduce input token count by compressing prompts, pruning irrelevant context, and passing references instead of full content — without degrading output quality.

## Motivation

Input tokens are the primary cost driver in agentic workflows. A node that receives outputs from 5 upstream nodes, a system prompt, and shared context can easily exceed 50K input tokens — even when only a fraction is relevant to the current task. This sub-plan targets the "send less" lever: every token not sent is a token not paid for.

## Tasks

- [ ] 1. Per-node input token budget
  - [ ] 1-1. Add `max_input_tokens: int | None` field to `LLMOperator` — optional hard cap on total input tokens per call
  - [ ] 1-2. When input exceeds `max_input_tokens`, apply the node's configured pruning strategy (task 2) before prompt assembly
  - [ ] 1-3. If no pruning strategy is set and input exceeds budget, emit a `TOKEN_BUDGET_EXCEEDED` warning event (non-fatal by default, configurable to error)
  - [ ] 1-4. Tokenizer selection: use `tiktoken` for OpenAI models, estimate via `len(text) / 4` heuristic for others (extensible per provider)

- [ ] 2. Context relevance scoring & pruning
  - [ ] 2-1. Create `ContextPruner` in `src/dan/engine/token_optimization.py` — scores each input port's content by relevance to the node's prompt template
  - [ ] 2-2. Scoring heuristics (stackable, configurable weights): (a) keyword overlap between input content and prompt template variables, (b) recency — newer inputs score higher, (c) declared dependency — inputs referenced in the prompt template score highest, (d) content length penalty — very long inputs scored lower per-token
  - [ ] 2-3. Pruning strategies (enum on `LLMOperator`): `none` (error if over limit), `least_relevant` (drop lowest-scored inputs first), `oldest_first` (drop by timestamp), `truncate_largest` (truncate the longest input to fit budget)
  - [ ] 2-4. Emit `CONTEXT_PRUNED` event: node_id, strategy, inputs_before, inputs_after, tokens_saved

- [ ] 3. System prompt deduplication
  - [ ] 3-1. Detect repeated system prompt prefixes across sequential LLM calls in the same execution scope (e.g., loop iterations, team turns)
  - [ ] 3-2. Extract shared prefix into a reusable block that can be marked for provider-level caching (coordinates with 18-2 task 1)
  - [ ] 3-3. Track metrics: unique vs. duplicated system prompt tokens per run

- [ ] 4. Reference-based context passing
  - [ ] 4-1. Add `pass_by_reference: bool` option to `ContextEdge` — when true, pass an artifact reference instead of inline content
  - [ ] 4-2. Receiving node resolves reference on demand (lazy loading) — only fetches full content if the prompt template references the variable
  - [ ] 4-3. For large upstream outputs (>N tokens, configurable threshold), emit a validation suggestion: "Consider reference-based passing for edge X→Y"
  - [ ] 4-4. Integration with artifact store (Layer 4) — references point to versioned artifacts

- [ ] 5. Input summarization gate
  - [ ] 5-1. Optional `summarize_inputs: bool | SummarizationConfig` field on `LLMOperator` — when true, long free-text inputs are summarized before prompt injection
  - [ ] 5-2. `SummarizationConfig`: `model` (defaults to cheapest available), `max_summary_tokens` (budget for the summary itself), `preserve_structured` (keep JSON/dict fields verbatim, only summarize free text)
  - [ ] 5-3. Summarization runs as a lightweight LLM call before the main node execution — cost tracked separately in `CostTracker`
  - [ ] 5-4. Emit `INPUT_SUMMARIZED` event: node_id, input_port, tokens_before, tokens_after, model_used

- [ ] 6. Prompt template analysis
  - [ ] 6-1. Build `PromptAnalyzer` utility — tokenizes prompt templates and identifies: (a) unused variables (declared in inputs but not referenced in template), (b) verbose boilerplate (repeated instruction patterns), (c) estimated token count per template
  - [ ] 6-2. Integration with validation API: `POST /api/validate` returns token optimization suggestions alongside existing warnings
  - [ ] 6-3. Editor integration: show estimated input token count per node in ConfigPanel (pre-execution estimate based on template + typical input sizes)

- [ ] 7. Tests
  - [ ] 7-1. Unit tests: `ContextPruner` scoring and strategy application, `PromptAnalyzer` detection of unused variables and verbose patterns, `SummarizationConfig` validation
  - [ ] 7-2. Integration tests: end-to-end pruning in a multi-node workflow (verify outputs unchanged with pruning enabled), reference-based passing with lazy resolution, summarization gate cost tracking
  - [ ] 7-3. Backward compat: nodes without `max_input_tokens` or pruning config behave identically to current behavior

## Primary Files

- `src/dan/engine/token_optimization.py` *(new)* — `ContextPruner`, `PromptAnalyzer`, `SummarizationConfig`
- `src/dan/models/nodes.py` — `max_input_tokens`, `pruning_strategy`, `summarize_inputs` fields on `LLMOperator`
- `src/dan/models/edges.py` — `pass_by_reference` on `ContextEdge`
- `src/dan/executors/llm.py` — pruning/summarization integration before prompt assembly
- `src/dan/engine/events.py` — `TOKEN_BUDGET_EXCEEDED`, `CONTEXT_PRUNED`, `INPUT_SUMMARIZED` event types
- `src/dan/validation/graph.py` — token optimization suggestions in validation output
- `editor/src/components/ConfigPanel.tsx` — token estimate display

## Decisions

- (filled in during execution)

## Notes

- LLMLingua-2 style learned compression (token dropping via a classifier) is a possible future extension but adds a model dependency. Start with heuristic pruning — simpler, faster, no additional LLM calls.
- Summarization gate adds latency (extra LLM call) and cost (summarization tokens). Should be opt-in and only for genuinely large inputs where the savings outweigh the overhead.
- Reference-based passing requires prompt templates that handle lazy resolution — templates referencing a variable that resolves to a ref need to trigger the fetch. This may require a small change to the prompt rendering pipeline.
- `tiktoken` is the standard for OpenAI token counting. For Anthropic/Google, provider SDKs may offer counting utilities; fall back to the `len/4` heuristic otherwise.
