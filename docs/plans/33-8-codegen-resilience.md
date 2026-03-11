# 33-8: Codegen Resilience & Provider Hardening

**Parent:** [33-generation-quality-eval](33-generation-quality-eval.md)
**Status:** completed
**Goal:** Make the codegen path survive LLM API flakiness — retry on transient errors, fall back gracefully, and improve eval scoring granularity so failures are diagnosable.

## Problem

The Phase 33 pilot showed that the #1 failure cause is **LLM API instability**, not bad generation logic:

| Failure | Cause | Prompt |
|---------|-------|--------|
| p01 Run 2 regression | Internal server error after successful codegen | T1 chain |
| p03 | Stuck in "Planning the workflow" for 70s, wall timeout | T2 web search |
| p05 | WebSocket keepalive ping timeout, 0 events | T3 lit review |
| p06/p07/p08 Run 2 | 0 events — server instability under load | T3/T4 mixed |

Cross-run comparison: only 3/10 prompts pass consistently. 3/10 are flaky. The stable pass rate is ~30-40%, not the headline 60%.

Current behavior: a single LLM failure kills the build with no recovery. The codegen path (`_generate_workflow_from_intent()`) makes one attempt; if the LLM returns an error, empty response, or times out, the entire generation fails. The existing bounded diagnosis loop (24-4) only activates for *validation* failures (bad builder code), not for *infrastructure* failures (API errors, timeouts, empty responses).

## Tasks

### A. Codegen retry for transient failures

- [ ] 1. **Add retry wrapper around codegen LLM call**
  - [ ] 1-1. In `_generate_workflow_from_intent()`, wrap the codegen `provider.complete()` call (Step 3, line ~3360) with a retry loop: max 2 retries, exponential backoff (2s, 4s), only retry on transient errors (HTTP 429/500/502/503, timeout, empty response, connection error)
  - [ ] 1-2. Do NOT retry on deterministic failures (valid LLM response that produces bad code) — those go to the existing diagnosis loop
  - [ ] 1-3. Log each retry attempt with reason, attempt number, and delay
  - [ ] 1-4. Record retry count in telemetry (`TelemetryEvent.metadata["codegen_retries"]`)

- [ ] 2. **Add retry for intent extraction LLM call**
  - [ ] 2-1. Same pattern as codegen: wrap the intent extraction `provider.complete()` (Step 1, line ~3290) with max 1 retry on transient errors
  - [ ] 2-2. Intent extraction is cheaper (smaller prompt), so a retry here has low cost and high value

- [ ] 3. **Handle empty/malformed LLM responses gracefully**
  - [ ] 3-1. If `provider.complete()` returns an empty `content` and no `tool_calls`, treat as transient failure and retry (currently this silently produces `intent=None` and falls through to codegen)
  - [ ] 3-2. If codegen produces empty or whitespace-only code, retry instead of passing empty string to sandbox
  - [ ] 3-3. If the sandbox returns `None` after passing `ast.parse()` (task 9): the error is a runtime failure (import error, attribute error, timeout) not a syntax error. Classify: subprocess timeout → transient (retry), other runtime error → deterministic (populate `codegen_errors` per task 10 and fall through to diagnosis)

### A2. Deterministic companion steps (pipeline gaps)

These are steps that should *always* follow another step but are currently missing or incomplete in `_generate_workflow_from_intent()`:

- [ ] 9. **Pre-sandbox syntax check**
  - [ ] 9-1. After `_extract_code_from_response()`, run `ast.parse(builder_code)` before sending to sandbox. This is ~0ms and catches syntax errors without a subprocess.
  - [ ] 9-2. On syntax error: skip sandbox, populate `codegen_errors` with a `SyntaxError` entry (stage, line, message), and fall through to the diagnosis loop. Currently a syntax error goes to sandbox → returns None → `codegen_errors = []` → diagnosis skipped entirely.
  - [ ] 9-3. Emit a `ChatValidationResultEvent(success=False, errors=["Syntax error: ..."])` so the eval harness can distinguish syntax errors from other failures.

