# 34-4: Integration — Complete Rewrite

**Parent:** [34-tiered-async-dispatcher](34-tiered-async-dispatcher.md)
**Status:** in-progress
**Goal:** Rewrite `runtime.py` from 6,681 lines to ~300 lines. Delete the legacy path entirely. Wire the tiered dispatcher as the only processing path. No feature flag, no fallback.

## New `ConciergeRuntime` (~300 lines)

The entire class becomes:

```python
class ConciergeRuntime:
    """Thin shell: init stores + dispatcher, process = slash check + dispatch."""

    def __init__(self, chat_manager, ...):
        # --- Stores ---
        self.chat_manager = chat_manager
        self.project_store = ProjectStore(...)
        self._concierge_state = {}

        # --- Infrastructure (keep) ---
        self._command_registry = CommandRegistry()
        self._pii_session = PIISession(...)  # if enabled
        self._behavior_store = BehaviorStore(...)  # if enabled

        # --- Tiered dispatcher (the only path) ---
        self._session_manager = SessionManager()
        self._dispatcher = TieredDispatcher(
            session_manager=self._session_manager,
            triage_fn=triage,
            executors={
                SessionTier.INSTANT: InstantExecutor(self),
                SessionTier.SINGLE: SingleShotExecutor(self),
                SessionTier.MULTI: MultiStepExecutor(self),
            },
            context_gatherer=ContextGatherer(),
            concierge=self,
        )

    async def process(self, msg: SurfaceMessage) -> AsyncIterator[ChatStreamEvent]:
        """Outer wrapper: reassurance timer + telemetry."""
        start = time.monotonic()
        self._concierge_state = self._load_concierge_state(msg.external_id)

        # Reassurance timer for slow responses
        progress_session = ProgressSession(...)
        reassurance_task = asyncio.create_task(self._reassurance_loop(progress_session))

        try:
            async for event in self._process_inner(msg):
                progress_session.mark_activity()
                yield event
        finally:
            reassurance_task.cancel()
            self._save_concierge_state(msg.external_id, self._concierge_state)
            self._emit_turn_telemetry(msg, time.monotonic() - start)

    async def _process_inner(self, msg: SurfaceMessage) -> AsyncIterator[ChatStreamEvent]:
        """The entire routing logic. ~10 lines."""
        fast = await self._try_fast_command(msg)
        if fast is not None:
            yield fast
            return

        async for event in self._dispatcher.dispatch(msg):
            yield event

    # --- Helpers that dispatcher/executors need ---

    async def _try_fast_command(self, msg): ...       # slash command check
    def _load_concierge_state(self, surface_id): ...  # state persistence
    def _save_concierge_state(self, surface_id, state): ...
    def _emit_turn_telemetry(self, msg, duration): ...
    def _record_assistant_turn(self, ...): ...         # turn recording
    def _store_memory_candidates(self, ...): ...       # memory extraction
    def _finalize_task(self, ...): ...                 # task finalization
```

That's the entire class. Everything else lives in the dispatcher, executors, or lifecycle hooks.

## TieredDispatcher

The central coordinator. Unchanged from the original plan except: no feature flag, no fallback, pending-follow-up check moved before triage.

```python
class TieredDispatcher:
    async def dispatch(self, msg: SurfaceMessage) -> AsyncIterator[ChatStreamEvent]:
        # 0. Check for pending follow-up (yes/no reply to earlier confirm/clarify)
        pending = self._check_pending_follow_up(msg)
        if pending is not None:
            yield pending
            return

        # 1. Triage (one LLM call)
        triage = await self._triage(msg)

        # 2. Create root session
        session = self.session_manager.create_root(msg, triage, triage.tier)

        # 3. Tier 0: instant — no context needed
        if triage.tier == SessionTier.INSTANT:
            async for event in self._executors[0].execute(session, self.session_manager):
                yield event
            await self._on_session_complete(session)
            return

        # 4. Gather context (targeted by triage.context_needs)
        context = await self._gather_context(msg, triage)
        session.context = context

        # 5. Dispatch to tier executor
        executor = self._executors[triage.tier]
        async for event in executor.execute(session, self.session_manager):
            yield event

        # 6. Post-session hooks
        await self._on_session_complete(session)
```

## ContextGatherer

