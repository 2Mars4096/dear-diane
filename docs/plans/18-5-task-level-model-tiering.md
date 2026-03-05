# 18-5: Task-Level Model Tiering

**Parent:** [18-token-optimization](18-token-optimization.md)
**Status:** in-progress (core + docs + editor UI complete; de-escalation telemetry deferred)
**Goal:** Automatically assign cost-appropriate models to each LLM call based on task difficulty, output impact, and downstream recoverability — reducing cost without degrading quality.

## Motivation

All five existing model-selection strategies (static, budget, cascade, capability, router) require **explicit per-node configuration**. In practice, most nodes use the engine default model — even when a cheaper model would suffice. A multi-department workflow with 30+ LLM nodes might route simple routing decisions and format-conversion calls through the same expensive model used for final creative synthesis. Task-level tiering fills this gap: a default policy that assigns the right-weight model to each call automatically, with zero per-node annotation required.

This is the **fifth lever** of token/cost optimization — complementary to the four existing levers (context assembly, caching, agent-directed context, analytics). While 18-1 through 18-4 reduce *tokens per call*, this plan reduces *cost per token* by routing simpler calls to cheaper models.

## Relationship to 15-3 (Dynamic Model Selection)

15-3 built the policy/selector/tracker infrastructure. This plan adds a new strategy (`TierPolicy`) that plugs into that infrastructure. `ModelSelector.select()` gains a `"tier"` branch (same pattern as existing strategies); `CostTracker` and `ProviderRegistry` are unchanged.

## Design

### Three Scoring Dimensions

Each LLM call is scored on three orthogonal dimensions. The scores combine into a single tier.

| Dimension | Question | Signals | Range |
|-----------|----------|---------|-------|
| **Difficulty** | How hard is the reasoning? | Node type, prompt complexity, tool count, output schema complexity | 0.0–1.0 |
| **Impact** | What breaks if the output is wrong? | Downstream fan-out, terminal position, total downstream token spend estimate | 0.0–1.0 |
| **Recoverability** | Can errors be caught/fixed? | Has validator, output normalizer retries, loop iteration, cascade policy | 0.0–1.0 |

### Four Model Tiers

Scores map to four provider-agnostic tiers. Each tier resolves to a concrete model via a configurable model map.

| Tier | Label | Score range | Default model class | Typical use |
|------|-------|-------------|---------------------|-------------|
| **L0** | `micro` | score < 0.25 | Haiku / GPT-4o-mini / Gemini Flash | Routing, classification, extraction, yes/no, format conversion |
| **L1** | `routine` | 0.25 ≤ score < 0.50 | Sonnet / GPT-4o | General generation, summarization, moderate tool use |
| **L2** | `reasoning` | 0.50 ≤ score < 0.75 | Opus / o3 / Gemini Pro | Multi-step planning, complex code, synthesis |
| **L3** | `critical` | score ≥ 0.75 | Opus / o3 (high temp) / extended thinking | Final user-facing output, hard diagnosis, novel research |

### Tier Score Formula

```
tier_score = w_d * difficulty + w_i * impact + w_r * (1 - recoverability)
```

Default weights: `w_d = 0.45, w_i = 0.35, w_r = 0.20`. Configurable per engine or per workflow.

Rationale: difficulty is the strongest signal (hard tasks need strong models); impact is second (wrong answers on high-impact nodes waste more downstream tokens than they save); recoverability discounts the other two (if errors are caught, we can afford a weaker model).

### Tier Model Map

A configurable mapping from tier label to concrete model string. Ships with sensible defaults; users override per-provider or per-workflow.

```python
DEFAULT_TIER_MAPS = {
    "anthropic": {
        "micro":     "claude-3-5-haiku-20241022",
        "routine":   "claude-sonnet-4-6",
        "reasoning": "claude-opus-4",
        "critical":  "claude-opus-4",  # same model, but TierPolicy.critical_params can set extended_thinking=True or higher max_tokens
    },
    "openai": {
        "micro":     "gpt-4o-mini",
        "routine":   "gpt-4o",
        "reasoning": "o3-mini",
        "critical":  "o3",
    },
    "google": {
        "micro":     "gemini-2.0-flash",
        "routine":   "gemini-2.0-flash",  # Google's flash is strong enough for routine
        "reasoning": "gemini-2.5-pro",
        "critical":  "gemini-2.5-pro",
    },
}
```

When L2 and L3 share the same model name (e.g. Anthropic Opus), `TierPolicy` can carry per-tier parameter overrides (`critical_params: dict`) — e.g. `{"extended_thinking": true, "max_tokens": 8192}` — so the same model is called with more compute budget at L3. The tier map selects the model string; parameter overrides distinguish tiers that share a model.

