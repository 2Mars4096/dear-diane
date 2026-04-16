# 53: DAN Code Project Planner Loop

**Status:** completed
**Goal:** Add a durable project-planner universal agent above bounded DAN Code runs so broad coding objectives are decomposed into persisted milestone slices before execution.

## Tasks
- [x] 1. Add a reusable project-planner controller on the same universal-worker seam as the durable DAN Code orchestrator
  - [x] 1-1. Define typed milestone-plan contracts plus tolerant normalization/fallback behavior
  - [x] 1-2. Keep the planner on `DurableAgentRunner` + `ProviderCompletionAdapter` instead of inventing a DAN Code-only control substrate
- [x] 2. Thread the planner through the DAN Code product shell
  - [x] 2-1. Persist planner session state and the current milestone plan inside `.dan-code/session.json`
  - [x] 2-2. Route coding turns through the planner before the existing bounded coding organism runs
- [x] 3. Lock the new planner layer with focused regressions
  - [x] 3-1. Add controller-level coverage for planner fallback and durable mailbox routing
  - [x] 3-2. Add CLI coverage proving the planner-selected milestone objective is what the bounded coding organism actually executes

## Decisions
- The project planner is a product-layer controller above the bounded coding organism, not a universal substrate rewrite.
- The planner only chooses the next milestone per turn. It does not yet auto-run every milestone in one uninterrupted long-project loop without user continuation.
- The existing bounded coding organism remains the execution engine for one slice at a time; the planner only narrows broad requests into the next bounded slice.

## Notes
- `src/dan/worker/organisms/coding_conversation.py` now defines `CodingProjectPlannerController`, `CodingProjectPlan`, `CodingProjectMilestone`, and `CodingProjectPlannerContext`.
- `src/dan/cli/code.py` now persists planner state under `orchestrator_state["project_planner_session"]` and `orchestrator_state["project_plan"]`, then feeds the planner-selected `active_objective` into the existing bounded coding supervision loop.
- Continue-style turns can now reuse a saved milestone plan and hand the next bounded slice to the same coding organism repair loop instead of re-running the full broad objective unchanged.
- Live reruns after landing this plan showed the intended control-path improvement: heavy prompts such as `codemodx`, `taskforge`, and `termboard` now decompose into four-milestone plans with narrowed first-slice objectives instead of passing the raw broad prompt straight into coding.
- Those same reruns also showed the remaining gap clearly: the worker still tends to stall inside the first milestone by reading nonexistent files in an empty workspace and falling back to extra `web_search`, so the planner alone does not yet move the heavy-task pass/fail boundary end to end.
- A later lightweight follow-up tightened read-only worker guidance in the coding organism/runtime. That was enough for `codemodx` to cross from worker discovery into aggregation/materialization on a fresh heavy rerun, but it did not generalize across the heavier set (`taskforge` and `termboard` still stalled in the worker loop).
- A later runtime follow-up added a stricter read-only no-progress guardrail in `local_runtime.py`: once a `coding_worker` with read-only tools confirms an effectively empty workspace and then probes `.dan-code` or another missing path, the tool loop now disables further tools and forces one final structured response. Focused runtime regressions cover both the internal-state and missing-path variants. Live confirmation at `/tmp/dan-live-taskforge-guardrail-confirm-3` proved the guardrail now appears in the event log (`toolloop.read_only_finalize_forced`, then `tool_count=0`) and lets aggregation materialize the first milestone (`database.py`, `models.py`).
- That same live run exposed the next failure boundary: repair attempt 2 could acknowledge the validator's missing API/tests/docs in `public_response` while falling back to a worker brief for the original milestone. The coding organism now uses the validator `repair_brief` as the deterministic fallback objective when a repair orchestrator response omits usable worker briefs; focused regressions cover that fallback.
- The later frozen-pack full rerun (`codemodx`, `taskforge`, `termboard`) confirmed the lower guardrail does fire in real heavy runs: `codemodx` forced-finalized after an empty-workspace + `.dan-code` probe, `taskforge` forced-finalized after an empty-workspace + generic `web_search`, and `termboard` stalled one round earlier after reading the reviewer checklist. But none of the three runs materialized project files before interruption. All three were ultimately waiting inside provider completion calls when stopped, so the next boundary sits above the planner and above the lower worker guardrail: DAN Code still needs bounded completion latency / quiet-period observability on the live coding path itself.
- Validation:
  - `PYTHONPATH=src python -m py_compile src/dan/worker/organisms/coding_conversation.py src/dan/worker/organisms/__init__.py src/dan/cli/code.py tests/test_worker/test_coding_conversation.py tests/test_cli/test_code.py`
  - `PYTHONPATH=src pytest -q tests/test_worker/test_coding_conversation.py tests/test_cli/test_code.py` (`48 passed`)
