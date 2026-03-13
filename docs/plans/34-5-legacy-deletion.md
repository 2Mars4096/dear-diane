# 34-5: Legacy Deletion

**Parent:** [34-tiered-async-dispatcher](34-tiered-async-dispatcher.md)
**Status:** not-started
**Goal:** Delete all replaced concierge modules, resolve every external dependency, relocate shared types, clean up tests. The source of truth for what stays and what goes.

## Phase 0: Type Relocation (before any deletion)

These types live in modules being deleted but are used by kept modules. Relocate them to `models.py` first, update all imports, then delete.

| Type | Current Home | New Home | Used By (kept modules) |
|------|-------------|----------|------------------------|
| `IntentCategory` | `classifier.py:30` | `models.py` | `policy.py`, `triage.py`, `tier_executors.py`, `tiered_dispatch.py` |
| `RouteMode` | `classifier.py:36` | `models.py` | `triage.py`, `tier_executors.py` |
| `RouteDecision` | `classifier.py:42` | `models.py` | `triage.py`, `tier_executors.py` |
| `ResolvedContext` | `context_resolver.py:53` | `models.py` | `triage.py`, `dispatcher.py`, `tiered_dispatch.py` |

Total: ~25 lines of type definitions. Copy them into `models.py`, then do a project-wide find-and-replace of the old import paths.

After relocation, update imports in:
- `triage.py` — `from .classifier import ...` → `from .models import IntentCategory, RouteDecision, RouteMode`; `from .context_resolver import ResolvedContext` → `from .models import ResolvedContext`
- `tier_executors.py` — `from .classifier import ...` → `from .models import IntentCategory, RouteDecision`
- `tiered_dispatch.py` — `from .classifier import IntentCategory` → `from .models import IntentCategory`
- `dispatcher.py` — `from .context_resolver import ResolvedContext` → `from .models import ResolvedContext`
- `policy.py` — `from .classifier import IntentCategory` → `from .models import IntentCategory`

## Phase 1: Source Modules to Delete

| Module | Lines | Blocked by | Notes |
|--------|-------|------------|-------|
| `classifier.py` | 1,281 | Phase 0 (type relocation) | Types relocated → delete everything else |
| `handlers.py` | 1,289 | 34-3 (executor rewrite) | `tier_executors.py` currently imports `HandlerRegistry`, `HandlerResult` — rewrite removes this |
| `solver.py` | 406 | — | No external deps |
| `executor.py` | 255 | — | No external deps |
| `entity_grounding.py` | 445 | — | No external deps |
| `context_resolver.py` | 327 | Phase 0 (type relocation) | `ResolvedContext` relocated → delete everything else |
| `resume.py` | 371 | 34-2 (triage cleanup) | `triage.py` currently imports `ResumeProtocol` — triage cleanup inlines the 5-line lookup |
| `continuity.py` | 425 | — | No external deps |
| `completion_guard.py` | 610 | — | No external deps |
| `build_session.py` | 929 | — | No external deps |
| `goal_loop.py` | 772 | — | No external deps |
| `boundary_handoff.py` | 451 | Phase 2 (external deps) | `meta/controller.py` imports `WorkflowDepAssembler` |
| `reuse_decision.py` | 352 | 34-4 (dispatcher patch) | `tiered_dispatch.py` currently imports `reuse_first_decision` — dispatcher patch inlines the lookup |
| `memory_bridge.py` | 249 | — | No external deps |
| `learning.py` | 241 | — | No external deps |
| `follow_up.py` | 540 | Phase 2 (external deps) | `app.py` imports 3 functions |
| `promotion.py` | 47 | — | No external deps |
| `queue.py` | 43 | — | No external deps |
| **Total** | **~8,000** | | |

Plus `runtime.py` shrinks from 6,681 → ~300 lines (**~6,400 lines removed**).

**Grand total source deleted: ~14,400 lines.**

### NOT deleted (previously misclassified)

| Module | Lines | Why it stays |
|--------|-------|-------------|
| `domain_learning.py` | 998 | **Self-contained** (no concierge imports). Substantial external usage: `memory_kernel.py` imports `DomainPatternGeneralizer`, `DomainTemplateConsolidator`, `get_or_create_template`, `save_domain_template`. `chat_manager.py` imports `detect_domain`. This is knowledge management infrastructure, not routing logic. |

## Phase 2: External Dependencies to Resolve

### `src/dan/server/app.py` → `follow_up.py` (3 imports)

```
line 1644: from dan.server.concierge.follow_up import create_schedule_result_trigger
line 1724: from dan.server.concierge.follow_up import (FollowUpDeliveryEngine, scan_stale_tasks)
line 1762: from dan.server.concierge.follow_up import create_run_completion_trigger
```

**Resolution:** If proactive follow-up triggers are still needed, extract the trigger-creation functions (~50 lines) into a standalone `triggers.py` in the concierge module. Otherwise delete the follow-up wiring from `app.py` entirely. The `FollowUpDeliveryEngine` is 200+ lines — decide if it earns its keep or is dead code.

