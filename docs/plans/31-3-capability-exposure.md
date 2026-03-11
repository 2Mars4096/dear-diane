# 31-3: Capability Exposure

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Expose 13 built-in tools as chat capabilities, add workflow introspection tools, and provide a single env var to enable all safe learning features.

## Problem

Thirteen tools in `src/dan/tools/` are built and discoverable via `get_all_tools()` but have no capability handlers, so they are invisible to chat. Workflow debugging features (variable inspector, node test cases) exist as REST endpoints but cannot be invoked from chat. Six self-evolvement features are gated by separate env vars defaulting to 0, making activation cumbersome.

## Tasks

- [x] 1. Expose 13 built-in tools as chat capabilities
  - [x] 1-1. Add `handle_python_eval` in `src/dan/server/capability_handlers.py` — wrap `dan.tools.python_eval.python_eval`, format result as `CapabilityResult`. Register in `register_base_capabilities` (or new `register_tool_capabilities`) for modes `["agent", "debug"]`.
  - [x] 1-2. Add `handle_csv_read` — wrap `dan.tools.csv_read.csv_read`, resolve path via `_resolve_user_path`. Register for `["agent", "conversation"]`.
  - [x] 1-3. Add `handle_compress` — wrap `dan.tools.compress.compress`. Register for `["agent"]` only (write tool).
  - [x] 1-4. Add `handle_file_copy`, `handle_file_move`, `handle_file_delete` — wrap respective tools from `dan.tools`. Register for `["agent"]` only (destructive).
  - [x] 1-5. Add `handle_git_status`, `handle_git_diff`, `handle_git_log` — read-only git tools. Register for `["agent", "ask", "debug"]`.
  - [x] 1-6. Add `handle_git_branch`, `handle_git_commit`, `handle_git_worktree` — write git tools. Register for `["agent"]` only.
  - [x] 1-7. Add `handle_notify` — wrap `dan.tools.notify.notify`. Register for `list(ALL_MODES)` (all modes).
  - [x] 1-8. Add `handle_text_diff` — wrap `dan.tools.text_diff.text_diff`. Register for `["agent", "ask", "debug"]`.
  - [x] 1-9. For each handler: define `*_CAPABILITY_SCHEMA` via `build_tool_schema()` using `TOOL_METADATA` from the tool module where applicable; follow existing pattern (wrap tool, catch exceptions, return `CapabilityResult`).
- [x] 2. Workflow introspection tools
  - [x] 2-1. Add `inspect_node` capability tool: params `workflow_id`, `node_id`, optional `run_id`. Use `ctx.graph_store` to load graph; call `compute_upstream_variables` from `dan.server.variable_inspector`; return node config, input ports, output ports, connected edges, upstream variables. Register for `["agent", "ask", "debug"]`.
  - [x] 2-2. Add `list_test_cases` capability tool: params `workflow_id`, `node_id`. Use test case store (inject via `CapabilityContext` or access via app-level store). Return list of case IDs and summaries. Register for `["agent", "ask", "debug"]`.
  - [x] 2-3. Add `run_test_case` capability tool: params `workflow_id`, `node_id`, `case_id`. Invoke same logic as `POST /api/test-cases/{workflow_id}/{node_id}/{case_id}/run` — load case, build synthetic graph, run via `ctx.run_manager`, return pass/fail and diff. Register for `["agent", "debug"]` only (write/execution).
  - [x] 2-4. Add `test_case_store: Any = None` to `CapabilityContext` in `capability_registry.py` and populate it in `app.py` lifespan so the introspection handlers can access it.
- [x] 3. Learning feature activation
  - [x] 3-1. Add `DAN_LEARNING_MODE=1` bundle env var. When set, if individual vars are not already set, set: `DAN_PROMPT_OPTIMIZATION=1`, `DAN_MODEL_LEARNING=1`, `DAN_TOPOLOGY_LEARNING=1`, `DAN_SKILL_LEARNING=1`. Do NOT auto-set `DAN_MEMORY_EXTRACTION_LLM` or `DAN_MEMORY_DUAL_WRITE` (external dependencies).
  - [x] 3-2. Wire bundle into startup: in `app.py` lifespan (early, before RunManager/Concierge init), if `DAN_LEARNING_MODE=1`, apply the four vars via `os.environ.setdefault()` so explicit user overrides are preserved.
  - [x] 3-3. Log which learning features are active at startup (e.g. `logger.info("Learning features: prompt_opt=%s model=%s topology=%s skill=%s", ...)`).
- [x] 4. `DAN_FULL_TOOLS` env var gate
  - [x] 4-1. In the tool registration path (e.g. `register_tool_capabilities()` or `register_base_capabilities()` in `capability_handlers.py`), gate the 13 new tool handlers behind `DAN_FULL_TOOLS=1`. When `0` (default), only the existing capability set is registered — backward compatible.
  - [x] 4-2. When `DAN_FULL_TOOLS=1`, register all 13 handlers from task 1. This is a one-line gate per `register_*` call.
  - [x] 4-3. Log which tools are registered at startup when `DAN_FULL_TOOLS=1`.
- [x] 5. Tests
  - [x] 5-1. Add tests for each new capability handler in `tests/test_server/test_capability_exposure.py`: call with valid args and assert success; call with invalid/missing args and assert error.
  - [x] 5-2. Add test for `DAN_LEARNING_MODE` bundle: mock env, set `DAN_LEARNING_MODE=1`, run startup logic, assert the four vars are set; test that explicit `DAN_PROMPT_OPTIMIZATION=0` is preserved.
  - [x] 5-3. Add tests for introspection tools: mock `graph_store` with a minimal graph, mock `test_case_store`, call `inspect_node`, `list_test_cases`, `run_test_case` and assert expected structure.
  - [x] 5-4. Add test for `DAN_FULL_TOOLS` gate: when `0`, assert new tools not registered; when `1`, assert all 14 registered.

## Mode Summary

| Tool | Modes |
|------|-------|
| python_eval | agent, debug |
| csv_read | agent, conversation |
| compress | agent |
| file_copy, file_move, file_delete | agent |
| git_status, git_diff, git_log | agent, ask, debug |
| git_branch, git_commit, git_worktree | agent |
| notify | all |
| text_diff | agent, ask, debug |
| inspect_node, list_test_cases | agent, ask, debug |
| run_test_case | agent, debug |

## File Paths

- `src/dan/server/capability_handlers.py` — handlers and schemas
- `src/dan/server/capability_registry.py` — `READ_ONLY_MODES`, `ALL_MODES`, `build_tool_schema`
- `src/dan/server/app.py` — lifespan (DAN_LEARNING_MODE), `_graph_store`, `_test_case_store`
- `src/dan/server/variable_inspector.py` — `compute_upstream_variables`
- `src/dan/tools/*.py` — tool implementations
- `tests/test_server/test_capability_handlers.py` — capability tests (or create if missing)

## Decisions

- (filled in during execution)

## Notes

- `CapabilityContext` currently has `graph_store`, `run_manager`; `test_case_store` may need to be added. Check `app.py` for how capability context is constructed.
- Tool modules use `TOOL_METADATA` with `tool_id`, `description`, `parameters`, etc. — reuse for schema where possible.

## Estimate

~1.5 days
