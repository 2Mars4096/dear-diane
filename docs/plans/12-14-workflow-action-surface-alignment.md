# 12-14: Workflow Action Surface Alignment

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** completed
**Goal:** Keep ordinary workflow delete/run/schedule follow-ups on DAN's native workflow surfaces instead of drifting into unavailable tools, Furnace control, or shell/cron advice.

## Tasks
- [x] 1. Make missing-action retry guidance aware of the actual tool surface for the current turn.
  - [x] 1-1. Stop retry prompts from instructing `start_run` / `http_request` when those tools are not actually exposed.
  - [x] 1-2. Add regressions for unavailable vs available workflow-run guidance.
- [x] 2. Separate ordinary workflow execution from Furnace run control earlier in routing.
  - [x] 2-1. Remove `run_control` from ordinary workflow-run lexical follow-ups.
  - [x] 2-2. Align embedding/fallback run hints so non-Furnace workflow runs prefer `workflow_run`.
  - [x] 2-3. Update focused triage/tiered-dispatch regressions.
- [x] 3. Add a native bridge for conversational workflow scheduling follow-ups.
  - [x] 3-1. Detect clear natural-language scheduling turns for the current workflow.
  - [x] 3-2. Route those turns through the existing `/schedule workflow ...` command handler instead of shell/cron prose.
  - [x] 3-3. Add fast-command/runtime regressions for linked-workflow schedule follow-ups.

## Decisions
- Ordinary workflow execution should use `start_run`; `run_control` remains reserved for explicit Furnace/session lifecycle requests.
- Retry prompts must reflect the tools actually present in the provider request for that turn.
- Workflow scheduling should reuse the existing scheduler command path and persistence model instead of introducing a shell fallback.

## Notes
- Motivating failures came from the 2026-03-30 equity-workflow chat where the model truthfully lacked `start_run` in a read-only turn, then received contradictory retry guidance and fell back to shell/cron advice.
- Landed in `chat/helpers.py`, `chat_manager.py`, `concierge/triage.py`, `concierge/triage_scenarios.py`, and `concierge/runtime/__init__.py`.
- Validation: `pytest -q tests/test_concierge/test_triage.py tests/test_concierge/test_tiered_dispatch.py` (`130 passed`), `pytest -q tests/test_concierge/test_fast_commands.py -k 'schedule_workflow_current_uses_linked_project_workflow_without_metadata or nl_workflow_schedule_followup'` (`4 passed, 36 deselected`), and `pytest -q tests/test_server/test_multi_turn_tools.py -k 'missing_action_prompt_skips_start_run_when_unavailable or missing_action_prompt_mentions_start_run_when_available'` (`2 passed, 46 deselected`).
- Follow-up hardening on the same day: mixed delete/edit/schedule turns now execute the embedded scheduling clause first, carry the schedule result forward as routing context, and continue the remaining clauses through the normal workflow tool path. Focused validation: `pytest -q tests/test_concierge/test_fast_commands.py -k 'nl_workflow_schedule_followup'` (`4 passed, 37 deselected`), `pytest -q tests/test_concierge/test_triage.py -k 'workflow_run_over_run_control or mixed_workflow_followup_on_workflow_edit'` (`2 passed, 42 deselected`), and `pytest -q tests/test_concierge/test_tiered_dispatch.py -k 'workflow_followup or workflow_action_followup or workflow_apply_followup or workflow_run'` (`4 passed, 83 deselected`).
- Revalidated after the accidental checkout/restore audit on 2026-03-30: the restored code paths are present in `chat/helpers.py`, `chat_manager.py`, `concierge/triage.py`, `concierge/triage_scenarios.py`, `concierge/runtime/__init__.py`, and `concierge/tier_executors.py`. Current focused validation is green: `pytest -q tests/test_server/test_multi_turn_tools.py -k 'missing_action_prompt_skips_start_run_when_unavailable or missing_action_prompt_mentions_start_run_when_available'` (`2 passed, 46 deselected`), `pytest -q tests/test_concierge/test_triage.py -k 'workflow_run_followup or workflow_run_over_run_control or mixed_workflow_followup_on_workflow_edit'` (`3 passed, 41 deselected`), `pytest -q tests/test_concierge/test_fast_commands.py -k 'schedule_workflow_current_uses_linked_project_workflow_without_metadata or nl_workflow_schedule_followup'` (`5 passed, 36 deselected`), and `pytest -q tests/test_concierge/test_tiered_dispatch.py -k 'workflow_followup or workflow_action_followup or workflow_apply_followup or workflow_run'` (`4 passed, 83 deselected`).
