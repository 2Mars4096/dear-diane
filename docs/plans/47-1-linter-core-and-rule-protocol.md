# 47-1: Linter Core & Rule Protocol

**Parent:** [47-agent-output-linter](47-agent-output-linter.md)
**Status:** completed
**Goal:** Define the core `lint()` engine, `LintConfig` / `LintResult` models, `Rule` protocol, tiered execution order, and severity model.

## Tasks

- [x] 1. Create `src/dan/linter/` package
  - [x] 1-1. Create `src/dan/linter/__init__.py` with public API exports
  - [x] 1-2. Verify zero imports from `dan.engine`, `dan.worker`, `dan.models`
- [x] 2. Define `Rule` protocol in `src/dan/linter/rules/__init__.py`
  - [x] 2-1. `Rule` abstract base: `check(data, config) → RuleResult`
  - [x] 2-2. Formalize shared rule metadata via `RuleSpec` and the `LintDiagnostic` / `LintResult` contract instead of a separate `RuleResult` class
  - [x] 2-3. `Severity` enum: `error`, `warning`, `info`
  - [x] 2-4. `Tier` enum: `STRUCTURAL`, `SEMANTIC`, `INTENT` — each rule declares its tier
- [x] 3. Define `LintConfig` in `src/dan/linter/config.py`
  - [x] 3-1. `StructuralConfig`: `json_schema`, `required_keys`, `ranges`, `non_empty_keys`, `format_patterns`
  - [x] 3-2. `SemanticConfig`: `topic_keywords`, `required_entities`, `min_similarity`, `reference_text`
  - [x] 3-3. `IntentConfig`: `intent` plus Tier 3 guardrails
  - [x] 3-4. `AutoFixConfig`: modeled inline on `LintConfig` via `autofix`, `max_retries`, `retry_budget_ms`
  - [x] 3-5. `LintConfig` container: structural + semantic + intent + autofix + default severity + enabled flag
- [x] 4. Define `LintResult` in `src/dan/linter/result.py`
  - [x] 4-1. `LintDiagnostic`: individual rule result with rule name, tier, severity, message, field path
  - [x] 4-2. `LintResult`: `passed`, `auto_fixed`, `fixed_data`, `diagnostics`, `tier_reached`, `elapsed_ms`
- [x] 5. Implement `lint()` engine in `src/dan/linter/engine.py`
  - [x] 5-1. Accept `data`, `config: LintConfig`, optional embedder/judge via injected runtime
  - [x] 5-2. Run Tier 1 rules first; if any error-severity rule fails, short-circuit (skip Tier 2/3)
  - [x] 5-3. Run Tier 2 rules if semantic config is present and embedder is provided; collect confidence scores
  - [x] 5-4. Run Tier 3 only if Tier 2 confidence is below `tier3_threshold` and an intent judge is provided
  - [x] 5-5. After all rules: deterministic fixes are applied inline; model-backed fixes are surfaced as planned feedback/suggestions
  - [x] 5-6. Return `LintResult` with all diagnostics, final pass/fail, and timing
- [x] 6. Add `src/dan/linter/rules/__init__.py` with tier/rule helpers shared by `structural.py`, `semantic.py`, and `intent.py`
- [x] 7. Add `src/dan/linter/autofix/__init__.py` with `AutoFix` protocol and strategy entry points
- [x] 8. Tests
  - [x] 8-1. `tests/test_linter/test_engine.py` — tiered execution order, short-circuit behavior, timing
  - [x] 8-2. `tests/test_linter/test_config.py` — config parsing, defaults, validation
  - [x] 8-3. `tests/test_linter/test_result.py` — result construction, diagnostics aggregation
  - [x] 8-4. Verify the linter module has zero imports from engine/worker/models (import check test)

## Core API Shape

```python
from dan.linter import lint, LintConfig, LintResult

result: LintResult = lint(
    data={"summary": "Q3 revenue was $4.2B...", "confidence": 0.92},
    config=LintConfig(
        structural=StructuralConfig(
            schema={"type": "object", "required": ["summary", "confidence"]},
            ranges={"confidence": (0.0, 1.0)},
        ),
        semantic=SemanticConfig(
            topic_keywords=["revenue", "Q3", "financial"],
            min_topic_similarity=0.7,
        ),
        intent=IntentConfig(
            intent="A concise financial summary for executive briefing",
            tier3_threshold=0.6,
        ),
    ),
    embedder=my_embedder,       # callable: str → list[float]
    llm_judge=my_llm_judge,     # callable: (data, intent) → JudgmentResult
)

assert result.passed
assert result.tier_reached == Tier.SEMANTIC  # didn't need Tier 3
```

## Rule Protocol

```python
from abc import ABC, abstractmethod

class Rule(ABC):
    tier: Tier
    name: str
    default_severity: Severity = Severity.ERROR

    @abstractmethod
    def check(self, data: Any, config: LintConfig, **deps) -> RuleResult:
        """Evaluate this rule against the data.
        
        deps may include embedder, llm_judge, etc. — injected by the engine.
        """
        ...
```

Rules are stateless. The engine instantiates them, calls `check()`, and collects results. Rules never import engine internals.

## Decisions

- The landed core uses a lightweight `Rule` protocol plus `RuleSpec` registries instead of instantiating class-based rule objects. This keeps the module small while still making tier/rule inventory explicit.
- `enabled` now lives directly on `LintConfig`, so callers can keep a resolved per-edge lint config attached while disabling it temporarily without deleting the contract.
- `LintResult` now carries `tier_reached` and `elapsed_ms`, which keeps the core package honest about what it actually evaluated before the scheduler projects the same metadata into runtime events.

## Notes

- The `lint()` function signature deliberately takes `embedder` and `llm_judge` as optional callables rather than concrete classes. This keeps the linter dependency-free — the caller provides whatever backend they want.
- `LintConfig` is designed to be serializable as JSON (for edge metadata storage) and parseable from a dict (for graph JSON round-trip).
- The engine measures elapsed time per tier so callers can monitor cost and adjust thresholds.
- The core short-circuits on hard structural errors now. Earlier slices still evaluated semantic/intent tiers after Tier 1 failures, but that contradicted the intended contract and is no longer true.
