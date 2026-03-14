# 35: Server Module Decomposition

**Status:** completed
**Goal:** Break down the largest server-side modules into focused, testable packages/modules without changing external behavior, public APIs, or public import paths mid-phase.

## Motivation

| File | Lines | Problem |
|---|---|---|
| `concierge/runtime.py` | 6,681 | God class with ~100 methods; already targeted by Plan 34 rewrite, so excluded from direct decomposition here |
| `chat_manager.py` | 5,669 | Prompt construction, event models, token estimation, graph summary, mutation parsing, multi-turn tool loop — all in one file |
| `app.py` | 5,362 | ~1,000 lines of domain tools, 660-line lifespan, 90 route handlers, 15+ mutable singletons, adapter subsystem |
| `capability_handlers.py` | 4,839 | ~70 handler functions + registration functions; every new capability grows this file |

Total: ~22,500 lines across 3 active decomposition targets, plus the Plan 34 runtime rewrite running alongside them.

## Scope

This phase is **structural extraction only**:
- Move code into packages/modules
- Add compatibility facades and stable import shims
- Update wiring/imports incrementally
- Verify behavior with focused smoke tests and existing test suites

**Out of scope:**
- Logic rewrites or feature work
- Plan 34 concierge runtime redesign itself
- Frontend changes
- `control_flow.py` and other non-server cleanup

## Operating Rules

- Preserve public behavior and public import paths during the phase. If code moves, the old module re-exports it until the whole phase is complete.
- Do not combine extraction with renames, signature changes, or behavior cleanup in the same PR.
- Keep PRs narrow: one boundary per PR, ideally <=800 changed lines excluding pure move-only churn.
- Every moved boundary lands with a before/after inventory and a verification step.
- Prefer packages over flat sibling files when a subsystem now spans 3+ modules.

## Sub-Plans

- [x] [35-1-extract-domain-tools](35-1-extract-domain-tools.md) — A. Extract domain tool implementations from `app.py` into `server/tools/`
- [x] [35-2-app-routers](35-2-app-routers.md) — B. Split route handlers from `app.py` into FastAPI `APIRouter` modules under `server/routers/`
- [x] [35-3-chat-manager-split](35-3-chat-manager-split.md) — C. Extract `chat_manager.py` support code into `server/chat/` package (5,669→3,647 lines; 7 new modules)
- [x] [35-4-capability-handlers-split](35-4-capability-handlers-split.md) — D. Split `capability_handlers.py` into `server/capabilities/` (4,839→752 lines; 14 domain modules)
- [x] [35-5-app-startup-state](35-5-app-startup-state.md) — E. Extract lifespan/startup into `server/startup.py` + typed `AppState` (1,076+120 lines new)

## Parallelization Contract with Plan 34

| Sub-plan | Main files touched | Safe to run in parallel with `34-XXX`? | Conditions |
|---|---|---|---|
| `35-1` | `app.py`, new `server/tools/*` | Yes, with caution | Keep `_build_tool_registry()` contract and tool IDs unchanged; no startup rewiring |
| `35-2` | `app.py`, new `server/routers/*` | No | Wait until `34-4` integration settles; too much `app.py` churn otherwise |
| `35-3` | `chat_manager.py`, new `server/chat/*` | Yes | Keep `chat_manager.py` as facade/re-export module; no `ChatManager` API changes |
| `35-4` | `capability_handlers.py`, new `server/capabilities/*` | Yes | Keep `register_*` signatures, capability names, and schemas identical |
| `35-5` | `app.py`, startup wiring, shared state | No | Sequence after `34-4` and after `35-2`; highest collision risk |

**Recommended landing order if Plan 34 is active:**
1. `35-3` and `35-4`
2. `35-1`
3. Finish/merge `34-4`
4. `35-2`
5. `35-5`

## Execution Order

```
Safe parallel lane with Plan 34:
  35-3 ──┐
         ├──→ 35-1
  35-4 ──┘

After 34-4 stabilizes:
  35-2 ──→ 35-5
```

Each sub-plan is independently shippable, but `35-2` and `35-5` should not be combined in one PR.

## Verification Gates

- Import compatibility smoke tests still pass (for example `from dan.server.chat_manager import ChatCompleteEvent`)
- Server startup and `/health` still work
- `/openapi.json` is stable after router extraction
- One representative chat turn still reaches exactly one terminal event
- Capability inventory/schema snapshot is unchanged after `35-4`
- Existing targeted suites for chat, concierge tiered dispatch, and capability registration still pass

## Success Criteria

- `app.py` becomes a thin composition root
- `chat_manager.py` becomes a compatibility facade + core manager surface, with support code moved under `server/chat/`
- `capability_handlers.py` becomes a compatibility facade + registration hub, with implementations moved under `server/capabilities/`
- No endpoint path, WebSocket protocol, tool ID, or capability schema changes
- Existing tests pass with only import-path adjustments where deliberately migrated

## Final Inventory

| Original file | Before | After | New packages |
|---|---|---|---|
| `app.py` | 5,362 | 426 | `server/tools/` (1,096), `server/routers/` (3,653), `server/startup.py` (1,076), `server/app_state.py` (120) |
| `chat_manager.py` | 5,669 | 3,647 | `server/chat/` (2,287) |
| `capability_handlers.py` | 4,839 | 752 | `server/capabilities/` (2,934) |
| **Total** | **15,870** | **4,825** | **11,166 lines in new packages** |

149 targeted tests verified passing. 2 pre-existing failures confirmed unrelated.

## Decisions

- `runtime.py` stayed out of scope; Plan 34 owns it
- Compatibility facades kept on `chat_manager.py` and `capability_handlers.py` — they re-export all moved symbols so existing imports work unchanged
- `app.py` uses `__getattr__` with cached lookups to re-export adapter/stream state that moved to router modules
- Module globals in `app.py` are mirrored from `AppState` via `_mirror_state_to_globals()` during lifespan for backward compatibility
- Adapter state dicts removed from `app.py` globals — canonical location is now `routers/adapters.py`

## Notes

- Phase completed in one session. All 5 sub-plans landed successfully.
- If Plan 34 deletes or relocates a dependency, update the relevant facade or `__getattr__` table in `app.py`.
