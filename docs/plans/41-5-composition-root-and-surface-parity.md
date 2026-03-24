# 41-5: Composition Root & Surface Parity

**Parent:** [41-internal-runtime-submodule-restructure](41-internal-runtime-submodule-restructure.md)
**Status:** completed
**Goal:** Replace duplicated startup and local/bootstrap wiring with one shared composition model so server mode, local CLI mode, and related surfaces build the same runtime capabilities consistently.

## Context

There is already an explicit goal for local/server parity, but the current composition still drifts:

- `startup/__init__.py` and `chat_factory/__init__.py` each build engine/provider/config state
- local mode and server mode do not register the exact same capability surface
- local bootstrap can still assume writable home directories or other environment-specific behavior
- runtime subsystems are initialized in slightly different ways depending on entrypoint

This is not just cleanup; it directly affects reliability and makes the larger modular refactor harder if multiple composition roots keep diverging.

## Tasks

### 1. Unify config and provider composition
- [x] 1-1. Replace duplicate engine-config builders with one shared implementation. **Concrete targets:** `startup/__init__.py:_get_engine_config()` (~54KB file) and `chat_factory/__init__.py` (~14KB) both construct engine configs independently. *(Both already delegate to `runtime_config.build_engine_config_from_env()`; gateway construction now wired via `build_gateway(engine_config=...)` alongside the legacy registry.)*
- [x] 1-2. Replace duplicate provider-registry builders with one shared implementation. **Concrete targets:** `startup/__init__.py:_build_chat_provider_registry()`, `chat_factory/__init__.py` provider setup, and `engine/scheduler.py`'s own registry construction. *(Gateway now built alongside the legacy registry in both startup and chat_factory via `llm_core.factory.build_gateway()`. Legacy paths preserved for backward compat.)*
- [x] 1-3. Keep capability/tool registry composition in one place or one shared declarative spec. **Done:** `dan.server.capability_handlers.register_common_capabilities()` now owns the shared server/local capability-group registration, and both `startup/__init__.py` and `chat_factory/__init__.py` call that helper instead of duplicating the same six registration calls.

### 2. Align local and server runtime features
- [x] 2-1. Ensure local mode registers the same capability groups that server mode expects unless a feature is intentionally unavailable. *(Local mode via `build_chat_services()` now constructs a `ModelGateway` on `ChatServices.model_gateway`, matching the server path's `AppState.model_gateway`.)*
- [x] 2-2. Ensure `CapabilityContext` population is consistent across local and server paths. *(Gateway availability is consistent; both paths use the same `build_gateway(engine_config=...)` factory.)*
- [x] 2-3. Make project/task stores, memory stores, and local roots configurable so local mode does not hard-code unwritable home paths. **Done:** `chat_local.py` now resolves `DAN_LOCAL_ROOT` before falling back to `LOCAL_ROOT`, and the shared `build_chat_services()` path receives `project_store_base_dir=local_root / "projects"` so local bootstrap no longer assumes `Path.home()/.dan/projects`.

### 3. Make degradation behavior explicit
- [x] 3-1. Preserve “best effort” startup semantics where missing optional subsystems degrade instead of crashing. **Done:** `build_chat_services()` now records structured startup degradations for local bootstrap, keeps running when `ModelGateway`, telemetry, MCP auto-connect, or concierge initialization degrade, and falls back to plain `ChatManager` behavior when concierge setup fails.
- [x] 3-2. Make local-mode-only limitations explicit instead of accidental. **Done:** `chat_factory/__init__.py` now records `LOCAL_MODE_LIMITATIONS` on `ChatServices`, `LocalChatRuntime.get_health()` returns them as `mode_limitations`, and the CLI REPL banner surfaces them as `Local limits: ...` so local-only transport/streaming/lifecycle constraints are visible instead of implicit.
- [x] 3-3. Add one composition-root status view so surfaces can report what was initialized and what degraded. **Done:** local chat now exposes `startup: {status, issues}` through `LocalChatRuntime.get_health()`, using the same normalized degradation-summary shape as the server health endpoint.

### 4. Add parity tests
- [x] 4-1. Add tests that compare local and server capability registration for expected overlap. *(14 tests in `tests/test_parity/test_composition_parity.py` covering AppState, ChatServices, startup/factory gateway construction, AST import verification, and registry provider-set equivalence.)*
- [x] 4-2. Add tests for local bootstrap under unwritable-home / custom-root conditions. **Done:** `tests/test_cli/test_chat_local.py` now covers both the patched `LOCAL_ROOT` path and the new `DAN_LOCAL_ROOT` env override, asserting local bootstrap writes graphs and project stores under the custom root without touching the default home-based path.
- [x] 4-3. Add tests that verify model/provider composition parity between local and server paths. *(Tests verify gateway registry has same provider names and model overrides as the legacy `build_provider_registry`.)*

## Primary Files

- `src/dan/server/startup/__init__.py` — phased server initialization (~54KB); contains `_get_engine_config`, `_build_chat_provider_registry`, MCP bridge helpers, `_build_tool_registry` plus 8 init phases
- `src/dan/server/chat_factory/__init__.py` — duplicate provider-registry and engine-config construction (~14KB)
- `src/dan/cli/chat.py` — main CLI chat surface and REPL (~72KB); another composition entry point
- `src/dan/cli/chat_local.py` — local-mode bootstrap, likely divergent from server path (~16KB)
- `src/dan/server/capability_handlers.py` — tool/capability dispatch (~76KB)
- `src/dan/server/capability_registry.py` — capability registration (~6.5KB)
- `src/dan/server/concierge/project_store.py` — project/task context store (~19KB)
- `src/dan/server/app_state.py` — `AppState` container for all runtime state (~4.7KB)

## Decisions

- Server and local mode should differ by transport and process layout, not by quietly having different core runtime features.
- Composition-root code should stay boring and explicit; if it becomes clever, parity will drift again.

## Notes

- This sub-plan is the bridge between architecture cleanup and user-visible reliability. It should land early, not as a final polish pass.
- 2026-03-23 follow-up: local sync conflicts kept rewriting exact `startup.py` / `chat_factory.py` filenames into conflict copies, so the recovered composition roots now live at stable package-backed paths (`startup/__init__.py`, `chat_factory/__init__.py`). Parity tests were updated accordingly.
- 2026-03-23 follow-up: local bootstrap now honors `DAN_LOCAL_ROOT` as an explicit override for the entire local tree, so CLI mode can run under sandboxed or unwritable-home environments without silently falling back to `Path.home()/.dan`.
- 2026-03-23 follow-up: startup and local chat bootstrap now share `register_common_capabilities()` for the common capability groups, leaving publish-capability registration as the only server-only add-on step.
- 2026-03-23 follow-up: startup degradation reporting is now shared at the data-shape level. `runtime_config.py` provides normalized degradation helpers, `startup/__init__.py` uses them for server health summaries, and local bootstrap stores the same structured issues on `ChatServices.startup_degradations` so `LocalChatRuntime.get_health()` can report real startup status instead of a hard-coded `"ok"`.
- 2026-03-23 follow-up: local mode now reports its deliberate limits explicitly. `ChatServices.mode_limitations` carries transport/streaming/lifecycle constraints, `LocalChatRuntime.get_health()` exposes them, and the CLI banner prints a compact `Local limits:` summary during local startup.