### `src/dan/meta/controller.py` → `boundary_handoff.py` (1 import)

```
line 367: from dan.server.concierge.boundary_handoff import WorkflowDepAssembler
```

**Resolution:** `WorkflowDepAssembler` (~80 lines) assembles workflow dependencies for the meta-controller. If still needed, extract to `src/dan/meta/dep_assembler.py` (closer to its consumer). Otherwise remove.

### `src/dan/server/chat_manager.py` → `domain_learning.py` (1 import — NO ACTION)

```
line 4895: from dan.server.concierge.domain_learning import detect_domain
```

**Resolution:** No action needed — `domain_learning.py` is kept.

### `src/dan/engine/memory_kernel.py` → `domain_learning.py` (1 import — NO ACTION)

```
line 923: from dan.server.concierge.domain_learning import (DomainPatternGeneralizer, ...)
```

**Resolution:** No action needed — `domain_learning.py` is kept.

## Phase 3: Test Deletion

### Tests in `tests/test_concierge/` to delete

| Test file | Lines | Covers |
|-----------|-------|--------|
| `test_classifier.py` | 322 | `classifier.py` |
| `test_handlers.py` | 532 | `handlers.py` |
| `test_solver.py` | 276 | `solver.py` |
| `test_execution_selector.py` | 279 | `executor.py` |
| `test_entity_grounding.py` | 705 | `entity_grounding.py` |
| `test_context_resolver.py` | 184 | `context_resolver.py` |
| `test_resume.py` | 868 | `resume.py` |
| `test_continuity.py` | 621 | `continuity.py` |
| `test_completion_guard.py` | 658 | `completion_guard.py` |
| `test_build_session_diagnosis.py` | 173 | `build_session.py` |
| `test_goal_loop.py` | 845 | `goal_loop.py` |
| `test_goal_orchestration.py` | 397 | goal orchestration in `runtime.py` |
| `test_follow_up.py` | 712 | `follow_up.py` |
| `test_fallback_policy.py` | 124 | fallback policy (legacy path) |
| `test_promotion.py` | 57 | `promotion.py` |
| `test_queue.py` | 82 | `queue.py` |
| `test_inline_execution.py` | 629 | inline execution (legacy path) |
| `test_workflow_memory.py` | 240 | `memory_bridge.py` |
| `test_runtime.py` | 1,085 | `runtime.py` (legacy path) |
| `test_runtime_fast_path.py` | 191 | legacy fast path |
| `test_dispatcher.py` | 573 | `dispatcher.py` (parts referencing legacy) |
| **Subtotal** | **~8,700** | |

### Tests outside `test_concierge/` to delete

| Test file | Lines | Covers |
|-----------|-------|--------|
| `tests/test_domain_learning.py` | ~1,800 | `domain_learning.py`, `context_resolver.py`, `handlers.py`, `reuse_decision.py`, `classifier.py` — heavily entangled with deleted modules |
| `tests/test_server/test_direct_task_complexity.py` | TBD | References `DirectTaskHandler` from `handlers.py` |
| `tests/test_server/test_classifier_path.py` | TBD | References `classifier.py` |
| **Subtotal** | **~2,000+** | |

**Grand total test lines deleted: ~10,700+**

### Tests to keep / update

| Test file | Lines | Action |
|-----------|-------|--------|
| `test_tiered_dispatch.py` | 588 | **Update** — primary integration tests for new path |
| `test_triage.py` | 269 | **Update** — remove `ClassificationResult` compat tests |
| `test_session_manager.py` | 202 | **Keep** — session model tests unchanged |
| `test_command_registry.py` | 518 | **Keep** — slash commands unchanged |
| `test_command_registry_health.py` | 129 | **Keep** |
| `test_fast_commands.py` | 380 | **Keep** — slash command tests |
| `test_identity.py` | 201 | **Keep** — bot identity unchanged |
| `test_policy.py` | 96 | **Update** — `IntentCategory` import path changes |
| `test_progress.py` | 111 | **Keep** — progress reporting |
| `test_progress_ux.py` | 1,501 | **Keep** — progress UX |
| `test_scheduler.py` | 1,580 | **Keep** — task scheduling |
| `test_resources.py` | 486 | **Keep** — resource management |
| `test_pii_tokenizer.py` | 958 | **Keep** — PII masking |
| `test_computer_policy.py` | 906 | **Keep** — browser automation |
| `test_model_control.py` | 456 | **Update** — may reference deleted classifier |
| `test_models_store.py` | 103 | **Keep** |
| `test_parallelism_integration.py` | 632 | **Update** — references legacy parallelism |
| `test_unified_queue.py` | 404 | **Update** — may reference deleted queue |
| `test_visibility_feedback.py` | 386 | **Update** — may reference legacy runtime |
| `test_live_data.py` | 152 | **Update** — may reference legacy handlers |
| `test_learning_commands.py` | 166 | **Keep** — slash commands |
| `test_memory_commands.py` | 199 | **Keep** — slash commands |
| `test_mcp_commands.py` | 174 | **Keep** — slash commands |
| `test_project_command.py` | 239 | **Keep** — slash commands |
| `test_surface_policy.py` | 39 | **Keep** |
| `test_clarification_formatting.py` | 48 | **Keep** |
| `test_fan_out.py` | 204 | **Keep** |
| `test_phase31_review_hardening.py` | 137 | **Update** — may reference deleted modules |
| `test_chat_manager_tool_handoff.py` | TBD | **Update** — may reference `ClassificationResult` |

