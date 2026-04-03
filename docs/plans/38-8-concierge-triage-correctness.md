# 38-8: Concierge Triage & Dispatch Correctness

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** completed, including the later 2026-04-03 follow-up tightening *(synced 2026-04-03)*
**Goal:** Fix correctness bugs and quality gaps in the concierge triage pipeline and tier executor dispatch identified in the 2026-03-19 concierge triage review.

## Context

- Originally, the triage LLM was configured with `max_tokens=60` for a response requiring 13 JSON fields — this almost certainly caused frequent truncation, falling back to the heuristic path with 0.50-0.58 confidence.
- The `Session` model's `child_execution` field is `Literal["parallel", "serial"]` but the dispatcher assigns `"mixed"`, creating a type mismatch that Pydantic may silently accept or reject depending on validation mode.
- `asyncio.run()` is called from within background tasks on the event loop in two locations.
- The plan decomposition stub splits on `" and "`, tier-2 synthesis is mechanical concatenation, and several other dispatch correctness gaps exist.

## Tasks

### 1. Increase triage `max_tokens` (P1 — highest impact)
- [x] 1-1. Increase `runtime.py:409` `max_tokens` from `60` to at least `256` (a minimal valid 13-field JSON response is ~100-150 tokens). *(fixed 2026-03-19: changed to 256; bumped to 512 on 2026-04-03 after the later review follow-up; raised again to 1024 on 2026-04-03 once we decided triage should have a normal worker-sized completion ceiling rather than a classifier-tight budget.)*
- [x] 1-2. Verify that the higher token budget does not meaningfully increase triage latency (the response is still short JSON). *(2026-03-19: verified by analysis — `max_tokens` is an upper bound, not a generation target. The model stops at natural EOS after ~100-150 tokens of JSON; raising the cap from 60 to 256 prevents truncation without changing output length or latency. 2026-04-03 follow-up: the same argument still applies at 512 and 1024; the ceiling is intentionally generous because the classifier now also sees richer task/anaphora context and occasionally needs more room for entities/rationale.)*
- [x] 1-3. Add a regression test confirming triage JSON can be parsed at the new limit. *(2026-03-19: added a `Concierge._triage_llm_complete()` regression; updated 2026-04-03 so it now asserts `max_tokens=1024` is used through the real triage path.)*

### 2. Fix `child_execution` literal type mismatch
- [x] 2-1. Expand `Session.child_execution` to `Literal["parallel", "serial", "mixed"]` in `session.py:102`. *(fixed 2026-03-19)*
- [x] 2-2. Add handling for `"mixed"` in `MultiStepExecutor` (currently falls through to serial behavior — make this explicit). *(fixed 2026-03-19: `MultiStepExecutor` now logs and normalizes `mixed` to the current serial-safe fallback instead of relying on an implicit else branch)*

### 3. Fix `asyncio.run()` inside running event loop
- [x] 3-1. Replace `asyncio.run(_call())` at `runtime.py:1402` with proper thread-safe async bridging (e.g., `asyncio.run_coroutine_threadsafe()` on the main loop, or restructure to stay async).
- [x] 3-2. Fix the same pattern at `runtime.py:1753` for memory extraction.
- [x] 3-3. Add a test that the domain reflection and memory extraction paths work from within an already-running event loop. *(2026-03-19: added `_run_coroutine_sync()` regression in `tests/test_concierge/test_unified_queue.py`)*

### 4. Deduplicate fast-classification path
- [x] 4-1. Remove the duplicate `fast_classify_text()` call in `_do_triage()` or ensure the fast result goes through `_post_process_triage_result` before returning. *(2026-03-19: dispatcher duplicate removed; runtime fast path now owns social short-circuiting)*
- [x] 4-2. Add a test confirming social messages get consistent post-processing regardless of entry path. *(covered by the existing tiered-dispatch social fast-path regression after the runtime fast-path fix)*