Targeted context fetching. Only fetches what triage says is needed.

```python
class ContextGatherer:
    async def gather(self, msg, triage, concierge) -> ResolvedContext:
        tasks = {}

        # Always resolve project/task
        tasks["context"] = self._resolve_context(msg, concierge)

        if "memory" in triage.context_needs:
            tasks["memory"] = self._retrieve_memory(msg, concierge)

        file_needs = [n for n in triage.context_needs if n.startswith("file:")]
        if file_needs:
            tasks["auto_read"] = self._read_files([n[5:] for n in file_needs])

        domain_needs = [n for n in triage.context_needs if n.startswith("domain:")]
        if domain_needs:
            tasks["domain"] = self._retrieve_domain(domain_needs[0][7:], msg, concierge)

        if "reuse" in triage.context_needs:
            tasks["reuse"] = self._search_reuse(msg, concierge)

        results = await fan_out_dict(tasks, timeout_per=5.0)
        return self._assemble(msg, results, concierge)
```

This replaces the old speculative parallel prep that ran memory + domain + reuse + artifacts + auto-read for every message regardless of need.

## Session Lifecycle Hooks

### Per-Session (every session, root or child)

```python
async def _on_any_session_complete(self, session):
    await self._concierge._emit_telemetry_event(
        "session_complete",
        metadata={
            "session_id": session.id,
            "tier": session.tier.value,
            "depth": session.depth,
            "task": session.task[:200],
            "duration_ms": session.result.duration_ms if session.result else 0,
            "token_usage": session.result.token_usage if session.result else {},
            "tools_used": session.result.tools_used if session.result else [],
        },
    )
```

### Root Session (conversation-level bookkeeping)

```python
async def _on_root_session_complete(self, session):
    result = session.result

    # 1. Record conversation turns
    self._concierge._record_assistant_turn(...)

    # 2. Store memory candidates
    self._concierge._store_memory_candidates(...)

    # 3. Attach session tree trace to turn metadata
    tree_trace = self._session_manager.build_trace(session.id)
    # metadata={"session_tree": [t.model_dump() for t in tree_trace]}

    # 4. Emit aggregated telemetry
    await self._concierge._emit_turn_telemetry(...)

    # 5. Finalize task
    self._concierge._finalize_task(...)

    # 6. Prune completed sessions
    self._session_manager.prune_completed(max_age_seconds=300)
```

## Event Streaming

Events bubble up through the tree:

```
[progress] Understanding your request...          ← triage phase
[progress] Researching supply chain disruptions... ← child A starting
[tool_call] web_search("port congestion 2026")     ← grandchild A1
[tool_result] Found 5 results...                   ← grandchild A1
[progress] Analyzing data trends...                ← child B starting
[complete] Here is your supply chain risk report... ← root synthesis
```

Reuses existing `ChatStreamEvent` types. No new event types needed.

## What Gets Removed from `runtime.py`

| What | Lines | Why |
|------|-------|-----|
| `_legacy_process_inner` | ~1,200 | Replaced by `TieredDispatcher.dispatch()` |
| `_simple_message_fast_path_candidate` | ~50 | Triage handles this |
| `_handle_build_session` / build orchestration | ~200 | Tier 2 session variant |
| `_handle_goal_orchestration` / goal loop | ~150 | Tier 2 session variant |
| `_execute_goal_inline` / inline execution | ~300 | Tier 2 executor |
| Speculative parallel context prep | ~100 | `ContextGatherer` |
| Guard check integration (3 passes) | ~100 | Removed; Tier 2 executor adds if needed |
| Solver/plan building path | ~100 | Tier 2 decomposition |
| Entity grounding integration | ~50 | Triage does this |
| Resume/continuity integration | ~50 | Triage does this |
| Correction detection | ~50 | Removed; add to Tier 2 if needed |
| `classify_intent` calls | ~30 | Triage replaces this |
| Memory/learning hooks (scattered) | ~200 | Session lifecycle hooks |
| 80+ helper methods | ~3,500 | Most become unnecessary |
| **Total removed** | **~6,000** | |

## What Stays in `runtime.py`