## Existing Baseline

| Component | Location | What exists | Gap |
|-----------|----------|-------------|-----|
| `ModelPolicy` union | `providers/model_policy.py` | 5 strategies (static/budget/cascade/capability/router) | No tier-based strategy |
| `ModelSelector` | `providers/model_selector.py` | `select()` dispatches on `policy.strategy` | No `"tier"` branch |
| `ModelCapabilityRegistry` | `providers/capabilities.py` | Static capabilities + latency tiers for ~9 models | Has cost/latency data usable for tier map population |
| `CostTracker` | `providers/cost_tracker.py` | Per-node cost + budget tracking | Records cost but doesn't feed back to tier decisions |
| Node models | `models/nodes.py`, `models/control_flow.py` | `node_type`, `model_policy`, `retry_policy`, output schema | Scoring signals available but not extracted |
| Graph structure | `models/graph.py`, `models/edges.py` | Edges, fan-out, downstream topology | Impact scoring requires graph walk |
| Token analytics | `engine/token_optimization.py` | `TokenWasteAnalyzer`, `PromptAnalyzer` | Prompt complexity signals available |

## Tasks

- [x] 1. Define `TierPolicy` and tier models
  - [x] 1-1. Add `TierPolicy` to `providers/model_policy.py`: `strategy: "tier"`, `tier_map: dict[str, str] | None` (override defaults), `tier_params: dict[str, dict[str, Any]] | None` (per-tier LLM parameter overrides, e.g. `{"critical": {"extended_thinking": true}}`), `weights: TierWeights | None`, `constraints: ModelConstraints | None`.
  - [x] 1-2. Add `TierWeights` model: `difficulty: float = 0.45`, `impact: float = 0.35`, `recoverability: float = 0.20`. Validate sum ≈ 1.0.
  - [x] 1-3. Add `TaskTier` enum: `micro`, `routine`, `reasoning`, `critical` with score thresholds.
  - [x] 1-4. Extend `ModelPolicy` union to include `TierPolicy`. Update `_POLICY_ADAPTER` in `model_selector.py`.
  - [x] 1-5. Define `DEFAULT_TIER_MAPS` — one per provider ecosystem (anthropic, openai, google) in `providers/tier_defaults.py`. Populate from `COST_PER_1K_TOKENS` and `LATENCY_TIER`. Include per-tier default `tier_params` where tiers share a model (e.g. Anthropic L2/L3 both use Opus but L3 gets `extended_thinking: true`).

- [x] 2. Build tier scorer (`providers/tier_scorer.py`) *(tasks 2-1 through 2-4)*
  - [x] 2-1. `DifficultyScorer` — scores 0.0–1.0 from node-type defaults + prompt/schema signals. Final score clamped to `[0.0, 1.0]`.
    - Node-type base scores: `router` → 0.15, `llm_operator` → 0.40, `orchestrator` → 0.55, `reflection` → 0.70, `vote` (judge) → 0.45, `agent_team` (moderator) → 0.30.
    - Adjustments: output schema property count (+0.05 per 3 properties, capped at +0.15), tool count (+0.05 per 3 tools, capped at +0.15), prompt token estimate (>2000 → +0.10, >5000 → +0.20).
  - [x] 2-2. `ImpactScorer` — scores 0.0–1.0 from graph topology. Precompute per-node impact scores once at run start (graph is static during execution); cache in `TierScorer` so per-call scoring is O(1). Final score clamped to `[0.0, 1.0]`.
    - Terminal node (no downstream LLM consumers) → 0.80 base.
    - Feeds `HumanNode` (downstream path reaches a HumanNode) → 0.85 base (user-facing output).
    - Fan-out degree: each downstream branch adds +0.05 (capped at 0.30).
    - Downstream token budget estimate: proportional to total downstream node count × avg cost.
    - Inside nested loop (depth ≥ 1) → −0.15 (per-iteration impact is lower; loop will retry).
    - Internal-only (consumed by single next node, not terminal) → 0.20 base.
  - [x] 2-3. `RecoverabilityScorer` — scores 0.0–1.0 from node/graph metadata. Final score clamped to `[0.0, 1.0]`.
    - Has `retry_policy` with `max_retries > 0` → +0.20.
    - Has output schema (normalizer will validate/re-prompt) → +0.25.
    - Inside a while-loop (iterative correction) → +0.25.
    - Has downstream `ValidatorNode` within 2 hops → +0.20.
    - Has `cascade` policy on fallback → +0.10.
  - [x] 2-4. `TierScorer` — combines the three sub-scores using `TierWeights`, maps to `TaskTier` via thresholds.
  - [x] 2-5. Add `task_tier: TaskTier | None` optional field to all LLM-using node types: `LLMOperator` (`models/nodes.py`), `OrchestratorNode`, `RouterNode`, `AgentTeamNode` (`models/control_flow.py`), `ReflectionNode` (`models/nodes.py`), `VoteNode` (`models/control_flow.py`). When set, bypasses scoring entirely — scorer returns the declared tier directly.