### 5. Replace naive plan decomposition stub
- [x] 5-1. Replace the `task.split(" and ")` stub in `tier_executors.py:1184-1189` with a lightweight LLM call that decomposes the task into subtasks. *(2026-03-19: `MultiStepExecutor` now uses a capped direct provider completion to request JSON subtask decomposition when tier-2 sessions look decomposable but triage omitted explicit subtasks.)*
- [x] 5-2. Add a token budget cap on the decomposition call to keep it cheap. *(2026-03-19: decomposition now goes through `_cheap_llm_complete()` — fixed caps removed later in favor of provider natural limits since `max_tokens` is just an upper bound.)*
- [x] 5-3. Keep the string split as a no-LLM fallback when the model is unavailable. *(2026-03-19: the old `and`-split logic lives on as `_fallback_split_task()` and is used whenever providers are unavailable, resolution fails, or the LLM output is unparseable.)*

### 6. Add LLM-powered tier-2 synthesis
- [x] 6-1. After collecting child results in `MultiStepExecutor._synthesize()`, add an optional LLM synthesis pass that produces a coherent unified response instead of concatenated sections. *(2026-03-19: final tier-2 synthesis now first attempts `_maybe_llm_synthesize()` with a lightweight direct provider completion and falls back to the existing deterministic section concatenation.)*
- [x] 6-2. Gate behind autonomy level: `aggressive` always synthesizes, `balanced` synthesizes for 3+ children, `careful` concatenates only. *(2026-03-19: implemented in `_should_llm_synthesize()`.)*
- [x] 6-3. Keep concatenation as the fallback when LLM synthesis fails or is disabled. *(2026-03-19: provider failures or missing providers now leave the existing deterministic `## child` concatenation path intact; regressions cover both the LLM path and fallback path.)*

### 7. Fix volatile concierge state race condition
- [x] 7-1. Add an `asyncio.Lock` per surface key on `_volatile_concierge_states` to serialize read-modify-write cycles.
- [x] 7-2. Add a test with concurrent dispatches to the same surface confirming no state corruption. *(2026-03-19: added a concurrent `_process_inner()` regression proving same-surface updates are serialized and both state mutations persist)*

### 8. Refine child session route inheritance
- [x] 8-1. In `tier_executors.py`, filter `action_hints` when creating child sessions so research subtasks don't inherit `workflow_edit` hints.
- [x] 8-2. Only propagate action hints that are relevant to the child's decomposed task, not the parent's full route.

### 9. Expand triage context window
- [x] 9-1. Increase from 4 turns × 200 chars to at least 6 turns × 400 chars for the triage context.
- [x] 9-2. Coordinate with task 1 (`max_tokens` increase) since the LLM needs enough context to classify correctly.

### 10. Fix `_finalize_task` completion marking
- [x] 10-1. Distinguish between `completed` (task fully done) and `partial` / `interrupted` when content was produced but the task was not fully finished. *(2026-03-19: `SessionResult.metadata["completion_status"]` now records `completed`, `interrupted`, or `cancelled`; direct single-shot/multi-step executor paths no longer mark an interrupted terminal stream as completed.)*
- [x] 10-2. Use the existing `SessionState` transitions to inform the final task status. *(2026-03-19: tiered-dispatch finalization now checks both `session.state` and `completion_status` so interrupted/cancelled sessions map to paused task status instead of being treated as fully completed.)*

## Primary Files

- `src/dan/server/concierge/runtime.py`
- `src/dan/server/concierge/triage.py`
- `src/dan/server/concierge/tiered_dispatch.py`
- `src/dan/server/concierge/tier_executors.py`
- `src/dan/server/concierge/session.py`

## Decisions

- `max_tokens=1024` is the current triage cap. The JSON response is still usually much shorter, but the higher ceiling gives the classifier room for richer context, entities, and rationale without adding a second triage call or forcing the prompt back into cramped classifier-only assumptions.
- Plan decomposition: LLM call with string-split fallback, not pure LLM. Keep it cheap.
- Synthesis: gate by autonomy level. Never force LLM synthesis on `careful` mode.
- State race: asyncio.Lock is sufficient since the concierge is single-process async.

