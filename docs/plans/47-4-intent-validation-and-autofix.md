# 47-4: Intent Validation & Auto-Fix

**Parent:** [47-agent-output-linter](47-agent-output-linter.md)
**Status:** completed
**Goal:** Implement Tier 3 intent validation via focused LLM judgment, and the auto-fix system that repairs common issues across all tiers.

## Part A: Intent Validation (Tier 3)

### Tasks

- [x] 1. Define LLM judge interface in `src/dan/linter/rules/intent.py`
  - [x] 1-1. `LLMJudge` protocol: `__call__(data: Any, intent: str, context: dict | None) → JudgmentResult`
  - [x] 1-2. `JudgmentResult`: `verdict: Literal["pass", "fail", "partial"]`, `confidence: float`, `reason: str`, `missing: list[str]` (what's missing for partial verdicts)
- [x] 2. Implement `IntentAlignmentRule`
  - [x] 2-1. Only triggered when Tier 2 aggregated confidence < `tier3_threshold`
  - [x] 2-2. Constructs a focused prompt: "The receiving agent needs: {intent}. Does this output serve that purpose? Answer pass/fail/partial. If partial, list what is missing."
  - [x] 2-3. Parses the LLM response into `JudgmentResult`
  - [x] 2-4. Severity: error on "fail", warning on "partial", info on "pass"
  - [x] 2-5. For "partial" verdicts, the `missing` list feeds into auto-fix retry prompts
- [x] 3. Implement `CompletenessRule`
  - [x] 3-1. A lighter variant of intent validation: "Does this output cover all the required aspects of the task?"
  - [x] 3-2. Uses the same LLM judge but with a completeness-focused prompt
  - [x] 3-3. Returns a list of covered vs. missing aspects
  - [x] 3-4. Only runs if `IntentAlignmentRule` returns "partial" — a second-stage drill-down
- [x] 4. Register Tier 3 rules in `intent.py` as `INTENT_RULES` list
- [x] 5. Tests
  - [x] 5-1. `tests/test_linter/test_intent.py` — with mock LLM judge
  - [x] 5-2. Prompt construction for various data shapes (dict, str, list)
  - [x] 5-3. Response parsing (valid JSON, malformed response, timeout)
  - [x] 5-4. Tier 3 is never called when Tier 2 confidence is above threshold

## Part B: Auto-Fix System

### Tasks

- [x] 6. Define `AutoFix` protocol in `src/dan/linter/autofix/__init__.py`
  - [x] 6-1. `AutoFix` abstract base: `fix(data: Any, diagnostic: LintDiagnostic, config: LintConfig, **deps) → FixResult`
  - [x] 6-2. `FixResult`: `fixed: bool`, `data: Any`, `description: str`
  - [x] 6-3. Fixes are composable: multiple fixes can be applied in sequence to the same data
- [x] 7. Implement fix strategies in `src/dan/linter/autofix/strategies.py`
  - [x] 7-1. `FillDefaultsFix`: fill missing fields with type-appropriate or schema-defined defaults
  - [x] 7-2. `TruncateFix`: truncate strings that exceed max length, preserving word boundaries
  - [x] 7-3. `ClampFix`: clamp numeric values to valid ranges
  - [x] 7-4. `CoerceFix`: attempt safe type coercion (str→int, str→float, etc.)
  - [x] 7-5. `RetryWithFeedbackFix`: generate a structured retry prompt from diagnostics and call the LLM judge to re-generate
    - This fix requires the `llm_judge` dependency
    - The retry prompt includes: what failed, what's expected, the original input, and the failed output
    - Bounded by `max_retries` from `AutoFixConfig`
  - [x] 7-6. `RefocusFix`: for topic-drift issues, generate a focused re-prompt that constrains the output topic
    - Also requires `llm_judge`
    - Uses Tier 2 confidence scores to describe what drifted
- [x] 8. Fix orchestration in `src/dan/linter/engine.py`
  - [x] 8-1. After rule evaluation, collect all diagnostics with available fixes
  - [x] 8-2. Apply fixes in priority order: deterministic fixes first (fill, clamp, truncate, coerce), then LLM-based fixes (retry, refocus)
  - [x] 8-3. After applying fixes, re-run the failing rules on the fixed data to verify
  - [x] 8-4. If re-lint passes, set `auto_fixed=True` and return the fixed data
  - [x] 8-5. If re-lint still fails after fixes, return `passed=False` with all diagnostics
  - [x] 8-6. Total fix time bounded by `retry_budget_ms` — hard timeout on the entire fix cycle
- [x] 9. Tests
  - [x] 9-1. `tests/test_linter/test_autofix.py` — each fix strategy in isolation
  - [x] 9-2. Fix composition: multiple fixes applied to the same data
  - [x] 9-3. Fix + re-lint cycle: verify that fixed data passes re-validation
  - [x] 9-4. Fix budget enforcement: timeout after `retry_budget_ms`
  - [x] 9-5. `RetryWithFeedbackFix` with mock LLM: prompt includes failure details, respects max retries

## Auto-Fix Priority Order

```text
1. FillDefaultsFix      (deterministic, free)
2. ClampFix             (deterministic, free)
3. TruncateFix          (deterministic, free)
4. CoerceFix            (deterministic, free)
5. RetryWithFeedbackFix (LLM-based, bounded)
6. RefocusFix           (LLM-based, bounded)
```

Deterministic fixes are always attempted first. LLM-based fixes are the last resort and are bounded by retry count and time budget.

## Retry Prompt Shape

```text
Your previous output failed validation at the handoff to the next agent.

**Receiving agent's need:** {intent}

**Validation failures:**
- [error] RequiredFieldsRule: field "confidence_score" is missing
- [warning] TopicSimilarityRule: output topic similarity is 0.42 (threshold: 0.7)

**Your previous output:**
{truncated_output}

Please regenerate your output, addressing the above failures.
```

## Decisions

- Tier 3 now treats `partial` as a first-class verdict instead of flattening everything into pass/fail. Partial judgments surface warning diagnostics with `missing` metadata rather than pretending every non-pass is a hard failure.
- Explicit `required_keywords` remain active even when a live judge backend is present. This keeps a deterministic intent guardrail in front of the softer LLM judgment path.
- Tier 3 prompt construction now lives in `build_intent_judge_prompt(...)` so the scheduler-side judge adapter stays thin and completeness follow-up can reuse the same contract-aware formatting across raw strings, dicts, and list payloads.
- Deterministic autofix is now a first-class linter submodule (`src/dan/linter/autofix/`). Structural lint no longer hardcodes fix mutations inline; it runs diagnostics, applies requested deterministic strategies in fix-order cycles, and re-lints until it stabilizes or runs out of useful fixes.
- LLM-backed autofix planning is also moving into the standalone linter layer. `RetryWithFeedbackFix` and `RefocusFix` now generate first-class feedback prompts in `dan.linter`, while the scheduler remains responsible for the actual producer re-invocation and retry budgeting.
- Retry budgeting is now explicit at the config/runtime boundary too. `LintConfig.retry_budget_ms` gives the scheduler a hard wall-clock budget for the entire retry loop, so a handoff cannot keep re-invoking producers indefinitely just because each individual retry is cheap.

## Notes

- The `RetryWithFeedbackFix` is the linter's equivalent of the LLM executor's re-prompt loop, but at the handoff boundary rather than the LLM output boundary. It tells the producing agent *why* its output was rejected and *what* the receiving agent needs.
- LLM-based fixes (retry, refocus) require the producing agent to be re-invoked, which means the linter needs a way to signal "re-run this node with this feedback." This is handled by the engine integration in 47-5 — the linter itself just returns the retry prompt; the engine decides whether and how to re-invoke.
- The `RefocusFix` is specifically for Tier 2 topic drift. It adds constraints to the original prompt based on what the semantic rules detected: "Your output discussed X but the downstream agent needs Y. Focus on Y."
- Current landed slice: `src/dan/linter/rules/intent.py` now has a `JudgmentResult` parser that accepts `passed` / `verdict` / `judgment` / `result` response shapes, normalizes `missing` / `covered` lists, and builds focused alignment plus completeness prompts from raw string/dict/list payloads. Partial judgments trigger a non-blocking `intent_partial` warning and, when useful, a second-stage `intent_completeness` warning with covered-vs-missing detail. The scheduler's injected judge adapter preserves that richer verdict shape, and `tests/test_linter/test_intent.py`, `tests/test_linter/test_core.py`, and `tests/test_engine/test_worker_lint_integration.py` now cover prompt construction, Tier 2 short-circuiting, completeness follow-up, and the retry-feedback path that carries missing requirements back into producer retries.
- Deterministic autofix now lives in `src/dan/linter/autofix/` with `AutoFix`, `FixResult`, and concrete `FillDefaultsFix`, `ClampFix`, `TruncateFix`, and `CoerceFix` strategies. `validate_structural(...)` applies those requested fixes in iterative cycles and re-runs structural diagnostics after each useful fix pass, which means composed cases like `coerce -> clamp` now work without scheduler involvement. `tests/test_linter/test_autofix.py` covers the individual strategies, and `tests/test_linter/test_core.py` now covers an end-to-end composed relint case.
- `RetryWithFeedbackFix` and `RefocusFix` now also exist as first-class linter strategies that generate retry/refocus prompts from diagnostics plus config, and `LintResult` now carries `suggested_fixes`, `retry_feedback`, and `refocus_feedback` so the engine layer can consume those plans without rebuilding them ad hoc. The scheduler now prefers those linter-produced prompts when retrying Worker/LLM producers, and `tests/test_engine/test_worker_lint_integration.py` covers both retry-with-feedback and refocus-driven re-execution.
- `LintConfig` now also carries `retry_budget_ms`, and the scheduler enforces it across the whole retry loop rather than per-attempt. `tests/test_engine/test_worker_lint_integration.py` includes a deterministic budget-exhaustion case using controlled scheduler time so the bound stays provable instead of relying on flaky wall-clock sleeps.
- `src/dan/linter/rules/intent.py` now also exports the explicit `INTENT_RULES` registry and a public `LLMJudge` protocol from the core rule helpers, which closes the remaining protocol/registry gap between the landed intent runtime and the original subplan.
- The fix-order contract is now explicitly regression-tested too. `tests/test_linter/test_core.py` asserts that mixed model-backed autofix planning prefers `retry_with_feedback` before `refocus`, matching `MODEL_AUTOFIX_ORDER` instead of depending on request order.
