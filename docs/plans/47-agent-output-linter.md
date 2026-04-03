# 47: Agent Output Linter

**Status:** completed
**Goal:** A fast, inline, configurable validation layer at every agent-to-agent handoff boundary — like ESLint for agent output — implemented after the Worker plan so it can target a normalized contract surface instead of today's mixed node landscape, while still applying across retained specialized control/runtime primitives where edge handoffs exist.

## The Problem

Orchestration decides *who talks to whom and when*. Contract delivery decides *whether what arrives actually serves the receiver's intent*. Everyone builds orchestration. Nobody has a good answer for contract delivery.

Current state: DAN validates agent output structurally (JSON schema, required fields via `ValidatorNode`, `BoundaryContract`). But structural correctness does not imply semantic correctness. An agent can return a perfectly valid JSON object where the *content* is wrong, irrelevant, or incomplete. A schema says "this field should be a string." It cannot say "this string should contain a coherent summary of Q3 financial results, not a hallucinated paragraph."

## The Linter Analogy

A linter has specific properties that no other validation tool has:

- **Fast.** Does not meaningfully block the workflow.
- **Inline.** Runs *before* the output is handed off, not post-hoc.
- **Configurable.** Rulesets and severity levels, adjustable per connection.
- **Deterministic.** Same input, same result. No randomness in Tier 1–2.
- **Auto-fix.** Does not just flag problems — repairs minor issues and moves on.

The agent output linter applies these properties to agent-to-agent handoffs.

## Three-Tier Architecture

**Tier 1 — Structural lint (deterministic, near-zero cost)**

Schema validation, required fields, types, string lengths, numeric ranges, non-empty arrays. Written once per contract. Runs in microseconds. Catches ~30% of handoff failures.

**Tier 2 — Semantic lint (lightweight model, low cost)**

Embedding similarity against expected topic, keyword/entity presence, tone/sentiment classification, contradiction detection. Uses embedding models or small classifiers, not full LLM calls. Milliseconds, not seconds. Catches ~40% of failures.

**Tier 3 — Intent lint (focused LLM call, moderate cost, only when needed)**

Triggered only when Tier 2 confidence is borderline. A narrowly scoped LLM judgment: "Given that the receiving agent needs [intent], does this output serve that purpose? Yes/No/Partial. If Partial, what's missing?" Not a general "rate this output" call — a binary-ish judgment on a specific intent contract.

**Cost curve:** 90% of handoffs validated cheaply (Tier 1 + 2). Only the ambiguous 10% incur an LLM call.

## Module Separation

The linter is a self-contained module with **zero dependencies** on `dan.worker`, `dan.engine`, or `dan.models`:

```
src/dan/linter/
  __init__.py          # public API: lint(), LintConfig, LintResult, Rule
  engine.py            # core: run rules in tier order, collect results, decide pass/fail/fix
  config.py            # LintConfig, RuleSeverity, TierConfig
  result.py            # LintResult, LintDiagnostic
  rules/
    __init__.py        # Rule protocol (abstract base)
    structural.py      # Tier 1: schema, types, required, range, non-empty
    semantic.py        # Tier 2: embedding similarity, entity presence, topic
    intent.py          # Tier 3: focused LLM judgment against intent statement
  autofix/
    __init__.py        # AutoFix protocol
    strategies.py      # fill_defaults, truncate, retry_with_feedback, refocus
```

The linter is a pure function: `lint(data, config) → LintResult`. It does not know what an engine, scheduler, or Worker is. The Tier 2 embedder and Tier 3 LLM judge are injected as callables, not imported.

Integration with the engine is a thin adapter in `dan.engine` (plan 47-5), not in the linter itself.

## Per-Edge Configuration

Lint config lives on the *edge*, not the node. Different downstream consumers of the same output can have different lint standards. The lint config is the ".lintrc" equivalent for each connection:

```python
edge = DataEdge(
    ...,
    lint={
        "structural": {"schema": {...}, "ranges": {...}},
        "semantic": {"topic_keywords": [...], "min_similarity": 0.7},
        "intent": {"intent": "A concise financial summary suitable for executive briefing"},
        "tier3_threshold": 0.6,
        "severity": "error",
        "autofix": ["fill_defaults", "retry_with_feedback"],
    },
)
```

Legacy `metadata["lint"]` is still accepted and mirrored for compatibility, but the canonical runtime/model surface is now `DataEdge.lint`.

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [47-1](47-1-linter-core-and-rule-protocol.md) | Linter Core & Rule Protocol | `lint()` engine, `LintConfig`, `LintResult`, `Rule` protocol, tiered execution, severity model | P0 | completed |
| [47-2](47-2-structural-rules.md) | Structural Rules | Tier 1: schema validation, type checking, required fields, range bounds, non-empty, format patterns | P0 | completed |
| [47-3](47-3-semantic-rules.md) | Semantic Rules | Tier 2: embedding similarity, entity/keyword presence, topic detection, tone classification | P1 | completed |
| [47-4](47-4-intent-validation-and-autofix.md) | Intent Validation & Auto-Fix | Tier 3: focused LLM judge; auto-fix strategies (fill, truncate, retry-with-feedback, refocus) | P1 | completed |
| [47-5](47-5-engine-integration-and-config-autogen.md) | Engine Integration & Config Auto-Generation | Scheduler hook at `port_data.set()`; topology/compiler generate lint configs from Worker-first contracts | P1 | completed |

