# 17-4: Observability & Event Wiring

**Parent:** [17-self-evolving-orchestrator](17-self-evolving-orchestrator.md)
**Status:** completed
**Goal:** Wire all 11 self-evolving `EventType` values into their natural emission sites so the full learning loop is traceable through the run event log — enabling debugging, UI visibility, and audit trails.

## Problem

Tiers 1–3 defined 11 new `EventType` values in `engine/events.py` but none are emitted anywhere. All self-evolving behavior is invisible to the event system. Without these emissions:

- The run history panel shows no trace of error indexing, retrieval, reflection, or rule activity.
- Debugging "why did/didn't the LLM get error context?" requires reading Python logs at DEBUG level.
- There is no audit trail for rule creation, expiry, pruning, or auto-disable decisions.
- The editor UI cannot surface learning-loop status to users.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| 11 `EventType` enums | `engine/events.py:72–85` | Declared: `ERROR_MEMORY_INDEXED`, `ERROR_MEMORY_RETRIEVED`, `REFLECTION_STARTED`, `REFLECTION_COMPLETED`, `RULE_GENERATED`, `RULE_ACTIVATED`, `RULE_EXPIRED`, `RULE_DISABLED`, `RULE_AUTO_DISABLED`, `RULE_PRUNED`, `RULE_EFFECTIVENESS_UPDATE` | Zero `emit()` calls reference them |
| `Engine._emit()` | `engine/scheduler.py:440–445` | Async emit → `event_callback(event)` if registered | Available only inside scheduler; not accessible from `run_manager.py` or standalone helpers |
| `RunManager._event_callback` | `server/run_manager.py:339–375` | Records events to `record.events`, broadcasts to WebSocket, persists via `RunStore.append_event()` | RunManager can emit events by constructing `EngineEvent` dicts and appending to `record.events` |
| `context.emit_event()` | `engine/executor.py` | Available inside executors via `ExecutionContext` | `LLMExecutor` and `ReflectionExecutor` can emit events for retrieval/reflection |
| `logger.debug(...)` calls | Various | 15+ debug/warning log calls across error_memory, reflection, rule_generator, run_manager | Only visible at DEBUG log level; not in event stream |

## Approach

Two categories of emission sites:

