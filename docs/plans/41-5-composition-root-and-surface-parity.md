# 41-5: Composition Root & Surface Parity

**Parent:** [41-internal-runtime-submodule-restructure](41-internal-runtime-submodule-restructure.md)
**Status:** not-started
**Goal:** Replace duplicated startup and local/bootstrap wiring with one shared composition model so server mode, local CLI mode, and related surfaces build the same runtime capabilities consistently.

## Context

There is already an explicit goal for local/server parity, but the current composition still drifts:

- `startup.py` and `chat_factory.py` each build engine/provider/config state
- local mode and server mode do not register the exact same capability surface
- local bootstrap can still assume writable home directories or other environment-specific behavior
- runtime subsystems are initialized in slightly different ways depending on entrypoint

This is not just cleanup; it directly affects reliability and makes the larger modular refactor harder if multiple composition roots keep diverging.

## Tasks

### 1. Unify config and provider composition
- [ ] 1-1. Replace duplicate engine-config builders with one shared implementation. **Concrete targets:** `startup.py:_get_engine_config()` (~54KB file) and `chat_factory.py` (~14KB) both construct engine configs independently.
- [ ] 1-2. Replace duplicate provider-registry builders with one shared implementation. **Concrete targets:** `startup.py:_build_chat_provider_registry()`, `chat_factory.py` provider setup, and `engine/scheduler.py`'s own registry construction.
- [ ] 1-3. Keep capability/tool registry composition in one place or one shared declarative spec.

### 2. Align local and server runtime features
- [ ] 2-1. Ensure local mode registers the same capability groups that server mode expects unless a feature is intentionally unavailable.
- [ ] 2-2. Ensure `CapabilityContext` population is consistent across local and server paths.
- [ ] 2-3. Make project/task stores, memory stores, and local roots configurable so local mode does not hard-code unwritable home paths. **Current known issue:** `build_concierge()` still instantiates `ProjectStore()` with its `Path.home()/.dan/projects` default even when `chat_local.py` redirects the rest of local mode under `LOCAL_ROOT`, so local bootstrap can still fail under unwritable-home/sandboxed environments.

### 3. Make degradation behavior explicit
- [ ] 3-1. Preserve “best effort” startup semantics where missing optional subsystems degrade instead of crashing.
- [ ] 3-2. Make local-mode-only limitations explicit instead of accidental.
- [ ] 3-3. Add one composition-root status view so surfaces can report what was initialized and what degraded.

### 4. Add parity tests
- [ ] 4-1. Add tests that compare local and server capability registration for expected overlap.
- [ ] 4-2. Add tests for local bootstrap under unwritable-home / custom-root conditions.
- [ ] 4-3. Add tests that verify model/provider composition parity between local and server paths.

## Primary Files

- `src/dan/server/startup.py` — phased server initialization (~54KB); contains `_get_engine_config`, `_build_chat_provider_registry`, MCP bridge helpers, `_build_tool_registry` plus 8 init phases
- `src/dan/server/chat_factory.py` — duplicate provider-registry and engine-config construction (~14KB)
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
