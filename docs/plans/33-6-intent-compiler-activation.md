# 33-6: Intent Compiler Activation

**Parent:** [33-generation-quality-eval](33-generation-quality-eval.md)
**Status:** completed
**Goal:** Get the intent compiler from 0% activation to meaningful usage on T1/T2 prompts, so simple workflows take the fast deterministic path instead of depending on fragile full-model codegen.

## Problem

Phase 22 (32-2) expanded the intent compiler from 7 to 15+ patterns and added auto-composition. Phase 33 pilot revealed **0% intent compiler activation** — every successful build went through full LLM codegen, even trivial prompts like "Build a simple 3-step chain."

The pipeline is: intent extraction (LLM tool call) → coverage check → compile → execute → validate. If any step fails, the system silently falls back to codegen. The pilot logs show no `chat_intent_extracted` events with `fully_covered=true`, which means:

1. **Intent extraction fails** — the LLM doesn't produce a valid tool call, or `_parse_intent_from_result()` can't parse it. This is the *only* possible failure point. `CoverageChecker.SUPPORTED_TYPES = set(StageType)` (line 118) means coverage *always* returns `fully_covered=True` when extraction succeeds — it's a no-op. The `_try_decompose()` composition path is unreachable dead code.
2. **`_parse_intent_from_result()` only handles `tool_calls`** — if the LLM puts the intent as JSON in message content (common with models that don't fully support function calling), the parser returns `None` and the compiler path is silently skipped.

**Additional performance gap:** Intent-compiled code currently runs through `_sandbox_exec_builder_code()` (subprocess, line 3331), but `_exec_deterministic_builder_code()` (line 3524, in-process `exec()`) exists specifically for deterministic IntentCompiler output and is unused. This adds unnecessary subprocess overhead.

Without the intent compiler, every build depends on the LLM generating correct `dan.builder` Python code from scratch — expensive, slow, and non-deterministic. T1/T2 prompts (chains, review loops, fan-outs, RAG) are exactly the cases the intent compiler was designed for.

## Diagnosis Tasks

- [x] 1. **Instrument the intent pipeline with debug logging**
  - [x] 1-1. Add `logger.info` at each decision point in `_generate_workflow_from_intent()`: after intent extraction (log the raw result, whether tool call was present, parsed intent or parse error), after coverage check (log `fully_covered`, `recommendation`, `constituent_patterns`), after compile attempt (log success/failure)
  - [ ] 1-2. Run 5 T1/T2 pilot prompts (p01-p04, p06) with `DAN_LOG_LEVEL=DEBUG` and capture the logs
  - [ ] 1-3. For each prompt, identify exactly where the pipeline breaks: extraction? parsing? coverage? compilation?

- [ ] 2. **Check intent extraction**
  - [ ] 2-1. Verify the LLM model being used supports tool/function calling (deepseek-v3.2 via the current provider). If the provider silently ignores `tools` parameter or doesn't return `tool_calls`, extraction will always fail
  - [ ] 2-2. Check `_parse_intent_from_result()` — does it handle all provider response formats? (OpenAI-style `tool_calls`, Anthropic-style `tool_use`, raw JSON in message content)
  - [ ] 2-3. Test intent extraction in isolation: send the `INTENT_EXTRACTION_SYSTEM_PROMPT` + a T1 prompt with `tool_choice="auto"` and inspect the raw response. Does the LLM produce a valid `WorkflowIntent` tool call?
  - [ ] 2-4. If the model doesn't support tool calling, add a JSON-in-message fallback: prompt the LLM to output `WorkflowIntent` as JSON in the message content, then parse it

- [x] 3. **Verify compile() handles extracted intents**
  - [x] 3-1. Coverage is a no-op (`SUPPORTED_TYPES = set(StageType)`, always returns `fully_covered=True`). Confirm this by reading `CoverageChecker.check()` — no code change needed, just verify the diagnosis is correct.
  - [x] 3-2. Manually construct a `WorkflowIntent` for "Build a simple 3-step chain" (3 `transform` stages) and run `IntentCompiler().compile(intent)` — does it produce valid builder code?
  - [x] 3-3. Do the same for "review loop" (1 `review_loop` stage), "fan-out" (1 `fan_out` stage), "RAG pipeline" (1 `rag_retrieval` stage) — confirm the compiler handles each stage type.
  - [x] 3-4. If `compile()` throws on any stage type, that's the gap to fix (the coverage checker can't catch it since it approves everything). Check that all 8 `StageType` values have handlers in `IntentCompiler._compile_stage()`.
  - [ ] 3-5. If the LLM consistently maps everything to `StageType.transform`, the issue is in `INTENT_EXTRACTION_SYSTEM_PROMPT` — it needs stronger guidance on when to use `review_loop`, `fan_out`, `rag_retrieval`, etc. (same as task 4-2)

