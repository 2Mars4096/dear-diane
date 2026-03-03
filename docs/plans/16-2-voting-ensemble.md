# 16-2: Voting / Ensemble

**Parent:** [16-execution-primitives](16-execution-primitives.md)
**Status:** completed
**Goal:** Implement a `VoteNode` that runs the same task through multiple model instances (same-model voting or cross-model ensemble), collects outputs, and selects the best answer via configurable strategies — providing a one-step quality primitive that trades cost for reliability.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| `ParallelSubagentsNode` | `models/control_flow.py` | Runs multiple sub-graphs concurrently; `merge_strategy` for fan-in | Generic fan-out; no voting semantics; requires manual sub-graph per model; no quality comparison |
| `ParallelSubagentsExecutor` | `executors/control_flow.py` | `asyncio.gather()` on branches; `_merge()` with APPEND/LAST_WRITE_WINS/REDUCER | No voting strategies; reducer is expression-based, not LLM-based |
| `ForEachNode` | `models/control_flow.py` | Fans out over list items; `parallelism` + `merge_strategy` | Designed for different inputs per branch, not same input to multiple models |
| `ReduceNode` | `models/control_flow.py` | Expression-based aggregation | No voting/consensus logic; no quality ranking |
| `MergeStrategy` | `models/context.py` | APPEND, LAST_WRITE_WINS, REDUCER | No MAJORITY, WEIGHTED, BEST_OF_N |
| `LLMOperator.model` | `models/nodes.py` | Static model string per node | One model per node; no multi-model invocation |
| `ProviderRegistry` | `providers/registry.py` | `resolve(model) -> LLMProvider` | Routes to provider; available for multi-model dispatch |
| `CompletionResult` | `providers/__init__.py` | `text`, `usage` (prompt_tokens, completion_tokens) | Token usage available for cost-aware voting |
| `COST_PER_1K_TOKENS` | `providers/costs.py` | Cost table + `estimate_cost()` | Available for cost tracking per vote candidate |
| `evaluate_expression()` | `engine/conditions.py` | Safe expression evaluator | Could evaluate custom voting expressions |
| Builder DSL | `builder/builder.py` | `wf.llm()`, `wf.tool()`, `wf.composite()`, etc. | No `wf.vote()` sugar |
| Markdown format | `loader/compiler.py` | Agent + workflow markdown files | No vote/ensemble section or syntax |
| Node type union | `models/graph.py` | 16 node types in `Node` discriminated union | No `VoteNode` |

## Tasks

- [x] 1. Define VoteNode model
  - [x] 1-1. Create `VoteNode` in `models/control_flow.py` with `node_type: Literal["vote"]`. Fields: `candidates` (list[str] — model names to vote across; if single entry, same model runs N times), `num_votes` (int, default 3 — how many times to run the task), `prompt_template` (str — the prompt to evaluate, with `{input_port}` placeholders), `system_prompt` (str, default ""), `temperature` (float, default 0.7 — used for all candidates; higher = more diverse votes), `output_json_schema` (dict | None — if set, all votes must conform; enables structured comparison).
  - [x] 1-2. Add `vote_strategy` field (Literal: `"majority"`, `"weighted"`, `"best_of_n"`, `"judge"`, `"unanimous"`) — how to select the winner from collected votes.
  - [x] 1-3. Define strategy-specific config: `VoteConfig` model with `judge_model` (str | None — model for `"judge"` strategy), `judge_prompt` (str | None — prompt for judge), `quality_metric` (str | None — expression evaluated on each vote output for `"weighted"` or `"best_of_n"`), `unanimity_threshold` (float, default 1.0 — for `"unanimous"`, fraction of agreement required).
  - [x] 1-4. Add `vote_config: VoteConfig | None` field to `VoteNode`.
  - [x] 1-5. Add `parallelism` (int, default num_votes — max concurrent LLM calls) and `timeout_seconds` (float | None) fields.
  - [x] 1-6. Add `VoteNode` to `Node` discriminated union in `models/graph.py`. Export from `models/control_flow.py`.