- [ ] 10. **Sandbox None → diagnosis path (not just validation failures)**
  - [ ] 10-1. Currently line 3402: when sandbox returns `None`, `codegen_errors = []`, and the diagnosis loop guard `if builder_code and codegen_errors` skips it. Fix: when sandbox returns None AND builder_code is non-empty, create a synthetic error entry (`GenerationError(stage="sandbox", error_type="no_output", message="Builder code produced no graph")`) so the diagnosis loop can attempt repair.
  - [ ] 10-2. This alone could recover some failures that currently silently die.

- [ ] 11. **Post-diagnosis re-validation**
  - [ ] 11-1. Line 3451: when `diag_result.success and diag_result.final_graph`, the code emits `ChatValidationResultEvent(success=True, error_count=0)` and returns — but this is a hardcoded success event, not actual validation. Fix: run `validate_codegen_output(diag_result.final_graph)` and emit the real validation result. If validation fails after diagnosis, don't return the graph.

- [ ] 12. **Post-mutation re-validation**
  - [ ] 12-1. In `send_message_with_tools()` line ~2246: after `dispatch_compound_mutations()` succeeds, the graph is saved directly with `_graph_store.save_graph()` — no `validate_graph()` call. Fix: validate the mutated graph before saving. If validation fails, rollback the mutation (the `@_atomic_macro` decorator handles per-macro rollback, but compound dispatch doesn't validate the final composite result).
  - [ ] 12-2. Emit `ChatValidationResultEvent` after mutation validation so the user sees if the mutation broke the graph.

### B. Classifier resilience

- [ ] 4. **Add observability to classifier fallback**
  - [ ] 4-1. `classify_intent_llm()` already falls back to `classify_intent()` on exception (line 451) and on unparseable LLM response (line 449/453). The fallback itself works. What's missing is **visibility**: there's no telemetry or structured log when a fallback occurs, so we can't tell from eval data how often the LLM classifier fails.
  - [ ] 4-2. Add telemetry event for classifier fallback: `event_type="classification"`, metadata includes `{llm_failed: true, fallback: "heuristic", reason: "exception"|"unparseable"|"empty_content"}`. Emit at the two fallback sites (exception catch line 451, unparseable line 449).
  - [ ] 4-3. Log `logger.warning(...)` (not just `logger.debug`) when classifier falls back — the current debug log is invisible at default log level.
  - [ ] 4-4. *(Optional)* If the heuristic fallback returns `CONVERSATION` with low confidence for a prompt that contains "build"/"create"/"workflow", consider boosting to `WORKFLOW_BUILD`. This is a safety net for the edge case where both LLM and heuristic misclassify.

### C. Server stability under eval load

- [ ] 5. **WebSocket keepalive hardening**
  - [ ] 5-1. Review the WebSocket ping/pong configuration in `app.py`. The pilot hit keepalive timeout on p05 (145s) — if the codegen LLM call takes >60s, the WebSocket may time out before the response arrives
  - [ ] 5-2. Increase WebSocket ping interval or add application-level heartbeat during long codegen calls (emit a `progress_ack` event every 30s while waiting for codegen)
  - [ ] 5-3. In the eval harness `client.py`, add WebSocket reconnect-on-close with 1 retry

- [ ] 6. **Sequential eval pacing**
  - [ ] 6-1. In `runner.py`, add a configurable delay between prompts (`--delay N` flag, default 2s) to prevent CPU saturation from overlapping codegen calls
  - [ ] 6-2. Log server health (CPU/memory if accessible, or just response time of `GET /api/graphs`) between prompts as a load indicator

### D. Eval scoring refinement

- [ ] 7. **Split "no_graph_created" into granular categories**
  - [ ] 7-1. Update `_determine_status()` in `runner.py` to distinguish:
    - `timeout_planning` — stuck in planning/context phase, never reached codegen
    - `timeout_codegen` — reached codegen but LLM timed out
    - `llm_error` — LLM returned error (500, empty response, malformed JSON)
    - `stream_error` — WebSocket/connection failure before any events
    - `routing_blocked` — confirmation prompt or meta-session redirect prevented build
    - `correct_refusal` — T5 edge case, correctly did not build
    - `syntax_error` — codegen produced code that fails `ast.parse()` (pre-sandbox)
    - `codegen_failed` — codegen produced code but sandbox/validation failed
  - [ ] 7-2. Update `report.py` failure mode table to show the new categories
  - [ ] 7-3. Add a "flakiness" indicator to the report: compare same-prompt results across runs (requires `--tag` on multiple runs of the same battery)

- [ ] 8. **Multi-run stability measurement**
  - [ ] 8-1. Add `--runs N` flag to CLI that repeats the battery N times and computes per-prompt consistency (pass/fail/flaky)
  - [ ] 8-2. A prompt is "flaky" if it passes in some runs but fails in others
  - [ ] 8-3. Report: stable pass count, stable fail count, flaky count, flakiness rate
  - [ ] 8-4. This is the key metric for measuring provider reliability improvement

## Key Files

| File | Action |
|------|--------|
| `src/dan/server/chat_manager.py` | **Modify** — retry wrappers, pre-sandbox lint, sandbox-None → diagnosis, post-diagnosis re-validation, post-mutation validation |
| `src/dan/server/concierge/classifier.py` | **Modify** — empty-response fallback to heuristic |
| `src/dan/server/app.py` | **Review** — WebSocket ping/pong config, heartbeat during codegen |
| `src/dan/meta/structural_mutations.py` | **Review** — mutation validation (currently caller's responsibility) |
| `tests/eval/runner.py` | **Modify** — granular failure categories, delay flag |
| `tests/eval/report.py` | **Modify** — new failure categories, flakiness indicator |
| `tests/eval/__main__.py` | **Modify** — `--delay`, `--runs` flags |

## Success Criteria

- [ ] Codegen survives at least 1 transient LLM error per prompt (retry recovers)
- [ ] Classifier falls back to heuristic on empty LLM response (instead of defaulting to CONVERSATION at 0.5)
- [ ] Eval report shows granular failure categories (not just "no_graph_created")
- [ ] `--runs 3` produces a flakiness report showing per-prompt consistency
- [ ] p01 (T1 chain) passes consistently across 3 runs (currently flaky)
- [ ] Syntax errors detected pre-sandbox (no subprocess needed for obviously broken code)
- [ ] Sandbox `None` triggers diagnosis instead of silent failure
- [ ] Diagnosis-repaired graphs are actually re-validated (not hardcoded success)
- [ ] Mutated graphs are validated before save

## Decisions

- (filled in during execution)

## Notes

- **Task numbering:** Tasks are grouped by section (A: 1-3, A2: 9-12, B: 4, C: 5-6, D: 7-8). Non-sequential but stable — task IDs are used in cross-references from 33-7.
- The retry budget is intentionally small (max 2 retries for codegen, 1 for extraction). The goal is to survive transient API errors, not mask systematic problems. If a prompt needs 3+ retries, the underlying issue needs fixing.
- Server stability (task 5-6) is a narrow scope: WebSocket keepalive and eval pacing. Broader server performance is out of scope for this plan.
- The classifier fallback (task 4) is a safety net. With intent compiler activation (33-6), correctly classified prompts will take the deterministic path and bypass the classifier flakiness altogether.
- Multi-run stability (task 8) is the primary metric for measuring whether this plan succeeded. The raw pass rate on a single run is noisy; cross-run consistency is the real signal.
- This plan is independent of 33-6 (intent compiler) and 33-7 (quality gates). All three address different failure modes and can proceed in parallel.
- **Deterministic companion step principle:** Every generation/mutation action should have guaranteed follow-up steps that never depend on LLM output. The full companion chain is: LLM call → empty check → code extraction → `ast.parse()` → sandbox → error classification → structural validation → semantic quality (33-7) → outcome recording. Currently several links in this chain are missing (tasks 9-12). Mutations have a parallel chain: macro dispatch → validation → save. Neither chain should have silent failure paths where a step produces nothing and the pipeline just moves on.
