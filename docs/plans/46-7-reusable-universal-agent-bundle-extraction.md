# 46-7: Reusable Worker Bundle Extraction

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** not-started  
**Priority:** deferred — no external consumer exists yet. Execute only when there is a concrete embedding need.
**Goal:** Extract the Worker contract primitive (input → execute → output) as a DAN-independent package that other projects can embed.

## Scope Change

The 46-6 decision that "concierge is the brain, Workers are hands" significantly simplifies this plan. The reusable core is **just the Worker compute contract** — not an entire agent runtime with its own prompt pipeline, memory system, and tool registry.

What gets extracted:
- Worker model (`WorkerConfig`, `ContextBindings`, `LLMHints`, roles)
- Worker executor dispatch (mode detection, code/LLM/tool/composite routing)
- Worker shared-resource resolution (`worker_resources` catalog)
- Minimal interfaces for model/completion, tool calls, memory access

What stays DAN-specific:
- Concierge (prompt assembly, triage, queue, task lifecycle)
- Engine scheduler (graph execution, checkpoints, retries)
- Capability registry, memory kernel, provider registry
- All prompt quality and stage overlay content

## Problem: Current Coupling

Worker files still import DAN internals directly:

| Worker file | Imports from | Coupling |
|---|---|---|
| `executor.py` | `dan.executors.*` | 9 composed executor instances |
| `executor.py` | `dan.engine.*` | `ExecutionContext`, checkpoint, retry |
| `executor.py` | `dan.models.legacy.*` | Legacy node bridge dispatch |
| `model.py` | `dan.models.context/control_flow/nodes/ports` | Node union, port models |
| `presets.py` | `dan.models.legacy/control_flow` | Bridge conversion |

Only `roles.py` and `__init__.py` are clean.

## Dependencies

- **46-6** must land first so the concierge-vs-Worker boundary is settled.
- Coordinate with **50-7** for Worker bridge retirement — less legacy coupling means less to adapter-wrap.
- **Trigger:** only start this plan when there is a concrete need to embed Workers outside DAN (a second project, an SDK, a plugin system).

## Tasks

- [ ] 1. Define bundle boundary
  - [ ] 1-1. Create `dan/worker/core/` (or similar) with zero imports from `dan.server.*`, `dan.models.legacy`, `dan.executors.*`.
  - [ ] 1-2. Define interfaces: `CompletionProvider`, `ToolProvider`, `MemoryProvider`, `EventSink`.
  - [ ] 1-3. Keep DAN-specific executor delegation, legacy bridge, and tool registry binding in `dan/worker/adapters/`.
- [ ] 2. Sever imports
  - [ ] 2-1. `executor.py` → import from core interfaces instead of `dan.executors.*` directly.
  - [ ] 2-2. `model.py` → define minimal port/context types in core instead of importing `dan.models.*`.
  - [ ] 2-3. `presets.py` → stays in DAN adapter layer (it is inherently a bridge).
- [ ] 3. Prove it works
  - [ ] 3-1. Add import-boundary test scanning core for forbidden DAN imports.
  - [ ] 3-2. Add minimal non-DAN fixture: create a Worker, give it stub adapters, run it, get a result.
- [ ] 4. Rollout
  - [ ] 4-1. Keep DAN runtime stable while routing through adapter-backed core.
  - [ ] 4-2. Verify `worker_resources` catalog resolution still works through the adapter layer.

## Success Criteria

- Worker core imports cleanly without DAN server/legacy/executor modules
- A non-DAN fixture proves the bundle runs with stub adapters
- Import-boundary test enforces the ceiling

## Decisions

- Internal extraction only. No external publication until there is a real consumer.
- The reusable core is the Worker compute contract, not an agent runtime. The concierge is not extracted.
- `LegacyWorkerAdapterExecutor` stays as a DAN adapter, not a core primitive.

## Notes

- This plan is **deferred** per the review conclusion that there is no external consumer yet. The coupling table and task list are ready for when the need arises.
- The 46-6 simplification (concierge-first, Workers as compute) made this plan much smaller. The earlier version tried to extract an entire agent runtime; now it just extracts the compute contract.
