# 34: Concierge Complete Rewrite — Tiered Async Dispatcher

**Status:** in-progress
**Goal:** Delete the monolithic 6,700-line `runtime.py` and its 10,000+ lines of satellite modules (classifier, solver, executor, entity grounding, guards, etc.). Replace with a clean tiered dispatcher architecture where the concierge does exactly three things: **understand → assign → track**. Everything else — context, execution, memory, telemetry — lives inside tier executors or lifecycle hooks.

## Problem

`runtime.py` is 6,681 lines with 100 methods. The concierge module is 41 files / 25,700 lines total. The legacy path is a sequential cascade of:
- 8+ heuristic pre-filters before the LLM sees the message
- Separate LLM classification call
- 3 guard passes
- Correction detection
- Entity grounding heuristics
- Speculative parallel context gathering (memory, domain, reuse, artifacts — all fetched regardless of whether they're needed)
- Policy enforcement
- Inline handler dispatch
- Post-processing + turn recording + memory storage + telemetry

The code has accumulated dozens of hardcoded workarounds, stagnation counters, phrase bags, and inline try/except patches. It's untrackable.

A concierge should: **understand → assign → track**. Everything else is someone else's job.

## Architecture

```
User Message
     │
     ├─ Slash command? (/help, /status) ──► Instant response (no LLM)
     │
     ▼
┌─────────────────────┐
│     CONCIERGE        │
│                      │
│  1. Triage (1 LLM)  │  ← single call replaces heuristic cascade + classifier
│  2. Gather context   │  ← targeted by triage (not speculative)
│  3. Assign tier      │
│  4. Dispatch session │
│  5. Track lifecycle  │
└──────────┬──────────┘
           │
     ┌─────┼─────────────┐
     │     │             │
     ▼     ▼             ▼
  Tier 0  Tier 1      Tier 2
  Instant Single-shot  Multi-step
           │          Recursive
           │             │
           │        ┌────┼────┐
           │        ▼    ▼    ▼
           │      child sessions
           │      (Tier 1 or 2)
           │         │
           │    ┌────┼────┐
           │    ▼    ▼    ▼
           │   grandchild sessions
           │    (max_depth enforced)
           │
     ◄─────┘
  Results bubble up
```

## Tiers

| Tier | What | Examples | Execution | Depth |
|------|-------|---------|-----------|-------|
| 0 | Instant | `/help`, "Hi", "Yes" (confirm), "2" (option) | Inline, no session | 0 |
| 1 | Single-shot | Q&A, file lookup, status check, experience query | One `send_message_with_tools()` call | 0 |
| 2 | Multi-step | Agent tasks, research, workflow builds, long-horizon goals | Recursive session tree | 0..N |

## Key Design Principle: Executors Call `chat_manager` Directly

The old architecture had handlers → executor → solver → handler dispatch → chat_manager. The new architecture is flat:

- **Tier 1**: builds a prompt + tool set from triage context, calls `chat_manager.send_message_with_tools()` once
- **Tier 2**: same thing, but can decompose into child sessions first. Each child does the same.

No handler wrappers. No solver. No executor indirection. The tier executor builds the right prompt and calls the LLM directly.

## Recursive Session Tree (Tier 2)

A Tier 2 session receives a task and decides: **handle directly** or **decompose into sub-tasks**.

```
Root Session (Tier 2, depth=0) — "Write a comprehensive supply chain report"
├── Child A (Tier 2, depth=1) — "Research supply chain disruptions"
│   ├── A1 (Tier 1, depth=2) — "Search for recent news on port congestion"
│   ├── A2 (Tier 1, depth=2) — "Search for academic papers on resilience"
│   └── A3 (Tier 1, depth=2) — "Read and summarize uploaded PDF"
├── Child B (Tier 2, depth=1) — "Analyze data trends"
│   ├── B1 (Tier 1, depth=2) — "Read CSV file"
│   └── B2 (Tier 1, depth=2) — "Compute summary statistics"
└── Child C (Tier 1, depth=1) — "Draft executive summary from children's results"
```

Key properties:
- **Max depth** is configurable (`DAN_SESSION_MAX_DEPTH`, default 4)
- **Max children** per session (`DAN_SESSION_MAX_CHILDREN`, default 8)
- **Max total sessions** per root (`DAN_SESSION_MAX_TOTAL`, default 32)
- Children can run in parallel (independent) or serial (dependent)
- Parent aggregates child results before continuing
- Any session can be paused/resumed/cancelled
- Progress from children bubbles up through the tree
- **Every session emits telemetry** — tokens, cost, duration, model, tools used
- **Full tree trace** is attached to the assistant turn metadata

## Sub-Plans (Execution Order)

The dependency chain is strict — each step unblocks the next:

1. [x] [34-1: Session Model & Lifecycle](34-1-session-model.md) — `Session`, `SessionState`, `SessionResult`, `SessionManager`, tree operations, depth enforcement *(complete — no changes needed)*
2. [ ] **[34-5 Phase 0](34-5-legacy-deletion.md)** — Relocate shared types (`IntentCategory`, `RouteDecision`, `RouteMode`, `ResolvedContext`) to `models.py`. Safe, no behavior change. **Unblocks everything else.**
3. [ ] [34-2: Triage LLM Call](34-2-triage-llm.md) — Clean `triage.py`: remove `classifier`, `context_resolver`, `resume` imports. Remove `ClassificationResult` production, heuristic fallback.
4. [ ] [34-3: Tiered Execution](34-3-tiered-execution.md) — Rewrite tier executors: remove `classifier` + `handlers` imports. Executors call `chat_manager.send_message_with_tools()` directly.
5. [ ] [34-4: Integration — Complete Rewrite](34-4-integration.md) — Rewrite `runtime.py` to ~300 lines. Patch `dispatcher.py`, `policy.py` imports. Wire tiered dispatcher as the only path.
6. [ ] **[34-5 Phases 1-5](34-5-legacy-deletion.md)** — Delete 18 modules, resolve external deps, delete ~23 test files, gut `__init__.py`, rewrite docs. **Runs last after everything else is verified.**

## Prerequisite: Type Relocation

Before deleting any module, relocate shared types that kept modules depend on:

| Type | Current Home | New Home | Used By |
|------|-------------|----------|---------|
| `IntentCategory` | `classifier.py` | `models.py` | `policy.py`, `triage.py`, `tier_executors.py`, `tiered_dispatch.py` |
| `RouteDecision` | `classifier.py` | `models.py` | `triage.py`, `tier_executors.py` |
| `RouteMode` | `classifier.py` | `models.py` | `triage.py`, `tier_executors.py` |
| `ResolvedContext` | `context_resolver.py` | `models.py` | `triage.py`, `dispatcher.py`, `tiered_dispatch.py` |

All four are small Pydantic models / enums (<20 lines total). Move them, update imports in every kept module, then delete the source modules.

## What Gets Deleted

| Module | Lines | Reason |
|--------|-------|--------|
| `classifier.py` | 1,281 | Replaced by `triage.py` (types relocated to `models.py` first) |
| `handlers.py` | 1,289 | Tier executors call `chat_manager` directly |
| `solver.py` | 406 | Tier 2 decomposition replaces solver planning |
| `executor.py` | 255 | Tier executors replace this |
| `entity_grounding.py` | 445 | Triage handles entity extraction |
| `context_resolver.py` | 327 | `ContextGatherer` replaces this (type relocated to `models.py` first) |
| `resume.py` | 371 | Triage handles resume detection |
| `continuity.py` | 425 | Triage handles surface-switch detection |
| `completion_guard.py` | 610 | Tier executor post-processing |
| `build_session.py` | 929 | Tier 2 session variant |
| `goal_loop.py` | 772 | Tier 2 session variant |
| `boundary_handoff.py` | 451 | Session lifecycle hooks |
| `reuse_decision.py` | 352 | Triage/ContextGatherer input |
| `memory_bridge.py` | 249 | `ContextGatherer` |
| `learning.py` | 241 | Session lifecycle hooks |
| `follow_up.py` | 540 | Session lifecycle hooks |
| `promotion.py` | 47 | Session lifecycle hooks |
| `queue.py` | 43 | Not needed (tiered dispatch handles flow) |
| `runtime.py` (legacy body) | ~5,500 | Rewritten to ~300 lines |
| **Total deleted** | **~13,500** | |

## What Gets Kept

| Module | Lines | Why | Cleanup needed? |
|--------|-------|-----|-----------------|
| `models.py` | ~130 | `SurfaceMessage`, `Project`, `Task` + relocated types (`IntentCategory`, `RouteDecision`, `RouteMode`, `ResolvedContext`) | Add relocated types |
| `project_store.py` | 234 | Project/task persistence — used by triage + executors | None |
| `command_registry.py` | 947 | Slash command dispatch — orthogonal | None |
| `identity.py` | 133 | Bot name/identity | None |
| `fan_out.py` | 128 | Async parallel utility | None |
| `policy.py` | 157 | Action/execution policy models | Change import: `classifier` → `models` |
| `progress.py` + `progress_ux.py` | 1,054 | Progress reporting | None |
| `pii_tokenizer.py` | 545 | PII masking | None |
| `computer_use.py` + `computer_policy.py` | 993 | Browser automation | None |
| `scheduler.py` | 1,241 | Task scheduling — orthogonal | None |
| `dispatcher.py` | 424 | `ConcurrentDispatcher` — project-level parallelism | Change import: `context_resolver` → `models` |
| `intent_catalog.py` | 138 | Routing ontology — used by triage prompt | None |
| `actions.py` | 170 | Action models | None |
| `resources.py` | 215 | Resource budget | None |
| `domain_learning.py` | 998 | Domain knowledge templates — used by `memory_kernel.py` | None (self-contained) |
| `session.py` | 393 | Session model (34-1) | None |
| `triage.py` | 598 | Triage (34-2, patched) | Remove imports from `classifier`, `context_resolver`, `resume` |
| `tier_executors.py` | 840 | Tier executors (34-3, rewritten) | Remove imports from `classifier`, `handlers` |
| `tiered_dispatch.py` | 358 | Dispatcher (34-4, patched) | Remove imports from `reuse_decision`, `classifier` |
| `__init__.py` | ~80 | Exports (trimmed — only kept modules) | Gut from 326 → ~80 lines |

## New `runtime.py` (~300 lines)

```python
class ConciergeRuntime:
    def __init__(self, chat_manager, ...):
        self.chat_manager = chat_manager
        self.project_store = ProjectStore(...)
        self._session_manager = SessionManager()
        self._dispatcher = TieredDispatcher(
            session_manager=self._session_manager,
            triage_fn=triage,
            executors={...},
            context_gatherer=ContextGatherer(),
            concierge=self,
        )

    async def process(self, msg):
        # Reassurance timer + telemetry wrapper (keep existing)
        async for event in self._process_inner(msg):
            yield event

    async def _process_inner(self, msg):
        fast = await self._try_fast_command(msg)
        if fast is not None:
            yield fast
            return
        async for event in self._dispatcher.dispatch(msg):
            yield event
```

Init + process wrapper + slash commands + state/telemetry helpers. Nothing else.

## Success Criteria

- `runtime.py` goes from 6,681 lines to <400 lines
- Total concierge module goes from ~25,700 lines to ~11,000 lines (including `domain_learning.py`)
- 18 source modules deleted (~8,000 lines), plus ~6,400 lines gutted from `runtime.py`
- All shared types (`IntentCategory`, `RouteDecision`, `RouteMode`, `ResolvedContext`) relocated to `models.py`
- Zero imports from any deleted module in any kept module
- Tier 0 messages respond without any LLM call
- Tier 1 messages: 1 LLM call (triage) + 1 `send_message_with_tools()` call
- Tier 2 messages: recursive decomposition to configurable depth
- All chat surfaces (editor, CLI, Telegram, WhatsApp) work unchanged
- No feature flag — this is the only path
- `pytest tests/test_concierge/ -x` passes clean

## Non-Goals (this plan)

- Persistent session storage (in-memory only for now)
- Cross-surface session handoff (future)
- Session branching/forking (future)
- Visual session tree in UI (future)