- [x] 2. Build VoteExecutor
  - [x] 2-1. Create `VoteExecutor` in `executors/control_flow.py`. Core flow: render prompt from inputs → fan out to N LLM calls (respecting `parallelism` via semaphore) → collect `CompletionResult`s → apply vote strategy → return winning answer + vote metadata.
  - [x] 2-2. Implement LLM fan-out: for each vote slot, pick model from `candidates` (round-robin if multiple candidates, same model if single). Call `provider_registry.resolve(model).complete(messages, model, temperature)`. Collect results as `VoteCandidate` (model, text, usage, cost, index).
  - [x] 2-3. Handle partial failures: if some votes fail (rate limit, timeout), proceed with available results if at least `ceil(num_votes/2)` succeeded. Emit warning events for failed votes.
  - [x] 2-4. Implement voting strategies:
    - `majority`: hash-compare outputs (or normalize whitespace and compare); pick the most common answer. Tie-break: first occurrence.
    - `weighted`: evaluate `quality_metric` expression on each output, pick highest score. Expression receives `{"output": text, "model": model_name, "cost": cost}`.
    - `best_of_n`: run judge model on all candidates. Judge prompt receives all outputs and returns the index of the best one. Default judge prompt: "Given these N answers to the same question, which is best? Return only the number."
    - `judge`: same as `best_of_n` but uses a dedicated `judge_model` (can be different from candidates).
    - `unanimous`: check if all outputs agree (within `unanimity_threshold`). If yes, return the agreed answer. If no, return the majority answer with `consensus_reached=false`.
  - [x] 2-5. Build `VoteResult` output: `winner` (str — the selected answer), `winner_model` (str), `winner_index` (int), `all_votes` (list of `{model, text, score}` — all candidates with scores), `consensus_reached` (bool), `vote_count` (int — successful votes), `total_cost` (float), `strategy_used` (str).
  - [x] 2-6. Emit events: `VOTE_STARTED` (num_votes, candidates, strategy), `VOTE_CAST` (index, model, token_count), `VOTE_COMPLETED` (winner_model, consensus, total_cost).

- [x] 3. Structured output voting
  - [x] 3-1. When `output_json_schema` is set, use output normalization (existing `LLMExecutor` pattern: parse JSON → validate → re-prompt on failure) for each vote. Enables field-level comparison.
  - [x] 3-2. Implement field-level majority: for structured outputs, compare per-field and build a consensus object where each field is the majority value. Useful when different models agree on most fields but disagree on one.
  - [x] 3-3. Add `consensus_mode` field to `VoteConfig`: `"whole"` (compare entire output) or `"field"` (per-field comparison for structured outputs). Default `"whole"`.

- [ ] 4. Extend builder API
  - [ ] 4-1. Add `wf.vote(name, prompt, candidates, num_votes=3, strategy="majority", **kwargs) -> NodeRef` to `WorkflowBuilder`. Shorthand that creates a `VoteNode` with common defaults.
  - [ ] 4-2. Add `wf.ensemble(name, prompt, models, strategy="judge", **kwargs) -> NodeRef` as an alias for `wf.vote()` with `candidates=models` and `num_votes=len(models)`.
  - [ ] 4-3. Extend builder compiler to generate `VoteNode` from vote/ensemble declarations.
  - [ ] 4-4. Extend builder decompiler to emit `wf.vote()` / `wf.ensemble()` calls.

- [ ] 5. Extend markdown loader
  - [ ] 5-1. Support `type: vote` in agent markdown frontmatter. Frontmatter fields map to `VoteNode` fields: `candidates`, `num_votes`, `vote_strategy`, `judge_model`, `quality_metric`.
  - [ ] 5-2. Example vote agent:
    ```
    ---
    type: vote
    name: Quality Check
    candidates: [claude-sonnet-4-6, gpt-4o, gemini-2.5-pro]
    vote_strategy: judge
    judge_model: claude-opus-4
    ---
    Evaluate this analysis and provide your assessment: {input}
    ```
  - [ ] 5-3. Extend `loader/compiler.py` to compile vote agents to `VoteNode`.
  - [ ] 5-4. Extend `loader/decompiler.py` to decompile `VoteNode` to vote agent `.md`.

- [ ] 6. Extend visual editor
  - [ ] 6-1. Add `vote` to `NODE_TYPE_CATALOG` in `graph.ts` with description and port info.
  - [ ] 6-2. Add node icon for `vote` in `nodeIcons.tsx`.
  - [ ] 6-3. Add `VoteNode` config panel fields: candidate model list (add/remove/reorder), num_votes spinner, strategy dropdown, judge model (conditional on strategy), quality metric expression textarea, output JSON schema editor.
  - [ ] 6-4. Show vote results in `OutputPreview`: tabular view of all candidates (model, output preview, score, cost), winner highlighted, consensus indicator.
  - [ ] 6-5. Show cost breakdown on `DanNode` badge: total vote cost.
  - [ ] 6-6. Palette entry under "Control Flow" or "Quality" category.

- [ ] 7. Validation
  - [ ] 7-1. Extend `validate_graph()`: `VoteNode.candidates` must be non-empty. `num_votes` must be >= 1. `judge_model` required when `vote_strategy` is `"judge"` or `"best_of_n"`. `quality_metric` required when `vote_strategy` is `"weighted"`. `output_json_schema` required when `consensus_mode` is `"field"`.
  - [ ] 7-2. Warn if `num_votes` < 3 (weak consensus) or candidates list has only 1 entry with `strategy="majority"` (same-model majority needs high temperature for diversity).

