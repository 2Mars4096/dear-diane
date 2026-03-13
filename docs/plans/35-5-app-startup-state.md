# 35-5: Extract Lifespan & Consolidate Server State

**Parent:** [35-server-module-decomposition](35-server-module-decomposition.md)
**Status:** not-started
**Goal:** Extract `app.py` startup/lifespan wiring into `server/startup.py` and migrate mutable module globals onto a typed `AppState`, using a staged compatibility approach rather than a big-bang cutover.

## Current State

### Lifespan (lines 1297–1920)
The `lifespan()` function is a single 620+ line async context manager that:
1. Resolves learning tier config from env
2. Creates RunStore, BlockRegistry, EngineConfig
3. Builds TierSuccessTracker, TelemetryStore
4. Creates RunManager, MentionResolver
5. Creates ChatCapabilityRegistry, registers 7 capability groups
6. Creates CapabilityContext
7. Initializes MCP bridge
8. Loads UserProfile, ConversationMemory, MemoryKernel
9. Creates ChatManager
10. Creates PublishRegistry, LocalRuntime, auto-registers published workflows
11. Initializes gateway router
12. Creates NotificationManager
13. Indexes self-knowledge (RAG)
14. Wires experience/discovery into capability context
15. Builds concierge + dispatcher
16. Starts TaskScheduler
17. Starts FollowUpDeliveryEngine
18. Starts consolidation loop
19. Initializes SkillLibrary
20. Yield (server runs)
21. Shutdown: cancel background tasks, shutdown MCP bridge, stop adapters

### Module globals (lines 98–120)
```python
_graph_store, _chat_store, _test_case_store  # created at import time
_run_manager, _chat_manager                  # None until lifespan
_meta_tasks, _meta_subscribers               # dict/defaultdict
_experience_index_cache                      # None until first access
_publish_registry, _block_registry           # None until lifespan
_active_adapters, _adapter_*                 # 5 adapter dicts
_self_knowledge_index                        # None until lifespan
_notification_manager, _concierge, _dispatcher, _mcp_bridge  # None until lifespan
```

## Safety Rules

- This sub-plan should land **after** `35-2` and after Plan `34-4` stabilizes.
- Do not replace every global in one PR; migrate in stages with temporary compatibility getters.
- Startup extraction and router/dependency migration are separate steps, not one giant diff.

## Tasks

- [ ] 1. Define `AppState` in `src/dan/server/app_state.py`
  - [ ] 1-1. Type fields explicitly wherever the concrete type is known
  - [ ] 1-2. Group fields by concern: stores, managers, integrations, background tasks, adapter runtime
  - [ ] 1-3. Add guard/accessor helpers for required runtime-only fields
- [ ] 2. Add a compatibility layer before deleting globals
  - [ ] 2-1. Instantiate and attach `AppState` on `app.state`
  - [ ] 2-2. Populate both `app.state` and existing globals from the same constructed objects
  - [ ] 2-3. Keep existing `_require_*` helpers temporarily, but source them from `app.state`
- [ ] 3. Create `src/dan/server/startup.py`
  - [ ] 3-1. Move `_get_engine_config()`
  - [ ] 3-2. Move `_build_chat_provider_registry()`
  - [ ] 3-3. Move `_auto_register_published_workflows()`
  - [ ] 3-4. Move `_consolidation_loop()`
  - [ ] 3-5. Move MCP bridge helpers
  - [ ] 3-6. Extract phased init functions with explicit inputs/outputs:
    - `init_stores(state)`
    - `init_capabilities(state)`
    - `init_managers(state)`
    - `init_integrations(state)`
    - `init_background_tasks(state)`
    - `shutdown_all(state)`
  - [ ] 3-7. Compose a clean `lifespan()` wrapper around those phases
- [ ] 4. Update `app.py` incrementally
  - [ ] 4-1. Import lifespan from `startup.py`
  - [ ] 4-2. Keep `app.py` as composition root only
  - [ ] 4-3. Do not combine with router extraction in the same PR
- [ ] 5. After `35-2`, move call sites onto state/dependencies
  - [ ] 5-1. Use `server/dependencies.py` for typed access
  - [ ] 5-2. Update router modules to use `request.app.state` / dependency helpers
  - [ ] 5-3. Remove direct module-global reads once no call sites remain
- [ ] 6. Final cleanup
  - [ ] 6-1. Remove legacy globals
  - [ ] 6-2. Remove `_require_run_manager()` and similar compatibility getters
  - [ ] 6-3. Delete any duplicate initialization paths
- [ ] 7. Verification
  - [ ] 7-1. Server lifecycle smoke: start → `/health` → chat turn → shutdown
  - [ ] 7-2. Run targeted startup/chat/router tests
  - [ ] 7-3. Verify no duplicate background tasks or double initialization

## Decisions

- (filled in during execution)

## Notes

- This is the highest-risk Phase 25 track. It should be the last one to land.
- The staged migration matters: first mirror globals into `app.state`, then flip readers, then delete the globals. Skipping that step would make rollback painful.
- Some globals (`_graph_store`, `_chat_store`, `_test_case_store`) are created at module import time today. Decide deliberately whether to keep eager construction or move to startup; do not change that implicitly during extraction.
- `_meta_tasks` and `_meta_subscribers` belong with the meta router state once `35-2` has landed.
