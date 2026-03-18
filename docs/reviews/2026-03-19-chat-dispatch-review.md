# Chat Dispatch Review

Date: 2026-03-19

Scope reviewed:
- `src/dan/server/chat_manager.py`
- `src/dan/server/concierge/triage.py`
- `src/dan/server/concierge/tier_executors.py`
- related prompt/tool wiring in `src/dan/server/chat/prompts.py`

## Active findings

### P1: `chat_manager` import path is broken by mutation-tool schema construction order

- `src/dan/server/chat/prompts.py:152-178` builds `replace_body_graph_schema` using `expand_pattern_schema` and `apply_skill_schema` before those locals are assigned at `src/dan/server/chat/prompts.py:196-220`.
- `src/dan/server/chat/prompts.py:283` eagerly evaluates `MUTATION_TOOL_SCHEMA = _build_mutation_tool_schema()` during import, so the bad local ordering fails before chat dispatch can even start.
- Repro:
  - `python -c "from dan.server.chat_manager import ChatManager"`
  - `pytest -q tests/test_server/test_chat_manager.py -k text_only_build_messages_adds_no_tools_override -vv`
- Observed failure:
  - `UnboundLocalError: cannot access local variable 'expand_pattern_schema' where it is not associated with a value`
- User impact:
  - the direct backend `chat_manager` path no longer imports cleanly under the focused test harness, so task dispatch can fail before triage, tool selection, or streaming logic runs.

### P2: workflow queries are still broadened into workflow-build dispatch

- `tests/test_concierge/test_triage.py:119-130` shows triage can legitimately return `route.target="workflow"` with `action_hints=["workflow_query"]`.
- `src/dan/server/concierge/tier_executors.py:488-490` maps any `route_target == "workflow"` to `workflow_build` before the plan checks run, even when the route only asked for `workflow_query`.
- `src/dan/server/concierge/tier_executors.py:573-580` also defaults `allow_mutation_tool=True` whenever `route_target == "workflow"` unless metadata explicitly overrides it.
- `src/dan/server/concierge/tier_executors.py:871-875` then treats every `workflow_build` turn as a direct chat-manager execution and skips decomposition.
- Existing tests currently lock in this broadening:
  - `tests/test_concierge/test_tiered_dispatch.py:984-987`
  - `tests/test_concierge/test_tiered_dispatch.py:1966-1974`
- User impact:
  - a user asking about a workflow can be dispatched down the workflow-build lane instead of the normal read/query lane.
  - explicit `ask`/`plan` modes still stay read-only inside `ChatManager`, but they are mis-staged as `workflow_build`.
  - default agent-mode requests are worse: they inherit `allow_mutation_tool=True` even though triage only asked for `workflow_query`.

## Verification notes

- Passing:
  - `pytest -q tests/test_concierge/test_tiered_dispatch.py tests/test_concierge/test_triage.py`
  - `pytest -q tests/test_server/test_chat_integration.py`
- Failing:
  - `pytest -q tests/test_server/test_chat_manager.py -k text_only_build_messages_adds_no_tools_override -vv`

The passing concierge and chat-integration suites suggest the main dispatch logic is still mostly wired, but the two issues above are real regressions in the chat-manager/backend handoff.