## Phase 4: `__init__.py` Cleanup

Current `__init__.py` is 326 lines with exports for all 40+ modules. Gut to ~80 lines.

**Keep exports:**
- Core: `ConciergeRuntime`, `SurfaceMessage`, `Project`, `Task`, `ProjectStore`
- Types: `IntentCategory`, `RouteDecision`, `RouteMode`, `ResolvedContext` (now from `models.py`)
- Dispatch: `ConcurrentDispatcher`, `TieredDispatcher`, `ContextGatherer`
- Sessions: `SessionManager`, `Session`, `SessionTier`, `SessionState`, `SessionResult`
- Executors: `InstantExecutor`, `SingleShotExecutor`, `MultiStepExecutor`
- Triage: `TriageResult`, `EntityRef`
- Infra: `CommandRegistry`, `ActionPolicy`, `ExecutionPolicy`

**Delete ALL exports referencing:** `ClassificationResult`, `classify_intent`, `HandlerRegistry`, `HandlerResult`, `GoalResolver`, `PlanBuilder`, `SolverDecision`, `ExecutionSelector`, `ExecutionResult`, `BuildSessionManager`, `BuildSession`, `ResumeProtocol`, `CompletionChecker`, `CompletionReport`, `FollowUpTrigger`, `FollowUpQueue`, `ProjectMessageQueue`, `WorkflowMemoryIndex`, `PromotionProposal`, `WorkflowPromoter`, and every other export from deleted modules.

## Phase 5: Documentation Updates

### `docs/architecture.md`

Rewrite the "Concierge Runtime" section to describe:
- Triage → Tier → Execute flow
- `ContextGatherer` replaces speculative prep
- Session tree model
- Lifecycle hooks for telemetry/memory/turn recording
- What's in each file (models, triage, tier_executors, tiered_dispatch, session)

### `docs/llm-api-guide.md`

Update if any LLM-facing API references mention deleted modules.

## Tasks

- [ ] 1. **Phase 0: Relocate types** — copy `IntentCategory`, `RouteMode`, `RouteDecision`, `ResolvedContext` into `models.py`; update all imports project-wide
- [ ] 2. **Phase 1: Delete 18 source modules** (see table above)
- [ ] 3. **Phase 2a: Resolve `app.py` → `follow_up.py`** — extract triggers to `triggers.py` or delete
- [ ] 4. **Phase 2b: Resolve `meta/controller.py` → `boundary_handoff.py`** — extract `WorkflowDepAssembler` or delete
- [ ] 5. **Phase 3: Delete ~23 test files** (~10,700 lines)
- [ ] 6. **Phase 3b: Update test files** that import from deleted modules (see "update" rows)
- [ ] 7. **Phase 4: Gut `__init__.py`** from 326 → ~80 lines
- [ ] 8. **Phase 5: Rewrite `docs/architecture.md`** concierge section
- [ ] 9. **Clean up `__pycache__`** in `tests/test_concierge/`
- [ ] 10. **Verify:** `python -c "from dan.server.concierge import ConciergeRuntime"` and `pytest tests/test_concierge/ -x` pass clean

## Execution Order

This plan runs **after** 34-2, 34-3, and 34-4 are complete and the new path is verified.

1. Phase 0 first — type relocation (safe, no behavior change)
2. Phases 1+2 — delete source modules + resolve external deps (one commit)
3. Phase 3 — delete/update tests (one commit)
4. Phase 4+5 — clean up exports + docs (one commit)
5. Phase verify — run full test suite

## Notes

- `domain_learning.py` (998 lines) is **kept**. It's self-contained (imports only from `dan.engine`) and provides domain template management used by `memory_kernel.py`. Not routing logic.
- `tests/test_domain_learning.py` (~1,800 lines) is **deleted** despite `domain_learning.py` being kept — the test file imports heavily from deleted modules (`context_resolver`, `handlers`, `reuse_decision`, `classifier`). New tests for `domain_learning.py` should be written that don't depend on deleted modules.
- Total lines deleted: ~14,400 source + ~10,700 tests = **~25,100 lines**. Total concierge source goes from ~25,700 to ~11,300 (including `domain_learning.py`). Total tests go from ~20,800 to ~10,100.
