# 38-8: Concierge Triage & Dispatch Correctness

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** in-progress
**Goal:** Fix correctness bugs and quality gaps in the concierge triage pipeline and tier executor dispatch identified in the 2026-03-19 concierge triage review.

## Context

- The triage LLM is configured with `max_tokens=60` for a response requiring 13 JSON fields — this almost certainly causes frequent truncation, falling back to the heuristic path with 0.50-0.58 confidence.
- The `Session` model's `child_execution` field is `Literal["parallel", "serial"]` but the dispatcher assigns `"mixed"`, creating a type mismatch that Pydantic may silently accept or reject depending on validation mode.
- `asyncio.run()` is called from within background tasks on the event loop in two locations.
- The plan decomposition stub splits on `" and "`, tier-2 synthesis is mechanical concatenation, and several other dispatch correctness gaps exist.

## Tasks

### 1. Increase triage `max_tokens` (P1 — highest impact)
- [x] 1-1. Increase `runtime.py:409` `max_tokens` from `60` to at least `256` (a minimal valid 13-field JSON response is ~100-150 tokens). *(fixed 2026-03-19: changed to 256)*
- [ ] 1-2. Verify that the higher token budget does not meaningfully increase triage latency (the response is still short JSON).
- [ ] 1-3. Add a regression test confirming triage JSON can be parsed at the new limit.

### 2. Fix `child_execution` literal type mismatch
- [x] 2-1. Expand `Session.child_execution` to `Literal["parallel", "serial", "mixed"]` in `session.py:102`. *(fixed 2026-03-19)*
- [x] 2-2. Add handling for `"mixed"` in `MultiStepExecutor` (currently falls through to serial behavior — make this explicit). *(fixed 2026-03-19: `MultiStepExecutor` now logs and normalizes `mixed` to the current serial-safe fallback instead of relying on an implicit else branch)*

### 3. Fix `asyncio.run()` inside running event loop
- [ ] 3-1. Replace `asyncio.run(_call())` at `runtime.py:1402` with proper thread-safe async bridging (e.g., `asyncio.run_coroutine_threadsafe()` on the main loop, or restructure to stay async).
- [ ] 3-2. Fix the same pattern at `runtime.py:1753` for memory extraction.
- [ ] 3-3. Add a test that the domain reflection and memory extraction paths work from within an already-running event loop.

### 4. Deduplicate fast-classification path
- [ ] 4-1. Remove the duplicate `fast_classify_text()` call in `_do_triage()` or ensure the fast result goes through `_post_process_triage_result` before returning.
- [ ] 4-2. Add a test confirming social messages get consistent post-processing regardless of entry path.

### 5. Replace naive plan decomposition stub
- [ ] 5-1. Replace the `task.split(" and ")` stub in `tier_executors.py:1184-1189` with a lightweight LLM call that decomposes the task into subtasks.
- [ ] 5-2. Add a token budget cap on the decomposition call to keep it cheap.
- [ ] 5-3. Keep the string split as a no-LLM fallback when the model is unavailable.

### 6. Add LLM-powered tier-2 synthesis
- [ ] 6-1. After collecting child results in `MultiStepExecutor._synthesize()`, add an optional LLM synthesis pass that produces a coherent unified response instead of concatenated sections.
- [ ] 6-2. Gate behind autonomy level: `aggressive` always synthesizes, `balanced` synthesizes for 3+ children, `careful` concatenates only.
- [ ] 6-3. Keep concatenation as the fallback when LLM synthesis fails or is disabled.

### 7. Fix volatile concierge state race condition
- [ ] 7-1. Add an `asyncio.Lock` per surface key on `_volatile_concierge_states` to serialize read-modify-write cycles.
- [ ] 7-2. Add a test with concurrent dispatches to the same surface confirming no state corruption.

### 8. Refine child session route inheritance
- [ ] 8-1. In `tier_executors.py`, filter `action_hints` when creating child sessions so research subtasks don't inherit `workflow_edit` hints.
- [ ] 8-2. Only propagate action hints that are relevant to the child's decomposed task, not the parent's full route.

### 9. Expand triage context window
- [ ] 9-1. Increase from 4 turns × 200 chars to at least 6 turns × 400 chars for the triage context.
- [ ] 9-2. Coordinate with task 1 (`max_tokens` increase) since the LLM needs enough context to classify correctly.

### 10. Fix `_finalize_task` completion marking
- [ ] 10-1. Distinguish between `completed` (task fully done) and `partial` / `interrupted` when content was produced but the task was not fully finished.
- [ ] 10-2. Use the existing `SessionState` transitions to inform the final task status.

## Primary Files

- `src/dan/server/concierge/runtime.py`
- `src/dan/server/concierge/triage.py`
- `src/dan/server/concierge/tiered_dispatch.py`
- `src/dan/server/concierge/tier_executors.py`
- `src/dan/server/concierge/session.py`

## Decisions

- `max_tokens=256` is a safe starting point — the JSON response is compact; 256 tokens is ~3x the minimum needed.
- Plan decomposition: LLM call with string-split fallback, not pure LLM. Keep it cheap.
- Synthesis: gate by autonomy level. Never force LLM synthesis on `careful` mode.
- State race: asyncio.Lock is sufficient since the concierge is single-process async.

## Estimate

~2.5 days
