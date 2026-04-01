# 47-4: Intent Validation & Auto-Fix

**Parent:** [47-agent-output-linter](47-agent-output-linter.md)
**Status:** not-started
**Goal:** Implement Tier 3 intent validation via focused LLM judgment, and the auto-fix system that repairs common issues across all tiers.

## Part A: Intent Validation (Tier 3)

### Tasks

- [ ] 1. Define LLM judge interface in `src/dan/linter/rules/intent.py`
  - [ ] 1-1. `LLMJudge` protocol: `__call__(data: Any, intent: str, context: dict | None) → JudgmentResult`
  - [ ] 1-2. `JudgmentResult`: `verdict: Literal["pass", "fail", "partial"]`, `confidence: float`, `reason: str`, `missing: list[str]` (what's missing for partial verdicts)
- [ ] 2. Implement `IntentAlignmentRule`
  - [ ] 2-1. Only triggered when Tier 2 aggregated confidence < `tier3_threshold`
  - [ ] 2-2. Constructs a focused prompt: "The receiving agent needs: {intent}. Does this output serve that purpose? Answer pass/fail/partial. If partial, list what is missing."
  - [ ] 2-3. Parses the LLM response into `JudgmentResult`
  - [ ] 2-4. Severity: error on "fail", warning on "partial", info on "pass"
  - [ ] 2-5. For "partial" verdicts, the `missing` list feeds into auto-fix retry prompts
- [ ] 3. Implement `CompletenessRule`
  - [ ] 3-1. A lighter variant of intent validation: "Does this output cover all the required aspects of the task?"
  - [ ] 3-2. Uses the same LLM judge but with a completeness-focused prompt
  - [ ] 3-3. Returns a list of covered vs. missing aspects
  - [ ] 3-4. Only runs if `IntentAlignmentRule` returns "partial" — a second-stage drill-down
- [ ] 4. Register Tier 3 rules in `intent.py` as `INTENT_RULES` list
- [ ] 5. Tests
  - [ ] 5-1. `tests/test_linter/test_intent.py` — with mock LLM judge
  - [ ] 5-2. Prompt construction for various data shapes (dict, str, list)
  - [ ] 5-3. Response parsing (valid JSON, malformed response, timeout)
  - [ ] 5-4. Tier 3 is never called when Tier 2 confidence is above threshold

## Part B: Auto-Fix System

### Tasks

- [ ] 6. Define `AutoFix` protocol in `src/dan/linter/autofix/__init__.py`
  - [ ] 6-1. `AutoFix` abstract base: `fix(data: Any, diagnostic: LintDiagnostic, config: LintConfig, **deps) → FixResult`
  - [ ] 6-2. `FixResult`: `fixed: bool`, `data: Any`, `description: str`
  - [ ] 6-3. Fixes are composable: multiple fixes can be applied in sequence to the same data
- [ ] 7. Implement fix strategies in `src/dan/linter/autofix/strategies.py`
  - [ ] 7-1. `FillDefaultsFix`: fill missing fields with type-appropriate or schema-defined defaults
  - [ ] 7-2. `TruncateFix`: truncate strings that exceed max length, preserving word boundaries
  - [ ] 7-3. `ClampFix`: clamp numeric values to valid ranges
  - [ ] 7-4. `CoerceFix`: attempt safe type coercion (str→int, str→float, etc.)
  - [ ] 7-5. `RetryWithFeedbackFix`: generate a structured retry prompt from diagnostics and call the LLM judge to re-generate
    - This fix requires the `llm_judge` dependency
    - The retry prompt includes: what failed, what's expected, the original input, and the failed output
    - Bounded by `max_retries` from `AutoFixConfig`
  - [ ] 7-6. `RefocusFix`: for topic-drift issues, generate a focused re-prompt that constrains the output topic
    - Also requires `llm_judge`
    - Uses Tier 2 confidence scores to describe what drifted
- [ ] 8. Fix orchestration in `src/dan/linter/engine.py`
  - [ ] 8-1. After rule evaluation, collect all diagnostics with available fixes
  - [ ] 8-2. Apply fixes in priority order: deterministic fixes first (fill, clamp, truncate, coerce), then LLM-based fixes (retry, refocus)
  - [ ] 8-3. After applying fixes, re-run the failing rules on the fixed data to verify
  - [ ] 8-4. If re-lint passes, set `auto_fixed=True` and return the fixed data
  - [ ] 8-5. If re-lint still fails after fixes, return `passed=False` with all diagnostics
  - [ ] 8-6. Total fix time bounded by `retry_budget_ms` — hard timeout on the entire fix cycle
- [ ] 9. Tests
  - [ ] 9-1. `tests/test_linter/test_autofix.py` — each fix strategy in isolation
  - [ ] 9-2. Fix composition: multiple fixes applied to the same data
  - [ ] 9-3. Fix + re-lint cycle: verify that fixed data passes re-validation
  - [ ] 9-4. Fix budget enforcement: timeout after `retry_budget_ms`
  - [ ] 9-5. `RetryWithFeedbackFix` with mock LLM: prompt includes failure details, respects max retries

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

- (to be filled during execution)

## Notes

- The `RetryWithFeedbackFix` is the linter's equivalent of the LLM executor's re-prompt loop, but at the handoff boundary rather than the LLM output boundary. It tells the producing agent *why* its output was rejected and *what* the receiving agent needs.
- LLM-based fixes (retry, refocus) require the producing agent to be re-invoked, which means the linter needs a way to signal "re-run this node with this feedback." This is handled by the engine integration in 47-5 — the linter itself just returns the retry prompt; the engine decides whether and how to re-invoke.
- The `RefocusFix` is specifically for Tier 2 topic drift. It adds constraints to the original prompt based on what the semantic rules detected: "Your output discussed X but the downstream agent needs Y. Focus on Y."