1. **Inside execution path** (have `context.emit_event()` or `Engine._emit()`):
   - `LLMExecutor` — error memory retrieval
   - `Engine._execute()` — rule injection summary
   - `ReflectionExecutor` — reflection lifecycle (via scheduler's existing node events, no extra needed)

2. **Outside execution path** (in `RunManager` post-run hooks, `RuleLifecycleManager`):
   - `RunManager._enrich_and_persist()` and helpers — error indexing, reflection scheduling, principle persistence, effectiveness tracking
   - These don't have `Engine._emit()` access, but they can append `EngineEvent.to_dict()` directly to `record.events` and broadcast via the existing `_broadcast_event()` helper.

## Tasks

- [x] 1. Error memory events (Tier 1)
  - [x] 1-1. `ERROR_MEMORY_INDEXED` — emit in `RunManager._index_run_errors()` after successful indexing. Data: `{"error_count": N, "workflow_id": wf_id}`.
  - [x] 1-2. `ERROR_MEMORY_RETRIEVED` — emit in `LLMExecutor.execute()` after retrieval returns results. Data: `{"context_chars": len(ctx), "tier": "error_memory"}`. Uses `context.emit_event()`.

- [x] 2. Reflection events (Tier 2)
  - [x] 2-1. `REFLECTION_STARTED` — emit in `RunManager._schedule_reflection_background()` when reflection run is created. Data: `{"source_run_id": original_run_id, "reflection_run_id": rid}`.
  - [x] 2-2. `REFLECTION_COMPLETED` — emit in `RunManager._persist_reflection_principles()` after principle persistence. Data: `{"reflection_run_id": rid, "principle_count": N, "workflow_id": wf_id}`.

- [x] 3. Rule lifecycle events (Tier 3)
  - [x] 3-1. `RULE_GENERATED` — emit in `RunManager._persist_reflection_principles()` after `RuleLifecycleManager.create_rule()` succeeds. Data: `{"rule_id": rid, "hyperedge_type": type, "principle_id": pid}`.
  - [x] 3-2. `RULE_ACTIVATED` — emit in `Engine._execute()` when generated rules are injected at runtime. Data: `{"rule_count": N, "workflow_id": wf_id}`.
  - [x] 3-3. `RULE_EXPIRED` / `RULE_PRUNED` — emit in `RunManager._track_rule_effectiveness()` when lifecycle transitions occur. Data includes `{"rule_id": rid, "reason": reason}`.
  - [x] 3-3b. `RULE_DISABLED` — emit via `RunManager.emit_rule_lifecycle_event()` from `POST /api/rules/.../disable` endpoint. Data: `{"rule_id": rid, "reason": "manual_api"}`.
  - [x] 3-4. `RULE_EFFECTIVENESS_UPDATE` — emit in `RunManager._track_rule_effectiveness()` after recording outcomes. Data: `{"rule_id": rid, "apply_count": N, "effectiveness_score": score}`. Payload uses reloaded rule values (fresh after `record_application`/`record_outcome`).

- [x] 4. Emission infrastructure for post-run hooks
  - [x] 4-1. Added `_emit_learning_event()` helper to `RunManager` that constructs an `EngineEvent`, appends `to_dict()` to `record.events`, broadcasts to subscribers, and persists via `RunStore.append_event()`.
  - [x] 4-2. All post-run event emissions include the `run_id` of the originating run and `workflow_id` in the data payload.

- [x] 5. Tests
  - [x] 5-1. Unit tests: `_emit_learning_event()` appends to record events, broadcasts to subscribers, includes node_id when provided.
  - [x] 5-2. Unit test: `ERROR_MEMORY_RETRIEVED` event emitted from `LLMExecutor` when error context is injected.
  - [ ] 5-3. Integration test: full pipeline test (requires end-to-end run with mock LLM — deferred).

## Data Shape Convention

All self-evolving events follow a consistent data schema:

```python
EngineEvent(
    event_type=EventType.ERROR_MEMORY_INDEXED,
    run_id="run-abc",                    # The run that triggered this
    node_id=None,                        # Set when node-specific (e.g., retrieval)
    node_type=None,                      # Set when node-specific
    data={
        "workflow_id": "wf-123",         # Always present
        "tier": "error_memory",          # One of: error_memory, reflection, rules
        # ... tier-specific fields
    },
)
```

## Decisions

- **Post-run events use `RunManager._emit_learning_event()`**, not `Engine._emit()`. Post-run hooks run after the engine has returned; the engine's emit channel is closed. RunManager appends directly to the record's event list and broadcasts to subscribers.
- **Reflection-related events are dual-emitted** to both the reflection run record and the originating run record. This ensures the full learning loop is traceable from the original run's event log.
- **API-driven lifecycle events use `RunManager.emit_rule_lifecycle_event()`**, which finds the most recent run for the workflow and emits to it. This covers `RULE_DISABLED` (manual disable via API).
- **`RULE_AUTO_DISABLED` emission is deferred** until the auto-disable feature (17-3 task 4-2) is implemented.
- **Reflection node lifecycle uses existing `NODE_STARTED`/`NODE_COMPLETED` events** from the scheduler — no separate `REFLECTION_STARTED`/`REFLECTION_COMPLETED` at the node level. The dedicated `REFLECTION_STARTED`/`COMPLETED` events fire at the *scheduling* level (when RunManager decides to trigger a reflection run and when principles are persisted).
- **Event data is kept small.** No embedding vectors, no full error messages — just IDs, counts, and status. The full data is accessible via the error memory index, principle store, or rule lifecycle manager.

## Notes

- This is a pure instrumentation pass — no behavioral changes, no new features. Just emitting events at the right places.
- The 11 `EventType` values already exist; this plan only wires them up.
- Editor UI integration (showing these events in the run panel) is out of scope — that's a frontend task for a future plan.