## Notes

- Added focused regressions for widened triage context, filtered child-route inheritance, and loop-safe async bridging in `tests/test_concierge/test_triage.py`, `tests/test_concierge/test_tiered_dispatch.py`, and `tests/test_concierge/test_unified_queue.py`.
- `tests/test_concierge/test_unified_queue.py` now also covers the widened triage token budget through the real `Concierge._triage_llm_complete()` path and a same-surface concurrent dispatch/state-persistence scenario.
- `tests/test_concierge/test_tiered_dispatch.py` now also covers LLM-backed tier-2 decomposition, decomposition fallback when providers are unavailable, autonomy-gated LLM synthesis, synthesis fallback on provider failure, and interrupted terminal events preserving non-completed session status.

### 2026-04-03 review follow-up sync

- Narrowed lexical over-match in `triage_scenarios.py` so bare freshness/file nouns do not steal routing from the LLM path.
- Expanded triage prompt context again with current task state plus recent assistant snippets for pronoun-heavy turns.
- Gated workflow edit/run post-processing on route confidence so confident semantic routes from both LLM and embedding stages are trusted.
- Added a low-confidence clarification stop in `tiered_dispatch.py`, then narrowed it so only action-like execution routes pause; plain low-confidence `ask/general` turns still answer normally.
- Revalidated the focused follow-up slices in `tests/test_concierge/test_triage.py`, `tests/test_concierge/test_tiered_dispatch.py`, `tests/test_concierge/test_pending_actions.py`, and `tests/test_concierge/test_unified_queue.py`.

## Follow-Up Tightening Scope (2026-03-21)

The original 38-8 work fixed dispatch correctness. A separate follow-up is still needed to make the tiered concierge feel intentionally different across execution layers instead of merely using different models. Today the system has:

- a dedicated triage/classification prompt in `triage.py`,
- a shared execution prompt path through `ChatManager` / `UNIFIED_SYSTEM_PROMPT`,
- small helper prompts for decomposition and synthesis in `tier_executors.py`,
- stage-to-model mapping in `tiering.py`,

but not a strong stage-specific prompt policy for root execution behavior.

### Deferred follow-up tasks

Tracked workflow follow-ups:

- [38-15: Lexical Triage Scenario Catalog](38-15-lexical-triage-scenario-catalog.md)
- [38-16: Workflow Build Contract & Repair Hardening](38-16-workflow-build-contract-and-repair.md)
- [38-17: Trace-to-Workflow Distillation](38-17-trace-to-workflow-distillation.md)

- [x] 11. Replace ad-hoc regex/keyword routing with an explicit lexical scenario catalog plus mandatory LLM fallback
  - [x] 11-1. Inventory every current lexical/regex determinant in `triage.py` and group them into named scenarios rather than free-floating regexes.
  - [x] 11-2. For each scenario, document: trigger examples, negative examples, intended route/action hints, confidence level, and precedence relative to other scenarios.
  - [x] 11-3. Add explicit ambiguity classes that MUST bypass lexical routing and invoke LLM triage: overlapping workflow/file/run cues, anaphora without strong recent workflow activity, mixed edit/query language, retry/follow-up wording without a stable referent, and turns containing both operational and informational intents.
  - [x] 11-4. Remove broad “catch too much” patterns in favor of narrower scenario-specific patterns with tests for false positives.
  - [x] 11-5. Record routing provenance explicitly (`fast_lexical`, `embedding`, `llm`, `heuristic_fallback`) plus the matched scenario ID and confidence when lexical routing wins.
  - [x] 11-6. Add regression fixtures for the common failure cases: “try again”, “apply it”, “run it”, “check this file”, “status of the workflow”, and mixed workflow + web/file turns.