- [x] 3. Integrate into `ModelSelector`
  - [x] 3-1. Add `"tier"` branch to `ModelSelector.select()`: instantiate `TierScorer`, compute tier, resolve model from tier map.
  - [x] 3-2. Thread graph reference into `ExecutionContext`. Currently `_make_context()` in `scheduler.py` receives `graph` but does not store it on the context — only uses it for the `run_subgraph` closure. Add `graph: Graph | None = None` field to `ExecutionContext` and populate it in `_make_context()`. `TierScorer` reads `context.graph` to precompute impact scores. This is a one-line addition to `ExecutionContext.__init__` and `_make_context`.
  - [x] 3-3. Emit `model_selected` event with `tier`, `tier_score`, `difficulty`, `impact`, `recoverability` breakdown for observability.
  - [x] 3-4. Support `TierPolicy` as `EngineConfig.default_model_policy` — the primary intended use. Nodes with explicit `model` or `model_policy` still override.

- [ ] 4. Adaptive escalation and de-escalation *(4-1, 4-3, 4-4 done; 4-2 deferred)*
  - [x] 4-1. **Escalation on failure**: when output normalizer retries exhaust, record `tier_escalation` event and bump tier by one level for this call (re-select model, retry). Cap at L3.
  - [ ] 4-2. **De-escalation over runs**: persist per-node tier success stats in `CostTracker` (or new `TierTelemetry` sidecar). After N successful runs at tier X, suggest de-escalation to X-1 via token analytics recommendations (18-4 playbook entry). *(deferred — requires multi-run telemetry infrastructure)*
  - [x] 4-3. **Floor enforcement**: `task_tier` explicit override acts as a floor — scoring always runs; result tier is clamped to be >= the declared tier.
  - [x] 4-4. **Escalation cap**: max 1 escalation per call to bound latency. If L3 also fails, fall through to normal `retry_policy` / `on_failure` handling.

- [x] 5. Default tier map population and provider detection
  - [x] 5-1. On `Engine` init, detect which providers are configured (from `EngineConfig` keys). Select the appropriate `DEFAULT_TIER_MAP` variant. If only one provider is configured, use that provider's tier map exclusively (all 4 tiers resolve to models from that provider). If multiple providers are configured, prefer the provider with the widest tier spread (most distinct models across tiers); allow cross-provider mixing via explicit `tier_map` override.
  - [x] 5-2. Allow `EngineConfig.tier_map` override — user supplies partial map, merged over defaults.
  - [x] 5-3. Validate that every tier in the map resolves to a model the `ProviderRegistry` can handle. Warn on unresolvable tiers at startup.

- [x] 6. Tests *(54 tests, all passing; 6-5 deferred with 4-2)*
  - [x] 6-1. Unit tests for each scorer: `DifficultyScorer`, `ImpactScorer`, `RecoverabilityScorer` with known node/graph fixtures.
  - [x] 6-2. Unit tests for `TierScorer` end-to-end: verify score → tier mapping across boundary cases.
  - [x] 6-3. Integration test: build a multi-node graph, run with `TierPolicy` as default, verify different nodes receive different tier scores.
  - [x] 6-4. Escalation test: `_escalate_tier()` helper: micro→routine, routine→reasoning, reasoning→critical, critical→critical.
  - [ ] 6-5. De-escalation test: simulate repeated success, verify recommendation appears in token analytics. *(deferred — depends on 4-2)*
  - [x] 6-6. Backward compatibility: graphs without `TierPolicy` behave identically to current behavior.

- [x] 7. Documentation and editor integration
  - [x] 7-1. Update `docs/llm-api-guide.md` with `TierPolicy` usage and `task_tier` override.
  - [x] 7-2. Add tier badge to `DanNode.tsx` — show assigned tier (L0/L1/L2/L3) alongside model name during/after runs.
  - [x] 7-3. Add tier breakdown to token analytics panel — per-node tier assignment, score decomposition, cost comparison vs. uniform model.
  - [x] 7-4. Update `docs/architecture.md` model heterogeneity section.

## Scoring Signal Reference

### Difficulty signals (available at scheduling time)

