# 38-15: Lexical Triage Scenario Catalog

**Parent:** [38-review-hardening](38-review-hardening.md)
**Status:** completed
**Goal:** Replace the current ad-hoc regex/keyword routing pile with a small, explicit lexical scenario catalog focused on workflow follow-up turns, easy to tune, hard to over-match, and designed to hand off to LLM triage whenever the lexical result is ambiguous.

## Problem

The current concierge triage logic mixes many free-floating regexes and keyword checks in `triage.py`:

- patterns are individually reasonable but collectively hard to reason about,
- precedence is implicit,
- false positives accumulate around workflow follow-up phrases like `retry`, `apply it`, `run it`, `check this`, `status of the workflow`, `what workflow is this`,
- anaphora and mixed-intent turns often get routed too early by lexical heuristics,
- there is little provenance for why a lexical route won.

The fix should be **concise, not clever**: keep lexical routing only for narrow high-confidence scenarios, and escalate the rest to the LLM path.

## Design

### Core principle

Lexical routing is an **early resolver for explicit scenarios**, not a general classifier.

### Scenario model

Define a compact catalog of `LexicalScenario` entries, each with:

- `id`
- `description`
- `positive_patterns`
- `negative_patterns`
- `required_context`
- `intent`
- `target`
- `action_hints`
- `confidence`
- `examples`
- `anti_examples`

### Evaluation contract

Given a user turn plus recent context, lexical evaluation should return one of:

- `matched_single` — exactly one high-confidence scenario matched; use it
- `ambiguous` — multiple scenarios matched or the scenario requires missing context; escalate to LLM triage
- `no_match` — no scenario matched; continue to embedding/LLM triage

### Initial scenario families

Keep the first version small:

1. `social_ack`
2. `workflow_followup_apply`
3. `workflow_followup_retry`
4. `workflow_run_followup`
5. `workflow_query_status`
6. `workflow_query_identity`
7. `explicit_file_read`
8. `explicit_file_write`
9. `explicit_web_lookup`
10. `explicit_furnace_run_control`

Workflow follow-up scenarios should only win when the required workflow context is actually present. Everything else should fall through.

## Tasks

- [x] 1. Introduce a lexical scenario catalog
  - [x] 1-1. Add `src/dan/server/concierge/triage_scenarios.py` with the scenario model and the initial scenario catalog.
  - [x] 1-2. Move existing regexes/keywords used for fast routing into named scenarios instead of scattered module globals where practical.
  - [x] 1-3. For each scenario, include 2-5 positive examples and at least 2 anti-examples in comments or test fixtures.

- [x] 2. Add a narrow lexical evaluator
  - [x] 2-1. Implement `evaluate_lexical_scenarios(text, context) -> LexicalRouteResult`.
  - [x] 2-2. Make precedence explicit: exact social first, then explicit operational controls, then explicit file/web requests, then workflow follow-ups only when workflow context gates are satisfied.
  - [x] 2-3. Require all scenario winners to pass their `negative_patterns` and `required_context` checks before routing.
  - [x] 2-4. Define workflow context predicates explicitly instead of inferring them ad hoc from scattered helpers: `recent_workflow_activity`, `current_workflow_reference`, `linked_workflow_reference`, `workflow_operation_subject_known`.

- [x] 3. Add mandatory ambiguity escalation
  - [x] 3-1. If more than one non-social scenario matches, do not choose heuristically; escalate to LLM triage.
  - [x] 3-2. If the turn contains anaphora but recent context is weak or conflicting, escalate to LLM triage.
  - [x] 3-3. If a lexical match implies workflow mutation/run control but recent workflow activity is absent, escalate instead of forcing the route.
  - [x] 3-4. If informational and operational cues coexist in the same turn, prefer LLM triage over lexical routing.
  - [x] 3-5. Distinguish workflow query vs workflow edit vs workflow run explicitly; if the same turn looks like more than one of those, lexical routing must not decide it.
  - [x] 3-6. Add explicit overlap classes for the current failure seam: workflow vs file, workflow vs web, workflow query vs workflow mutation, workflow run vs workflow retry/debug.

- [x] 4. Record routing provenance
  - [x] 4-1. Add lexical provenance fields to triage metadata/result handling: `route_source`, `scenario_id`, `scenario_confidence`.
  - [x] 4-2. Thread those fields into telemetry or another queryable surface so routing mistakes can be audited from data instead of logs alone.

- [x] 5. Keep the implementation concise and flexible
  - [x] 5-1. Avoid a large rules engine; use a small table-driven catalog plus a simple evaluator.
  - [x] 5-2. Keep scenario count intentionally low in v1; add new scenarios only when they fix a known failure mode.
  - [x] 5-3. Keep the LLM path authoritative for unclear turns rather than trying to encode every corner case lexically.
  - [x] 5-4. Treat the workflow family as the center of gravity for v1. Generic lexical cleanup is only in scope when it prevents workflow misrouting.

- [x] 6. Add focused regression coverage
  - [x] 6-1. Add table-driven triage tests for known bad phrases: `try again`, `apply it`, `run it`, `check this file`, `status of the workflow`, `what workflow is this`, `search that online`, mixed workflow+file, mixed workflow+web.
  - [x] 6-2. Add tests proving ambiguous lexical matches escalate instead of routing directly.
  - [x] 6-3. Add tests proving negative patterns suppress over-broad matches.
  - [x] 6-4. Add tests for current-workflow context sensitivity: the same phrase with and without recent workflow activity should route differently.

## Primary Files

- `src/dan/server/concierge/triage.py`
- `src/dan/server/concierge/triage_scenarios.py` *(new)*
- `src/dan/server/concierge/runtime.py`
- `src/dan/server/telemetry.py`
- `tests/test_concierge/test_triage.py`

## Decisions

- **Lexical routing stays, but only for explicit high-confidence scenarios.**
- **Ambiguity goes up to the LLM, not sideways into more regex.**
- **Table-driven scenarios beat scattered regex globals.**
- **Keep v1 small.** A concise scenario catalog is easier to trust and tune than a comprehensive heuristic grammar.

## Notes

- This plan intentionally does **not** replace the LLM triage path.
- This plan is compatible with the stage-specific prompt follow-up tracked in 38-8; it only changes how early routing decides when lexical logic is safe to trust.
- Workflow-specific follow-up language is the primary target. Generic regex cleanup is only valuable if it reduces workflow build/query/run misroutes.
- Landed in `triage_scenarios.py`, `triage.py`, `tiered_dispatch.py`, `session.py`, and `tests/test_concierge/test_triage.py`.
- This plan should stay small. The point is to define a trusted catalog for the known workflow follow-up seams, not to encode every possible user utterance lexically.
