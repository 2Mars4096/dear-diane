# 47: Agent Output Linter

**Status:** not-started
**Goal:** A fast, inline, configurable validation layer at every agent-to-agent handoff boundary — like ESLint for agent output.

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
    metadata={
        "lint": {
            "structural": {"schema": {...}, "ranges": {...}},
            "semantic": {"topic_keywords": [...], "min_similarity": 0.7},
            "intent": "A concise financial summary suitable for executive briefing",
            "tier3_threshold": 0.6,
            "severity": "error",
            "autofix": ["fill_defaults", "retry_with_feedback"],
        }
    }
)
```

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [47-1](47-1-linter-core-and-rule-protocol.md) | Linter Core & Rule Protocol | `lint()` engine, `LintConfig`, `LintResult`, `Rule` protocol, tiered execution, severity model | P0 | not-started |
| [47-2](47-2-structural-rules.md) | Structural Rules | Tier 1: schema validation, type checking, required fields, range bounds, non-empty, format patterns | P0 | not-started |
| [47-3](47-3-semantic-rules.md) | Semantic Rules | Tier 2: embedding similarity, entity/keyword presence, topic detection, tone classification | P1 | not-started |
| [47-4](47-4-intent-validation-and-autofix.md) | Intent Validation & Auto-Fix | Tier 3: focused LLM judge; auto-fix strategies (fill, truncate, retry-with-feedback, refocus) | P1 | not-started |
| [47-5](47-5-engine-integration-and-config-autogen.md) | Engine Integration & Config Auto-Generation | Scheduler hook at `port_data.set()`; topology compiler generates lint configs from contracts | P1 | not-started |

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

**Cross-plan dependency:** 47-5 integrates with the engine scheduler and optionally with the topology compiler from plan 46. Neither 47-1 through 47-4 nor plan 46 depend on each other.

## Relationship to Plan 46 (Worker)

These two plans are peers, not parent-child:

```
dan.worker (46)  ←──→  dan.engine  ←──→  dan.linter (47)
     ↑                      ↑                    ↑
  defines nodes      runs the graph      validates handoffs
```

`dan.worker` defines what a node IS. `dan.linter` validates what flows BETWEEN nodes. `dan.engine` orchestrates both. No direct dependency between worker and linter.

The only intersection is the topology compiler: when it generates a squad of Workers (46), it also generates lint configs for the edges between them (47-5). That is the compiler's responsibility, not a dependency between the modules.

## Success Criteria

- `lint(data, config)` is a pure function with no engine/worker/model imports
- Tier 1 rules run in < 1ms for typical payloads
- Tier 2 rules run in < 100ms with a local embedding model
- Tier 3 is only triggered when Tier 2 confidence is below threshold
- Auto-fix can repair common issues (missing fields, truncation, topic drift) without human intervention
- The linter works with both legacy node types and the new Worker primitive
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

- (to be filled during execution)

## Notes

- The existing `ValidatorNode` and `BoundaryContract` cover structural validation at explicit graph boundaries. The linter generalizes this to *every* edge, adds semantic/intent tiers, and makes it invisible middleware rather than a visible graph node.
- The existing LLM executor's output normalization loop (parse/validate/re-prompt) is a form of inline auto-fix. The linter's auto-fix extends this pattern to the handoff boundary, not just the LLM output boundary.
- The linter operates on `port_data` values — the actual data flowing through the graph. It does not need to understand the graph topology, just the data at each edge.