## Dependencies / Sequencing

```text
47-1 (Core)
  ↓
47-2 (Structural Rules)  ←── shippable standalone here
  ↓
47-3 (Semantic Rules)
  ↓
47-4 (Intent + Auto-Fix)
  ↓
47-5 (Engine Integration)
```

47-1 + 47-2 are immediately useful even without semantic/intent checking. Pure structural linting catches 30–40% of handoff failures with zero cost.

47-5 is where the linter connects to `dan.engine`. Until then, it is a standalone library callable in tests or custom code.

**Cross-plan sequencing:** architecturally, 47-1 through 47-4 remain dependency-free on `dan.worker`. In practice the 47 series can start once the Worker-first contract surface is stable enough for config generation, even if 46 still has a smaller rollout/compaction tail.

## Implementation Lane

1. Execute plan 47 only in the dedicated follow-on worktree cut after the Worker-first contract surface from plan 46 is stable.
2. Keep the `dan.linter` module itself zero-dependency on `dan.worker`, `dan.engine`, and `dan.models`.
3. Treat Worker metadata from plan 46 as the canonical source for auto-generated lint config. Retained specialized control/runtime primitives and legacy nodes get best-effort fallback rather than driving the design.

## Relationship to Plan 46 (Worker)

Architecturally they stay decoupled, but the implementation order is intentional:

```
plan 46: normalize nodes around Worker
    ↓
stable contract / intent surface
    ↓
plan 47: validate handoffs against that surface
```

`dan.worker` defines what a node IS. `dan.linter` validates what flows BETWEEN nodes. `dan.engine` orchestrates both. The linter module still does not import Worker internals. But from a delivery standpoint, 46 comes first so 47 can consume one normalized authoring/runtime surface instead of twenty-plus node-specific ones.

## Success Criteria

- `lint(data, config)` is a pure function with no engine/worker/model imports
- Tier 1 rules run in < 1ms for typical payloads
- Tier 2 rules run in < 100ms with a local embedding model
- Tier 3 is only triggered when Tier 2 confidence is below threshold
- Auto-fix can repair common issues (missing fields, truncation, topic drift) without human intervention
- The linter works with both legacy node types and the new Worker primitive
- Auto-generated lint configs are Worker-first and only use best-effort fallbacks for legacy nodes
- Per-edge lint configs are expressible in graph JSON and editable in the visual editor
- `src/dan/linter/` has zero imports from `dan.engine`, `dan.worker`, or `dan.models`

## Key Design Principles

1. **Zero dependencies.** The linter is a pure validation library. Backends (embedder, LLM judge) are injected, not imported.
2. **Per-edge, not per-node.** Validation is a property of the connection, not the producer or consumer. Same output, different standards for different consumers.
3. **Tiered cost.** Structural (free) → semantic (cheap) → intent (moderate). Short-circuit on failure. Escalate only when ambiguous.
4. **Fix, don't just flag.** A linter that only reports problems is half a solution. Auto-fix repairs what it can and only escalates what it cannot.
5. **Configurable severity.** Error (block handoff), warning (log and continue), info (metrics only). Per-rule, per-edge.
6. **Self-assembling.** The topology compiler generates lint configs from worker contracts. Manual config is optional, not required.

## Decisions

- The linter module remains architecturally decoupled from `dan.worker`, but the implementation sequence is still 46 → 47. Normalizing contracts first is the point.
- Worker role/persona/description plus port descriptions/schemas are the canonical source for lint-config generation. Legacy node support exists, but it should not drive the design.
- Do not start 47 in parallel with 46. Otherwise the config-generation logic will calcify current node heterogeneity instead of the simpler Worker target state.
- The linter is not “Worker-only middleware.” It should validate any real edge handoff, including those around retained control/runtime primitives, but Worker-first contracts remain the canonical source of rich lint metadata.
- The linter is the tiered contract gate between workers. In runtime terms, the handoff is not published downstream until the lint decision is made.

## Notes

- `ValidatorNode` and `BoundaryContract` stay relevant for explicit visible validation boundaries; the linter generalizes validation to every edge as middleware.
- The linter operates on `port_data` values, not graph topology. Worker-first contracts matter mainly because they make autogen and diagnostics much cleaner.
- The current landed state is substantial: standalone tiers, first-class edge lint, scheduler gating/retry, editor and telemetry surfacing, Worker-first autogen, optional graph-construction-time intent refinement, deterministic autofix, and deterministic plus optional live-provider rollout benchmarks are all live.
- Detailed incremental history now lives in `docs/changelog.md`; this plan now records the completed 47 foundation and leaves future rollout extensions to later follow-up plans instead of appending more history here.