| Signal | Source | Weight |
|--------|--------|--------|
| Node type | `node.node_type` | Base score (see 2-1) |
| Output schema complexity | `node.output_ports[*].json_schema` | Property count, nesting depth |
| Tool count | `len(tool_registry.tools)` for tool-using nodes | More tools → harder routing |
| Prompt length | `estimate_tokens(node.prompt_template or node.system_prompt)` | Longer prompt → more complex task |
| Hyperedge count | Number of attached skills/rules | More constraints → harder to satisfy |

### Impact signals (require graph topology)

| Signal | Source | Weight |
|--------|--------|--------|
| Terminal position | No downstream data edges to LLM nodes | High impact |
| Fan-out degree | Count of downstream branches | Multiplied impact |
| Feeds HumanNode | Downstream path includes HumanNode | User-facing → high impact |
| Loop depth | Inside 0/1/2+ nested loops | Deeper → lower per-iteration impact |

### Recoverability signals (available at scheduling time)

| Signal | Source | Weight |
|--------|--------|--------|
| Retry policy | `node.retry_policy.max_retries > 0` | Can retry on failure |
| Output schema | `node.output_ports` with `json_schema` | Normalizer validates + re-prompts |
| Loop context | Node inside while-gate body | Iterative correction opportunity |
| Downstream validator | `ValidatorNode` reachable within 2 hops | Explicit quality gate |
| Cascade fallback | `node.model_policy` is CascadePolicy | Built-in fallback chain |

## Dependencies

- **15-3 (model policy infrastructure):** provides `ModelPolicy`, `ModelSelector`, `CostTracker`, `ProviderRegistry` — all prerequisites, already complete.
- **18-4 (token analytics):** tier breakdown and de-escalation recommendations feed into the analytics dashboard and evolving playbooks.
- **Graph topology access (task 3-2):** `ImpactScorer` needs to walk edges from the graph. The scheduler already has the graph in `_make_context()` but doesn't store it on `ExecutionContext`. Requires a one-line addition: `context.graph = graph`. Impact scores are precomputed once at run start and cached, so the graph walk is not repeated per-call.
- **`ExecutionContext` field addition:** New `graph: Graph | None` field. Minimal change to `executor.py` and `scheduler.py`. No behavioral change to existing code paths.

## Success Criteria

- Multi-node workflows show ≥ 2 distinct model tiers in use with zero per-node configuration.
- Cost reduction of 20–40% on typical workflows vs. uniform strong-model baseline, with <5% quality degradation (measured by output normalizer success rate).
- Tier assignments are observable in run events and token analytics.
- Adaptive escalation recovers from cheap-model failures without user intervention.
- Explicit `task_tier` override works as a floor for author control.
- All existing tests pass unchanged (backward compatible).

## Decisions

- `task_tier` field on node models uses `Literal["micro", "routine", "reasoning", "critical"] | None` for Pydantic v2 validation (post-review fix; originally `str | None`).
- `ExecutionContext.graph` field uses `Any` type, matching the existing pattern for loosely-typed context fields (`model_selector`, `cost_tracker`, etc.).
- Escalation is injected in `LLMExecutor` after the normalization retry loop exhausts — one additional attempt with the next-higher tier before falling through to the normal failure path.
- De-escalation (task 4-2) deferred: requires persistent per-node tier telemetry across runs, which is a cross-run storage concern better addressed alongside 18-4 playbook infrastructure.
- Provider detection uses `EngineConfig.providers` dict keys and `model_provider_map` prefix patterns; `llm_api_key` no longer implies OpenAI (post-review fix).
- `ModelSelector.select()` returns `(model, TierResult, tier_params)` tuple for tier strategy (post-review fix: avoids concurrency hazard from shared `_last_tier_result`).
- `TierScorer` cached per graph identity on `ModelSelector` to avoid O(N) `ImpactScorer._precompute()` per LLM call (post-review fix).
- Explicit `task_tier` uses floor-enforcement: scoring runs normally (populating real difficulty/impact/recoverability values for observability), but the final tier is the higher of the scored tier and the declared tier. This preserves observability data while honoring the author's intent.

## Notes

- The "Manager vs. worker node distinction" backlog item is partially addressed by this plan: worker-type nodes (simple LLM operators doing extraction/formatting) naturally land in L0/L1, while manager-type nodes (orchestrators, planners) land in L2/L3.
- The scoring formula and weights are initial defaults. The de-escalation telemetry (task 4-2) provides data to calibrate weights over time.
- Router nodes are *not* always cheap: if a routing error triggers an expensive downstream branch, the `ImpactScorer` will push the tier up. This avoids the naive assumption that "routing = simple."