| What | Lines | Why |
|------|-------|-----|
| `__init__` | ~100 | Init stores, chat_manager, dispatcher |
| `process()` | ~30 | Reassurance timer + telemetry wrapper |
| `_process_inner()` | ~10 | Slash command → dispatch |
| `_try_fast_command()` | ~30 | Slash command dispatch |
| State persistence helpers | ~30 | Load/save concierge state |
| Telemetry helpers | ~30 | Emit turn telemetry |
| Turn recording helpers | ~30 | Record assistant turn |
| Memory storage helpers | ~30 | Store memory candidates |
| **Total** | **~300** | |

## Entanglements in Integration Modules

### `tiered_dispatch.py` — imports from deleted modules

| Line | Import | Resolution |
|------|--------|------------|
| 62 | `from .reuse_decision import reuse_first_decision` | Remove. Reuse lookup becomes a `ContextGatherer` responsibility — if triage says `"reuse" in context_needs`, gatherer calls the relevant search directly (inline the 10-line lookup). |
| 342 | `from .classifier import IntentCategory` | Change to `from .models import IntentCategory`. |

### `dispatcher.py` — imports from deleted modules

| Line | Import | Resolution |
|------|--------|------------|
| 14 | `from .context_resolver import ResolvedContext` | Change to `from .models import ResolvedContext`. |

### `policy.py` — imports from deleted modules

| Line | Import | Resolution |
|------|--------|------------|
| 10 | `from .classifier import IntentCategory` | Change to `from .models import IntentCategory`. |

## Tasks

- [ ] 1. **Rewrite `runtime.py`** — gut to ~300 lines: init + process wrapper + helpers
- [ ] 2. **Patch `TieredDispatcher`** — remove feature flag, remove fallback, add pending-follow-up check before triage. Remove `reuse_decision` import (inline or move to gatherer). Change `IntentCategory` import to `.models`.
- [ ] 3. **Patch `ContextGatherer`** — ensure fully self-contained: no dependency on `reuse_decision.py`. Inline the reuse-search call (~10 lines) if triage requests it.
- [ ] 4. **Patch `dispatcher.py`** — change `ResolvedContext` import from `.context_resolver` → `.models`
- [ ] 5. **Patch `policy.py`** — change `IntentCategory` import from `.classifier` → `.models`
- [ ] 6. **Wire lifecycle hooks** — `_on_any_session_complete` and `_on_root_session_complete` do all bookkeeping
- [ ] 7. **Wire cancellation** — `cancel_event` propagates from surface → root session → all children
- [ ] 8. **Reassurance timer** — `process()` still emits "Working on it..." via `ProgressSession`
- [ ] 9. **Remove `DAN_TIERED_DISPATCH` flag** — this is the only path now
- [ ] 10. **Update `__init__.py`** — gut from 326 → ~80 lines; export only kept modules
- [ ] 11. **Update `app.py`** — remove `follow_up` imports. If proactive triggers still needed, extract to standalone `triggers.py` or remove entirely.
- [ ] 12. **Integration tests** — full dispatch path: Tier 0, Tier 1, Tier 2 with children, cancellation

## Decisions

- **No feature flag.** The tiered dispatcher is the only path. `DAN_TIERED_DISPATCH` is deleted.
- **No legacy fallback.** If the dispatcher fails, the error surfaces to the user. No silent retry with the old path.
- **`ConcurrentDispatcher` stays.** It handles project-level parallelism (different concern). Stack: `ConcurrentDispatcher` → `ConciergeRuntime.process()` → `TieredDispatcher.dispatch()`.
- **`ProgressSession` stays.** The `process()` wrapper still manages reassurance timing.
- **`chat_manager` stays unchanged.** It's the LLM tool loop — the hands that do the work. The rewrite only changes the brain (routing/dispatch).
- **Outer stream hardening stays.** `app.py` still guarantees a terminal event per chat turn.

## Notes

- The key insight: `chat_manager.send_message_with_tools()` already handles the multi-turn tool loop. The old handlers/solver/executor layers were unnecessary indirection between the routing decision and the actual LLM call.
- Conversation turns are recorded only by the root session. Child sessions appear in telemetry and the session tree trace attached to the root turn's metadata.
- The `ContextGatherer` is the main efficiency win. The old flow ran memory + domain + reuse + artifacts speculatively for every message. The triage-informed gatherer only runs what's needed.