- [x] 8. Tests and documentation (executor + model tests)
  - [x] 8-1. Unit tests: `VoteNode`/`VoteConfig` serialization, voting strategy implementations (majority with ties, weighted scoring, unanimous threshold), partial failure handling.
  - [x] 8-2. Integration tests: 3-model ensemble with mock providers (verify winner selection), same-model 3-vote majority (verify consensus detection), judge strategy with mock judge LLM.
  - [ ] 8-3. Builder round-trip tests: `wf.vote()` / `wf.ensemble()` → build → decompile → compare.
  - [ ] 8-4. Markdown round-trip tests: vote agent `.md` → compile → decompile → compare.
  - [x] 8-5. Cost tracking tests: verify `total_cost` accumulation from `CompletionResult.usage` across all vote candidates.
  - [ ] 8-6. Update `docs/architecture.md`: add `VoteNode` to control-flow primitives table, document voting strategies.
  - [ ] 8-7. Update `docs/llm-api-guide.md`: vote node reference, `wf.vote()`/`wf.ensemble()` builder API, markdown syntax.
  - [x] 8-8. Update `docs/changelog.md`, `docs/todo.md`, and this plan as implementation progresses.

## Primary Files

- `src/dan/models/control_flow.py` — `VoteNode`, `VoteConfig`
- `src/dan/models/graph.py` — add `VoteNode` to `Node` union
- `src/dan/executors/control_flow.py` — `VoteExecutor`
- `src/dan/executors/__init__.py` — register `VoteExecutor`
- `src/dan/engine/events.py` — `VOTE_STARTED`, `VOTE_CAST`, `VOTE_COMPLETED`
- `src/dan/builder/builder.py` — `wf.vote()`, `wf.ensemble()`
- `src/dan/builder/compiler.py` — compile vote declarations
- `src/dan/builder/decompiler.py` — decompile `VoteNode`
- `src/dan/loader/parser.py` — parse `type: vote` frontmatter
- `src/dan/loader/compiler.py` — compile vote agent to `VoteNode`
- `src/dan/loader/decompiler.py` — decompile `VoteNode` to markdown
- `editor/src/types/graph.ts` — `VoteNode` TypeScript type
- `editor/src/lib/nodeIcons.tsx` — vote icon
- `editor/src/components/ConfigPanel.tsx` — vote config fields
- `editor/src/components/OutputPreview.tsx` — vote results view
- `tests/test_models/` — vote model serialization
- `tests/test_engine/` — vote execution integration tests
- `tests/test_builder/` — vote builder round-trip
- `tests/test_loader/` — vote markdown round-trip

## Decisions

- **LLM-level primitive, not graph-level ensemble.** `VoteNode` is specifically designed for zero-shot (or few-shot) LLM ensemble voting, meaning it directly wraps `provider.complete()`. It does *not* execute full agent sub-graphs. If users need to vote across multi-step agent trajectories (e.g., agents that use tools), they should use `ParallelSubagentsNode` + `ReduceNode` instead. This keeps `VoteNode` fast and focused on text generation quality.
- **New node type, not composed from existing.** While voting *could* be built from ForEach+Reduce, that requires the user to manually wire fan-out, parameterize model selection, and write a reduce expression. A dedicated `VoteNode` makes the intent explicit and provides optimized strategies.
- **Same-model and cross-model in one node.** `candidates` list with `num_votes` handles both: single-entry `candidates` + `num_votes=5` = same model voting; multi-entry `candidates` + `num_votes=len(candidates)` = cross-model ensemble. Unified interface.
- **Judge is an LLM call, not a separate node.** The judge call is internal to `VoteExecutor` — it's part of the voting strategy, not a visible graph node. For complex judge logic, users can wire a separate LLM node downstream.
- **Temperature matters.** Same-model voting only produces diverse outputs at temperature > 0. The node's `temperature` field defaults to 0.7; builder/editor should warn if temperature is 0 with same-model voting (all votes will be identical).
- **Cost tracking is per-vote.** Each LLM call's `CompletionResult.usage` is recorded. Total vote cost = sum of all candidate costs + judge cost (if applicable). Coordinates with 15-3 `CostTracker` if available.
- **Partial failure is tolerable.** Unlike most nodes where failure is binary, votes can succeed with partial results. The executor proceeds if at least half the votes completed.

## Notes

- Self-consistency (Wang et al., 2022) showed that majority voting over chain-of-thought reasoning significantly improves accuracy. `VoteNode` with `strategy="majority"` and `output_json_schema` implements this directly.
- Cross-model ensemble is especially effective when candidate models have different failure modes (e.g., GPT-4o for breadth, Claude for precision, Gemini for long context).
- 15-3 (dynamic model selection) `CapabilityPolicy` could feed the `candidates` list — select models by capability then vote across them. Coordination point for later integration.
- The `"weighted"` strategy enables custom quality signals — users can define what "good" means for their domain via the `quality_metric` expression (e.g., `len(output) > 100 and 'references' in output`).
