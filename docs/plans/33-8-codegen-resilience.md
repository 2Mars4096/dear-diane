# 33-8: Codegen Resilience & Provider Hardening

**Parent:** [33-generation-quality-eval](33-generation-quality-eval.md)
**Status:** completed *(initial implementation shipped 2026-03-11; P1/P2/P3 patches verified 2026-03-12; original tasks 1-12 claimed shipped but granular eval categories [task 7] not reflected in battery output — see Post-Patch Battery Findings below)*
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
  - [ ] 3-3. If the sandbox returns `None` (compilation failed), check whether the error is a Python syntax error (deterministic → diagnosis) vs. a sandbox timeout (transient → retry)

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

- [ ] 4. **Harden classifier against empty responses**
  - [ ] 4-1. In `classify_intent_llm()`, if the LLM returns empty content, fall back to `classify_intent()` (keyword heuristic) with confidence 0.6 instead of returning `CONVERSATION` at 0.5. The heuristic is more reliable than a failed LLM call.
  - [ ] 4-2. Add telemetry event for classifier fallback: `event_type="classification"`, metadata includes `{llm_failed: true, fallback: "heuristic"}`
  - [ ] 4-3. Log warning when classifier falls back so it's visible in server logs

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
| `src/dan/server/chat_manager.py` | **Modify** — retry wrappers on codegen + intent extraction LLM calls |
| `src/dan/server/concierge/classifier.py` | **Modify** — empty-response fallback to heuristic |
| `src/dan/server/app.py` | **Review** — WebSocket ping/pong config, heartbeat during codegen |
| `tests/eval/runner.py` | **Modify** — granular failure categories, delay flag |
| `tests/eval/report.py` | **Modify** — new failure categories, flakiness indicator |
| `tests/eval/__main__.py` | **Modify** — `--delay`, `--runs` flags |

## Success Criteria

- [ ] Codegen survives at least 1 transient LLM error per prompt (retry recovers)
- [ ] Classifier falls back to heuristic on empty LLM response (instead of defaulting to CONVERSATION at 0.5)
- [ ] Eval report shows granular failure categories (not just "no_graph_created")
- [ ] `--runs 3` produces a flakiness report showing per-prompt consistency
- [ ] p01 (T1 chain) passes consistently across 3 runs (currently flaky)

## Patch Tasks (post code-review 2026-03-12)

- [x] P1. **Generation failure budget**
  - [x] P1-1. The generation pipeline currently has no cap on total work: intent extraction (1 retry) + codegen (2 retries) + sandbox (1 retry on timeout) + diagnosis loop (bounded at `MAX_DIAGNOSIS_ROUNDS`). The cumulative wall-clock time can exceed 3 minutes for a single prompt. Add a `DAN_MAX_GENERATION_SECONDS` env var (default 120s) as a hard wall-clock cap across the entire `_generate_workflow_from_intent()` call. When reached, emit a terminal `ChatValidationResultEvent` with a clear timeout reason and skip remaining retries/diagnosis.
  - [x] P1-2. Log the total generation time and retry/diagnosis counts in `_record_gen_outcome()` metadata so the eval harness can analyze time-to-failure distributions.

- [x] P2. **User-visible terminal failure reasons**
  - [x] P2-1. When generation fails terminally (after all retries exhausted), the user currently sees either nothing or a generic "Workflow creation failed." Improve the terminal `ChatValidationResultEvent` error messages to include the failure category: "Codegen timed out after 2 retries", "LLM returned empty response", "Generated code had syntax errors that could not be repaired", "Graph quality score (25) below minimum threshold (40) for this complexity level".
  - [x] P2-2. Surface the failure category in the chat response text (not just the stream event) so non-streaming surfaces (Telegram, WhatsApp) also see actionable feedback.

- [x] P3. **Sandbox timeout classification**
  - [x] P3-1. The sandbox retry currently retries once on any timeout. Distinguish between sandbox process startup timeout (infra issue, worth retrying) and builder-code execution timeout (the generated code may have an infinite loop — not worth retrying, send to diagnosis with "execution_timeout" error type instead of generic "timeout").

## Decisions

- Retry budgets intentionally small (1 for extraction, 2 for codegen, 1 for sandbox timeout). Shipped.
- Pre-sandbox `ast.parse()`, sandbox-None→diagnosis, post-diagnosis re-validation, post-mutation validation all shipped.
- Granular failure categories and `--runs N` flakiness measurement shipped.
- P1: `DAN_MAX_GENERATION_SECONDS` (default 120s) — deadline checks before codegen and diagnosis, generation time logged on all return paths.
- P2: Terminal failure events now include specific failure categories ("empty response after N retries", "LLM call failed: ..."). Fallback event added for cases with no explicit failure event.
- P3: Sandbox timeout split into execution timeout (skip retry, send to diagnosis) vs process startup timeout (retry once).

## Post-Patch Battery Findings (2026-03-12)

Full battery run: 51 records (41 prompts + 10 multi-turn), 54.9% pass rate. Results file: `tests/eval/results/2026-03-12_133646_run.jsonl`.

**Failure mode distribution:**
- `no_graph_created`: 20 (87% of failures)
- `misrouted`: 3 (13% of failures)
- All other categories: 0

**Despite retry being implemented (tasks 1-3), 20/23 failures still produce no graph.** Breakdown:
- 13 failures have `path=unknown` or `path=-` — these never reached codegen. The failure is upstream: classification, context gathering, or routing. Retry on the codegen LLM call cannot help.
- 7 failures have `path=codegen` — codegen was attempted but still failed. Either retry was exhausted, or the wall-clock cap (P1) intervened. Examples: t3-02 at 427s, t1-02 at 245s (both exceed the 120s cap, suggesting pre-generation time dominates).

**Granular failure categories (task 7) gap:** The Decisions section says "Granular failure categories and `--runs N` flakiness measurement shipped." However, the battery report only shows `no_graph_created` and `misrouted` — none of the granular subcategories (`timeout_planning`, `timeout_codegen`, `llm_error`, `stream_error`, `routing_blocked`, `correct_refusal`, `codegen_failed`) appear. Either the eval harness `_determine_status()` isn't emitting them, or the server events don't carry enough signal to distinguish them. This is the top diagnostic gap — without granular categories, the 20 `no_graph_created` failures are an opaque bucket.

**Cross-run flakiness (2 runs compared):** Stable pass: 2, Stable fail: 2, Flaky: 6 (60% flakiness). Key flips: p01 passed→failed, p03 failed→passed, p06/p07 failed→passed, p09/p10 passed→failed.

**Implications for 33-9:** The data confirms that the dominant bottleneck is now upstream of the generation pipeline. 33-9 tasks D (pre-generation latency) and E (agent-lane routing) address this directly. Task 7 (granular categories) is a prerequisite for further triage — add to 33-9 scope if not already effective.

## Notes

- The retry budget is intentionally small (max 2 retries for codegen, 1 for extraction). The goal is to survive transient API errors, not mask systematic problems. If a prompt needs 3+ retries, the underlying issue needs fixing.
- Server stability (task 5-6) is a narrow scope: WebSocket keepalive and eval pacing. Broader server performance is out of scope for this plan.
- The classifier fallback (task 4) is a safety net. With intent compiler activation (33-6), correctly classified prompts will take the deterministic path and bypass the classifier flakiness altogether.
- Multi-run stability (task 8) is the primary metric for measuring whether this plan succeeded. The raw pass rate on a single run is noisy; cross-run consistency is the real signal.
- This plan is independent of 33-6 (intent compiler) and 33-7 (quality gates). All three address different failure modes and can proceed in parallel.
