# 47-1: Linter Core & Rule Protocol

**Parent:** [47-agent-output-linter](47-agent-output-linter.md)
**Status:** not-started
**Goal:** Define the core `lint()` engine, `LintConfig` / `LintResult` models, `Rule` protocol, tiered execution order, and severity model.

## Tasks

- [ ] 1. Create `src/dan/linter/` package
  - [ ] 1-1. Create `src/dan/linter/__init__.py` with public API exports
  - [ ] 1-2. Verify zero imports from `dan.engine`, `dan.worker`, `dan.models`
- [ ] 2. Define `Rule` protocol in `src/dan/linter/rules/__init__.py`
  - [ ] 2-1. `Rule` abstract base: `check(data, config) → RuleResult`
  - [ ] 2-2. `RuleResult`: `passed: bool`, `severity: Severity`, `message: str`, `fix: AutoFix | None`
  - [ ] 2-3. `Severity` enum: `error`, `warning`, `info`
  - [ ] 2-4. `Tier` enum: `STRUCTURAL`, `SEMANTIC`, `INTENT` — each rule declares its tier
- [ ] 3. Define `LintConfig` in `src/dan/linter/config.py`
  - [ ] 3-1. `StructuralConfig`: `schema: dict | None`, `required_fields: list[str]`, `ranges: dict[str, tuple]`, `non_empty: list[str]`, `format_patterns: dict[str, str]`
  - [ ] 3-2. `SemanticConfig`: `topic_keywords: list[str]`, `required_entities: list[str]`, `min_topic_similarity: float`, `embedding_reference: str | None`
  - [ ] 3-3. `IntentConfig`: `intent: str`, `tier3_threshold: float` (escalation threshold from Tier 2)
  - [ ] 3-4. `AutoFixConfig`: `strategies: list[str]`, `max_retries: int`, `retry_budget_ms: int`
  - [ ] 3-5. `LintConfig` container: structural + semantic + intent + autofix + default severity + enabled flag
- [ ] 4. Define `LintResult` in `src/dan/linter/result.py`
  - [ ] 4-1. `LintDiagnostic`: individual rule result with rule name, tier, severity, message, field path
  - [ ] 4-2. `LintResult`: `passed: bool`, `auto_fixed: bool`, `fixed_data: Any | None`, `diagnostics: list[LintDiagnostic]`, `tier_reached: Tier`, `elapsed_ms: float`
- [ ] 5. Implement `lint()` engine in `src/dan/linter/engine.py`
  - [ ] 5-1. Accept `data`, `config: LintConfig`, optional `embedder` callable, optional `llm_judge` callable
  - [ ] 5-2. Run Tier 1 rules first; if any error-severity rule fails, short-circuit (skip Tier 2/3)
  - [ ] 5-3. Run Tier 2 rules if semantic config is present and embedder is provided; collect confidence scores
  - [ ] 5-4. Run Tier 3 only if Tier 2 confidence is below `tier3_threshold` and llm_judge is provided
  - [ ] 5-5. After all rules: if any rule returned a fix, apply fixes in priority order and set `auto_fixed=True`
  - [ ] 5-6. Return `LintResult` with all diagnostics, final pass/fail, and timing
- [ ] 6. Add `src/dan/linter/rules/__init__.py` with empty `structural.py`, `semantic.py`, `intent.py` stubs
- [ ] 7. Add `src/dan/linter/autofix/__init__.py` with `AutoFix` protocol and empty `strategies.py` stub
- [ ] 8. Tests
  - [ ] 8-1. `tests/test_linter/test_engine.py` — tiered execution order, short-circuit behavior, timing
  - [ ] 8-2. `tests/test_linter/test_config.py` — config parsing, defaults, validation
  - [ ] 8-3. `tests/test_linter/test_result.py` — result construction, diagnostics aggregation
  - [ ] 8-4. Verify the linter module has zero imports from engine/worker/models (import check test)

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

- (to be filled during execution)

## Notes

- The `lint()` function signature deliberately takes `embedder` and `llm_judge` as optional callables rather than concrete classes. This keeps the linter dependency-free — the caller provides whatever backend they want.
- `LintConfig` is designed to be serializable as JSON (for edge metadata storage) and parseable from a dict (for graph JSON round-trip).
- The engine measures elapsed time per tier so callers can monitor cost and adjust thresholds.
