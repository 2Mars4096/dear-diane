# 46-7: Reusable Worker Bundle Extraction

**Parent:** [46-universal-worker-primitive](46-universal-worker-primitive.md)
**Status:** completed
**Priority:** active — sequenced after 46-6.
**Goal:** Extract the Worker contract primitive (input → execute → output) as a DAN-independent package that other projects can embed, while consuming the normalized task/evidence/output-contract surface defined by 46-6 instead of inventing a second request shape.

## Scope Change

The 46-6 decision that "concierge is the brain, Workers are hands" significantly simplifies this plan. The reusable core is **just the Worker compute contract** — not an entire agent runtime with its own prompt pipeline, memory system, and tool registry.

The follow-up constraint from 46-6 is important too: the bundle should not define a rival prompt contract. It should accept a caller-supplied execution request built from the same minimum contract DAN uses for concierge root turns, concierge child turns, and workflow Worker nodes:

- task
- scope / constraints
- evidence / context
- trust / provenance labels
- output contract

What gets extracted:
- Worker model (`WorkerConfig`, `ContextBindings`, `LLMHints`, roles)
- Worker executor dispatch (mode detection, code/LLM/tool/composite routing)
- Worker shared-resource resolution (`worker_resources` catalog)
- Minimal interfaces for model/completion, tool calls, memory access
- A caller-facing execution request / handoff surface that can carry the 46-6 minimum contract without DAN-specific prompt assembly

What stays DAN-specific:
- Concierge (prompt assembly, triage, queue, task lifecycle)
- Engine scheduler (graph execution, checkpoints, retries)
- Capability registry, memory kernel, provider registry
- All prompt quality and stage overlay content
- Slot prioritization, truncation-budget policy, and stage-overlay wording

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

- **46-6** must land first so the concierge-vs-Worker boundary is settled and the normalized contract surface exists. 46-7 should consume these concrete outputs from 46-6:
  - shared minimum contract across concierge root / concierge child / workflow Worker prompts
  - explicit trust/provenance labels
  - typed handoff envelope with definition-of-done and expected return shape
  - slot-level prompt budget semantics, even if the bundle itself does not own prompt assembly
- Coordinate with **50-7** for Worker bridge retirement — less legacy coupling means less to adapter-wrap. If 50-7 has not landed yet, 46-7 adapter-wraps the remaining coupling instead of waiting.

## Tasks

- [x] 1. Define bundle boundary
  - [x] 1-1. Create `dan/worker/core/` (or similar) with zero imports from `dan.server.*`, `dan.models.legacy`, `dan.executors.*`.
  - [x] 1-2. Define interfaces: `CompletionProvider`, `ToolProvider`, `MemoryProvider`, `EventSink`.
  - [x] 1-3. Define a caller-facing `ExecutionRequest` / `WorkRequest` contract that carries at least: task, constraints, evidence/context blocks, trust labels, and output contract. This should be the externalized form of the 46-6 minimum contract, not a new DAN-only shape.
  - [x] 1-4. Keep DAN-specific executor delegation, legacy bridge, and tool registry binding in `dan/worker/adapters/`.
- [x] 2. Sever imports
  - [x] 2-1. `executor.py` → import from core interfaces instead of `dan.executors.*` directly.
  - [x] 2-2. `model.py` → define minimal port/context types in core instead of importing `dan.models.*`.
  - [x] 2-3. `presets.py` → stays in DAN adapter layer (it is inherently a bridge).
  - [x] 2-4. Keep prompt-slot assembly out of the core. The bundle may accept normalized evidence and output-contract fields, but it must not pull in concierge stage overlays, chat prompt modules, or DAN-specific truncation logic.
- [x] 3. Prove it works
  - [x] 3-1. Add import-boundary test scanning core for forbidden DAN imports.
  - [x] 3-2. Add minimal non-DAN fixture: create a Worker, give it stub adapters, run it, get a result.
  - [x] 3-3. Add one fixture proving the same `ExecutionRequest` shape can be built from a DAN 46-6-style handoff (`definition_of_done`, `expected_return_shape`, trust-labeled evidence blocks) and from a non-DAN harness without changing the core bundle API.
- [x] 4. Rollout
  - [x] 4-1. Keep DAN runtime stable while routing through adapter-backed core.
  - [x] 4-2. Verify `worker_resources` catalog resolution still works through the adapter layer.
  - [x] 4-3. Document the adapter boundary clearly: DAN concierge still owns prompt wording and prompt budgets; the reusable bundle owns only execution against the normalized request contract.

## Success Criteria

- Worker core imports cleanly without DAN server/legacy/executor modules
- A non-DAN fixture proves the bundle runs with stub adapters
- The same normalized request shape works for DAN handoffs and non-DAN harnesses
- The core bundle does not reintroduce DAN-specific prompt assembly or stage-overlay content
- Import-boundary test enforces the ceiling

## Decisions

- Internal extraction first. External publication can follow once the boundary is proven.
- The reusable core is the Worker compute contract, not an agent runtime. The concierge is not extracted.
- `LegacyWorkerAdapterExecutor` stays as a DAN adapter, not a core primitive.
- 46-7 consumes the contract outputs of 46-6; it does not redefine task/evidence/output semantics a second time.

## Notes

- The 46-6 simplification (concierge-first, Workers as compute) made this plan much smaller. The earlier version tried to extract an entire agent runtime; now it just extracts the compute contract plus a normalized caller-facing request shape.
- Even without an external consumer today, the extraction forces the import-ceiling and adapter-boundary discipline that improves the internal codebase. The reusable bundle is a forcing function for clean boundaries, not just a distribution artifact.