- [x] 12. Add stage-specific execution prompt overlays on top of the shared DAN base prompt. *(2026-03-25 sync: `_extract_chat_params()` now layers a stage overlay on top of the shared prompt, with `_determine_stage()` selecting from the centralized `_STAGE_PROMPT_OVERLAYS` registry; workflow-facing turns also pick up the shared workflow-generation contract module instead of bloating the overlay text itself.)*
  - [x] 12-1. Keep one shared non-negotiable base prompt for safety/tool honesty, but add narrow stage overlays selected from `_determine_stage(session)`.
  - [x] 12-2. Introduce one explicit stage-overlay registry/module map instead of scattering small prompt conditionals across executor code.
  - [x] 12-3. `conversation` / single-shot ask path: broad generalist prompt, concise, minimal orchestration language, no heavy workflow assumptions.
  - [x] 12-4. `conversation_plan`: orchestration-first prompt that focuses on decomposition, scope control, explicit assumptions, and deciding whether work should stay conversational vs become a workflow.
  - [x] 12-5. `workflow_build`: practical contract-first prompt that emphasizes current workflow identity, canonical node kinds, exact port/schema compatibility, explicit "proposed vs applied" language, and validation before claiming success.
  - [x] 12-6. `direct_task` / `file_review`: execution-focused prompt that prefers concrete actions and summaries over meta-planning.
  - [x] 12-7. Define stage ownership explicitly: root `tier=2` sessions orchestrate, while child sessions or specialized stages perform detailed execution. Avoid stuffing worker detail into the root prompt.
  - [x] 12-8. Keep `workflow_build` narrow: it should supervise workflow authoring and validation, not become a generic catch-all for any turn that happens to mention a workflow.

- [x] 13. Separate tier semantics from role semantics. *(2026-03-25 final sync: stage now drives prompt overlay/persona while `tiering.py` maps stage to budget/model, and the root-vs-child scope split is now documented explicitly below.)*
  - [x] 13-1. Treat tier as complexity/budget, not as the sole source of prompt behavior.
  - [x] 13-2. Tier-2 root sessions should act as orchestrators by default; detailed/practical work should be pushed into child tasks or stage-specific execution prompts rather than bloating the root prompt.
  - [x] 13-3. Document the intended behavior matrix: `tier` decides depth and budget; `stage` decides prompt persona and operating style.
  - [x] 13-4. Add an explicit root-vs-child scope table so the system does not mix planner, concierge, worker, and build-validator responsibilities in one prompt surface.
  - [x] 13-5. Make the workflow-facing split explicit:
    - root concierge decides route, scope, and whether the turn is build/query/run
    - `workflow_build` produces or mutates a candidate graph
    - the build contract validator determines validated vs run-ready

- [x] 14. Make stage/tier/route behavior observable. *(2026-03-25 follow-up: turn-level telemetry now carries concierge stage/tier/prompt-overlay/routing provenance, `TelemetryStore.aggregate()` can group by those metadata keys, and `/analytics` now exposes a narrow reporting surface for over-time review.)*
  - [x] 14-1. Persist `concierge_stage`, `session_tier`, selected prompt overlay/module, lexical scenario ID, and routing provenance in telemetry or another queryable store.
  - [x] 14-2. Extend analytics so stage/tier/routing quality can be reviewed over time instead of inferred from logs.

### Follow-up decisions

- **Regexes should not be trusted as the final authority.** Lexical matching should only win for explicit, well-scoped scenarios; ambiguous matches must escalate to LLM triage. The concrete follow-up is tracked in `38-15`.
- **Do not create one monolithic prompt per tier.** Keep one shared DAN base prompt and layer small stage-specific overlays on top.
- **Tier 2 root should be more orchestration-heavy, not more implementation-heavy.** Practical/detail work belongs in child tasks or explicit build/file-review stages.
- **Workflow build needs a stricter contract than general chat.** It should optimize for structural correctness, validation, and truthful status reporting, not broad conversational helpfulness.
- **Tiered concierge scope should be narrow and role-based.** The root concierge should decide, route, and supervise; specialized build/file/direct-task paths should execute.

