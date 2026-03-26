# 42-2: Local Runtime Tool Parity

**Parent:** [42-benchmark-execution-trustworthiness](42-benchmark-execution-trustworthiness.md)
**Status:** completed
**Goal:** Either make local workflow execution register the built-in tools that real workflows need or explicitly de-scope local mode from tool-heavy benchmark runs.

## Problem

There are two distinct "local" paths with different tool registration behavior:

1. **Bare `Engine` path** (`DanClientOrLocal._dispatch_local()` and `cli/run.py _make_engine()`): wires `ToolExecutor()` with an **empty** `ToolRegistry` via `executor_defaults.py`. This is the path `dan-run --local` and the auto-fallback both use for workflow execution.
2. **`build_chat_services` path** (`chat_factory/__init__.py`): calls `_build_tool_registry()` → `register_builtin_tools()` → `get_all_tools()`, which populates `web_search`, `file_read`, `shell_command`, browser tools, git tools, etc. This is the local **chat** bootstrap, not the workflow execution path.

The bare `Engine` path is the one that matters for benchmark workflow execution, and it has an empty tool registry. The `register_common_capabilities()` helper centralizes `ChatCapabilityRegistry` (chat-side tool handlers for concierge), not `ToolRegistry` (workflow node executor tools) — it does **not** close this gap.

Additionally, `DanClientOrLocal.__init__` accepts a `tool_registry` parameter, but `_make_engine()` does not pass it into `Engine` — making it a dead argument.

This plan resolves the ambiguity one way or the other:

- either the bare `Engine` path registers the built-in tool set
- or local mode is clearly documented as unsupported for tool-heavy benchmark workflows

## Tasks

- [ ] 1. Inventory local-vs-server tool differences
  - [ ] 1-1. List the built-in tools from `ToolRegistry.register_builtin_tools()` → `dan.tools.get_all_tools()` that the bare `Engine` path currently omits.
  - [ ] 1-2. List the server-only tools from `startup._build_tool_registry()` → `register_server_tools()` (e.g. `save_paper`, `search_papers`, `run_python`, `rag_index_documents`) plus custom tools from `DAN_CUSTOM_TOOLS_DIR`.
  - [ ] 1-3. Identify which workflow classes depend on built-in vs server-only tools.
  - [ ] 1-4. Decide which tools must be parity-complete for benchmark-grade local runs.

- [ ] 2. Choose one of two valid end states
  - [ ] 2-1. Register the built-in tool set in the bare `Engine` path (via `_make_engine` in `local.py` and `run.py`), or
  - [ ] 2-2. De-scope local mode and update docs/tests accordingly.
  - [ ] 2-3. If parity is chosen, wire `DanClientOrLocal._tool_registry` into `_make_engine()` (currently a dead parameter).
  - [ ] 2-4. Do not leave the system in a half-supported state.

- [ ] 3. Add regressions
  - [ ] 3-1. Test that representative workflows can resolve required tools locally if parity is chosen.
  - [ ] 3-2. Test that tool-heavy workflows fail clearly if local mode is explicitly unsupported.
  - [ ] 3-3. Prevent silent success on workflows that are only partially wired.

## Likely Files

| File | Why |
|------|-----|
| `src/dan/executor_defaults.py` | Shared executor registration — currently constructs `ToolExecutor()` with empty registry |
| `src/dan/executors/tool.py` | `ToolExecutor` and `ToolRegistry` — `register_builtin_tools()` is the method that populates built-ins |
| `src/dan/tools/__init__.py` | `get_all_tools()` — the canonical built-in tool list |
| `src/dan/client/local.py` | `_make_engine()` — needs to accept and pass through a populated `ToolRegistry` |
| `src/dan/cli/run.py` | `_make_engine()` — same bare-Engine path, same gap |
| `src/dan/server/chat_factory/__init__.py` | `_build_tool_registry()` — reference built-in tool registration (chat bootstrap, not workflow execution) |
| `src/dan/server/startup/__init__.py` | `_build_tool_registry()` — server-grade registration including `register_server_tools()` + custom tools |
| `tests/` workflow and CLI tests | Parity or de-scope regressions |

## Notes

- This plan is a policy decision as much as a code change.
- If parity is too expensive to make honest, the right answer is to narrow local mode rather than claim it is equivalent.
- Benchmark docs should reflect the outcome of this plan, not the other way around.

## Audit Notes (2026-03-25)

- **Two distinct "local" paths exist**: bare `Engine` (workflow execution, empty tool registry) and `build_chat_services` (chat bootstrap, built-ins populated). The plan's gap is specifically about the bare `Engine` path.
- **`register_common_capabilities` does not help here** — it registers `ChatCapabilityRegistry` handlers (concierge/chat tool surface), not `ToolRegistry` entries (workflow node executor tools). These are separate subsystems.
- **`DanClientOrLocal.__init__` accepts `tool_registry` but `_make_engine()` ignores it** — dead parameter that should either be wired through or removed.
- **Server-grade tool registration** includes `register_server_tools()` (domain-specific tools) and `DAN_CUSTOM_TOOLS_DIR` scanning — local parity only needs the built-in subset, not the full server tool surface.
- **`executor_defaults.py` is shared**, not "local-only" — it just happens to construct `ToolExecutor()` with an empty default. The fix would be to make the calling `Engine` constructor pass in a populated registry.

## Completion Notes (2026-03-26)

- Default tool executors now bootstrap the built-in tool registry in `[src/dan/executor_defaults.py](/Volumes/data/Dropbox/Projects/deep-agent-network/src/dan/executor_defaults.py)`.
- Local workflow execution in `[src/dan/client/local.py](/Volumes/data/Dropbox/Projects/deep-agent-network/src/dan/client/local.py)` now preserves custom tools and adds the built-in tool surface.
- Regression coverage proves `pdf_read` and other built-ins are present in local execution.
