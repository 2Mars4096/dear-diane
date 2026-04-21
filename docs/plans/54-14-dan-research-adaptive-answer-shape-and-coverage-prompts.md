# 54-14: DAN Research Adaptive Answer Shape and Coverage Prompts

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Make DAN Research choose answer shape and coverage from the user query at the orchestration seam instead of implying one fixed visible report template.

## Tasks
- [x] 1. Move answer-shape guidance into the durable research orchestration prompts.
  - [x] 1-1. Extend the intention-plan seam with explicit answer-shape and coverage-priority fields.
  - [x] 1-2. Update the planner/orchestrator/deep-research prompt text so models ask what format best serves the query and what content is actually required.
- [x] 2. Keep the adaptive answer-shape choice live through bounded execution.
  - [x] 2-1. Forward planner-selected answer shape and coverage priorities through CLI planning payloads and bounded-run delivery targets.
  - [x] 2-2. Add a focused regression proving the bounded run uses the planner-selected answer shape instead of the default `research memo`.
- [x] 3. Update DAN Research docs and tracking notes.
  - [x] 3-1. Record the prompt-level adaptive-answer-shape behavior in README and architecture docs.
  - [x] 3-2. Record the completed work in todo/changelog and the fixed-approach note in bugs.

## Decisions
- Kept structured verification/audit/integrity fields as internal accountability outputs rather than removing them from the product contract.
- Treated answer shape and coverage priorities as synthesis cues chosen from the query, not as another rigid user-facing format contract.

## Notes
- Validation: `python -m py_compile src/dan/cli/research.py src/dan/worker/organisms/research_conversation.py src/dan/worker/organisms/project_execution.py src/dan/worker/organisms/reference_demo.py tests/test_worker/test_research_conversation.py tests/test_cli/test_research.py`
- Validation: `PYTHONPATH=src pytest -q tests/test_worker/test_research_conversation.py tests/test_cli/test_research.py` (`60 passed`)