## Fix Tasks

- [x] 4. **Fix intent extraction for current provider**
  - [x] 4-1. If tool calling doesn't work with current model: add JSON-in-content fallback to `_parse_intent_from_result()` (try `json.loads(result.content)` → `WorkflowIntent.model_validate()`)
  - [x] 4-2. If extraction works but produces wrong stage types: update `INTENT_EXTRACTION_SYSTEM_PROMPT` with explicit examples mapping common phrases to correct `StageType` values. E.g., "review loop" → `review_loop`, "in parallel" → `fan_out`, "search and retrieve" → `rag_retrieval`
  - [ ] 4-3. If extraction works correctly: the problem is downstream in coverage. Move to task 5.

- [x] 5. **Switch intent-compiled code to in-process execution**
  - [x] 5-1. In `_generate_workflow_from_intent()` Step 2 (line ~3331): replace `self._sandbox_exec_builder_code(builder_code)` with `self._exec_deterministic_builder_code(builder_code)`. The latter already exists (line ~3524) and is specifically documented for IntentCompiler output — it runs via `exec()` in-process, no subprocess overhead.
  - [x] 5-2. Keep `_sandbox_exec_builder_code()` for codegen path only (LLM-generated code must remain sandboxed for security).
  - [x] 5-3. Confirm `_exec_deterministic_builder_code()` returns the same `dict | None` format expected by the validation step.

- [ ] 6. **Verify end-to-end**
  - [ ] 6-1. Re-run the 5 T1/T2 pilot prompts with fixes applied
  - [ ] 6-2. Confirm `chat_intent_extracted` events appear with `fully_covered=true`
  - [ ] 6-3. Confirm `chat_code_generated` events appear with `source="intent_compiler"`
  - [ ] 6-4. Compare latency: intent compiler path should be significantly faster than codegen (in-process `exec()` instead of subprocess + no codegen LLM call)

- [x] 7. **Add intent compiler monitoring**
  - [x] 7-1. Add a telemetry event for intent extraction outcome: `event_type="intent_extraction"`, metadata includes `{extracted: bool, fully_covered: bool, recommendation: str, stage_count: int, patterns: list}`
  - [x] 7-2. Update the eval harness to report intent compiler activation rate per tier

## Key Files

| File | Role |
|------|------|
| `src/dan/server/chat_manager.py` | `_generate_workflow_from_intent()` — the pipeline entry point (line ~3244) |
| `src/dan/meta/intent_extraction.py` | `INTENT_EXTRACTION_SYSTEM_PROMPT`, `build_intent_tool_schema()` |
| `src/dan/meta/intent_schema.py` | `WorkflowIntent`, `StageIntent`, `StageType` |
| `src/dan/meta/intent_compiler.py` | `CoverageChecker`, `IntentCompiler`, `COVERAGE_CATALOG` |
| `tests/eval/runner.py` | Eval harness — records generation path |

## Success Criteria

- [ ] T1 prompts (chain, review_loop, fan_out) use intent compiler path ≥80% of the time
- [ ] T2 prompts (RAG, data_analysis, tool_chain) use intent compiler path ≥50% of the time
- [ ] Intent compiler path is ≥2x faster than codegen path for same prompts
- [ ] No regressions on prompts that currently pass via codegen

## Decisions

- (filled in during execution)

## Notes

- The intent compiler is deterministic once intent extraction succeeds — the only LLM call is extraction itself. This makes it inherently more reliable than full codegen.
- If the current LLM model truly can't do tool calling, JSON-in-content is the pragmatic fix. Don't block on switching models.
- This plan is independent of 33-7 (quality gates) and 33-8 (codegen resilience). All three can run in parallel, but all three modify `_generate_workflow_from_intent()` — coordinate merges.
- The pilot showed that when codegen works, it produces valid graphs. The issue is reliability, not capability. Intent compiler fixes the reliability problem for the common cases.
- **Coverage checker is a no-op** — `SUPPORTED_TYPES = set(StageType)` makes it always return `fully_covered=True`. The real gate is intent extraction → `_parse_intent_from_result()` → `IntentCompiler.compile()`. Coverage checking and decomposition are vestigial from Phase 14 when not all stage types were supported. Consider removing the coverage step entirely (or making it meaningful by checking COVERAGE_CATALOG pattern matching instead of just type membership).
- **`_exec_deterministic_builder_code()` vs `_sandbox_exec_builder_code()`** — the in-process executor was built for intent-compiled code but was never wired into the pipeline. The sandbox is only needed for untrusted LLM-generated code.