### 2026-03-25 status sync

- `src/dan/server/concierge/tier_executors.py` now centralizes the stage registry, stage resolution, overlay injection, and audit metadata threading.
- `src/dan/server/concierge/tiering.py` now makes the stage-to-tier-to-model split explicit instead of deriving prompt behavior directly from tier.
- `src/dan/server/concierge/tiered_dispatch.py`, `src/dan/server/concierge/runtime/__init__.py`, and `src/dan/server/concierge/session.py` now persist stage/tier/route/scenario provenance into user-turn metadata, turn telemetry, completion telemetry, session traces, and exported trees.
- `src/dan/server/telemetry.py` now allows aggregate rollups by concierge metadata keys (`concierge_stage`, `session_tier`, `prompt_overlay`, `route_source`, `scenario_id`, `scenario_confidence`) instead of only top-level event columns.
- `src/dan/server/concierge/command_registry.py` and `src/dan/server/concierge/runtime/__init__.py` now expose a narrow `/analytics` fast command for day-by-stage / tier / route review over recent `chat_turn` telemetry.
- 2026-03-25 follow-up: unified telemetry now also records `gateway_call` events plus explicit `model_used`, `chat_mode`, and `hour` rollups, `src/dan/server/gateway/router.py` exposes `/api/gateway/analytics/telemetry`, and the editor token analytics panel now shows a compact 24-hour telemetry strip for recent model/mode/hour activity.
- Focused regressions already cover stage resolution, overlay selection, provenance persistence, telemetry metadata, and workflow-build narrowing in `tests/test_concierge/test_tiered_dispatch.py`, `tests/test_concierge/test_tiering.py`, and `tests/test_concierge/test_session_manager.py`.

### Root-vs-Child Scope Table

| Surface / Role | Primary responsibility | Must decide | Must not own |
| --- | --- | --- | --- |
| Root concierge session | Orchestration, routing, scope control, approvals, progress, and cross-turn supervision | whether the turn is conversational vs delegated, build vs query vs run, when to spawn child work, and what evidence/status to report back | detailed worker implementation, low-level file review minutiae, or pretending validation/execution already happened |
| Child direct-task / file-review session | Practical execution for a scoped subtask | concrete local actions, focused findings, and concise completion/error reporting for its assigned slice | global routing policy, multi-turn supervision, or rewriting the parent objective |
| `workflow_build` stage | Author or mutate a candidate workflow under the workflow-generation/build contract | current workflow identity, exact mutation/build shape, proposed vs applied state, and whether the artifact validated | generic orchestration for unrelated tasks, broad worker behavior, or claiming run success without evidence |
| Build-contract validator | Structural validation and run-readiness classification | validated vs repairable vs fatal, plus run-ready vs non-runnable distinctions | routing, decomposition, or speculative execution policy |

This is the intended separation behind the current codebase:

- Root sessions stay orchestration-heavy, matching the concierge narrowing decision in `41-3`.
- Child sessions inherit scoped work envelopes and perform detailed execution instead of broad replanning.
- `workflow_build` remains a specialized authoring surface layered on top of the shared workflow-generation contract, not a general prompt bucket for any workflow mention.
- Validation remains a downstream truth boundary: the concierge or build surface may propose or apply, but only the contract validator determines validated vs run-ready status.

### Implementation split

- `38-15` should stop lexical misroutes before execution starts, especially on workflow follow-up turns.
- `38-8` follow-up should make the stage prompt behavior intentional once a route is chosen.
- `38-16` should become the hard contract boundary for whether a built workflow is merely proposed, actually validated, or truly run-ready.
- `38-17` should let DAN learn reusable workflow drafts from successful audited executions when direct one-shot workflow generation is not the best first step.

## Estimate

~2.5 days
