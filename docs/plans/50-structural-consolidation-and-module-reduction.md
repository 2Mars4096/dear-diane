# 50: Structural Consolidation and Module Reduction

**Status:** not-started
**Goal:** Tighten architectural ownership, make shared invariants callee-owned, split the largest behaviorful modules along real boundaries, and prune redundant code without hurting performance.

## Problem

The latest structural review found a consistent failure mode:

- new helpers, facades, and runtime packages were added, but the previous owner often kept the same responsibility
- strong invariants now exist (`workflow_guards.py`, workflow identity helpers, capability modules), but several are still caller-owned instead of enforced by the authoritative callee
- a few files remain too large because they still mix multiple boundaries:
  - `src/dan/server/chat_manager.py`
  - `src/dan/server/run_manager.py`
  - `src/dan/server/concierge/runtime/__init__.py`
  - `src/dan/server/agent_runtime/workflow_generation.py`
  - `src/dan/server/graph_mutator.py`
  - `src/dan/server/concierge/scheduler.py`
  - `src/dan/engine/scheduler.py`
  - `src/dan/server/routers/adapters.py`
  - `editor/src/components/ChatPanel.tsx`
  - `editor/src/store/useGraphStore.ts`
- Worker-native naming exists, but runtime-core execution still materially relies on compatibility delegation
- `AppState` exists, but router/service access still leaks through globals-backed compatibility paths

The risk is not just file size. The deeper problem is boundary drift: behavior spreads across old owners and new owners at the same time, which raises abstraction count faster than clarity.

## Scope

In scope:

- callee-owned workflow apply/run invariants and authoritative run launch behavior
- retirement of false facades (`chat_manager.py`, `capability_handlers.py`)
- splitting lifecycle vs finalization in `RunManager`
- narrowing concierge runtime vs scheduler authority vs fast-command/schedule semantics
- standardizing concierge/worker connection contracts enough to remove duplicate queue/context/prompt steps before any reusable-bundle extraction
- splitting workflow-generation and graph-mutation sinks along real phase boundaries
- reducing Worker/legacy compatibility debt in runtime-core paths
- removing globals-backed `AppState` compatibility after typed accessors are in place
- pinning down the key scripts/files, setting split/pruning guardrails, and doing targeted deletion of redundant code
- frontend sink reduction for oversized editor/chat state owners (including `ConfigPanel.tsx` and store spillover in `useMessagingStore.ts`)

Out of scope:

- a big-bang rewrite of the server, concierge, or Worker runtime
- net-new product features unrelated to structural cleanup
- speculative new abstraction layers that do not replace multiple existing ones
- performance regressions in hot paths or larger frontend bundles hidden behind refactors

## Key Hotspots At Plan Start

| File | Lines | Current structural risk |
|---|---:|---|
| `editor/src/components/ChatPanel.tsx` | 5151 | UI, thread, stream, run, persistence, reconnect, and branch state all mixed |
| `src/dan/engine/scheduler.py` | 4154 | engine bootstrap, execution, lint, retries, telemetry, and mutation hooks intertwined |
| `src/dan/server/chat_manager.py` | 3759 | compatibility-named facade that still owns workflow orchestration |
| `src/dan/server/concierge/runtime/__init__.py` | 3624 | orchestration, command adaptation, UX signaling, and scheduler bridging mixed |
| `src/dan/builder/builder.py` | 3053 | DSL construction, validation, serialization, and Worker-projection mixed |
| `editor/src/components/ConfigPanel.tsx` | 2982 | node/edge config editing, schema validation, and form rendering mixed |
| `src/dan/executors/control_flow.py` | 2859 | branching, looping, routing, and error-handling execution all in one file |
| `editor/src/store/useGraphStore.ts` | 2533 | graph/editor state mixed with broader workflow/chat session concerns |
| `src/dan/server/routers/adapters.py` | 2459 | adapter state, routing, callback protocol, and streaming all mixed |
| `src/dan/meta/planner.py` | 2384 | intent decomposition, candidate ranking, and plan repair in one module |
| `src/dan/server/graph_mutator.py` | 2259 | apply engine, macro authoring, migration policy, and repair logic mixed |
| `src/dan/server/run_manager.py` | 2155 | lifecycle mixed with finalization/learning/reflection tail |
| `src/dan/server/concierge/tier_executors.py` | 2146 | tiered dispatch execution mixed with capability/chat/mutation event handling |
| `src/dan/server/agent_runtime/workflow_generation.py` | 1983 | planning, candidate build, diagnosis, repair, and recovery all in one flow |
| `src/dan/server/concierge/scheduler.py` | 1875 | daemon authority mixed with user-facing schedule semantics |

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [50-1](50-1-key-script-inventory-and-refactor-guardrails.md) | Key Script Inventory and Refactor Guardrails | Pin down the real owners, key scripts, split budgets, and pruning/perf rules | P1 | not-started |
| [50-2](50-2-workflow-invariant-ownership-and-run-launch.md) | Workflow Invariant Ownership and Run Launch | Make run/apply invariants callee-owned and consolidate run launch/relay | P1 | not-started |
| [50-3](50-3-chat-and-capability-facade-retirement.md) | Chat and Capability Facade Retirement | Remove workflow/domain ownership from `chat_manager.py` and `capability_handlers.py` | P1 | not-started |
| [50-4](50-4-run-manager-lifecycle-and-finalization-split.md) | Run Manager Lifecycle and Finalization Split | Separate lifecycle authority from reflection/learning/telemetry finalization | P1 | not-started |
| [50-5](50-5-concierge-runtime-and-scheduler-boundary-cleanup.md) | Concierge Runtime and Scheduler Boundary Cleanup | Narrow orchestration vs fast-command/schedule semantics vs scheduler authority | P1 | not-started |
| [50-6](50-6-workflow-authoring-and-large-module-narrowing.md) | Workflow Authoring and Large-Module Narrowing | Split workflow generation, graph mutator, and one secondary backend giant | P1 | not-started |
| [50-7](50-7-worker-bridge-retirement-and-appstate-cleanup.md) | Worker Bridge Retirement and AppState Cleanup | Remove runtime-core bridge defaults and finish typed service access | P2 | not-started |
| [50-8](50-8-frontend-sink-reduction-and-pruning.md) | Frontend Sink Reduction and Pruning | Narrow oversized editor/chat state owners and prune redundant code | P2 | not-started |

## Dependencies / Sequencing

Recommended order:

```text
50-1 (inventory, ownership map, prompt seam map, guardrails)
  ↓
50-2 (callee-owned invariants and authoritative run launch)
  ├→ 50-3 (chat/capability facade retirement)
  ├→ 50-4 (RunManager lifecycle/finalization split)
  └→ 50-5 (concierge runtime/scheduler narrowing)
         ↓
  46-6 (concierge prompt pipeline cleanup + prompt quality + handoff envelope)
         ↓
50-6 (workflow authoring + backend large-module narrowing)  [if needed]
50-7 (Worker bridge retirement + AppState cleanup)          [if needed]
50-8 (frontend sink reduction + pruning sweep)              [if needed]
```

46-7 (Worker bundle extraction) is deferred until an external consumer exists.

Rationale:

- the first step must identify the true key scripts/owners and define performance guardrails, otherwise later splits will drift into style cleanup instead of structural cleanup
- callee-owned invariants need to land early so later file splits are built on one authoritative run/apply contract
- false facade retirement should happen before deep phase extractions, otherwise the same behavior will keep leaking back into the old owners
- 50-6 must follow 50-3 and 50-5 (not run in parallel) because workflow-generation and graph-mutator splits depend on chat/capability ownership and concierge runtime boundaries being settled first; the sub-plan text already says "coordinate with 50-3" and "coordinate with 50-5"
- 46-6 (concierge prompt pipeline cleanup) runs right after 50-5 settles the concierge file boundaries. It is the highest-leverage remaining work: better prompts → better agent output
- 50-6, 50-7, 50-8 are marked "[if needed]" — they address real structural debt but can be deferred until the debt actively blocks product work
- 46-7 is deferred until an external consumer exists for the Worker bundle
- frontend sink reduction benefits from the backend ownership seams stabilizing first

## Success Criteria

- the repo has one explicit inventory of key scripts/modules, real owners, and split/pruning guardrails (deliverable: `docs/key-scripts.md` or a dedicated section in `docs/architecture.md`)
- workflow run/apply invariants are callee-owned rather than reimplemented across callers
- `chat_manager.py` and `capability_handlers.py` are honest thin boundaries or deleted as behaviorful owners
- `RunManager` owns lifecycle while a separate boundary owns post-run finalization/learning
- concierge runtime and scheduler no longer mix orchestration, command adaptation, and daemon authority in the same sink files
- at least two oversized backend files and two oversized frontend files are narrowed by real boundary splits, each losing at least 30% of lines or shedding at least one of its listed responsibilities entirely
- Worker compatibility bridges are more contained and no longer the default path for the compute families that have native Worker execution
- globals-backed `AppState` compatibility is removed from the normal dependency surface
- targeted pruning removes redundant code in touched areas, and focused validation/perf checks show no material regression

## Relationship to 49-Series

The 49 concierge-service hardening plans (49-1 through 49-5) target `concierge/runtime/__init__.py` and `scheduler.py` — the same files 50-5 narrows. The 50-series is sequenced after 49 structurally: 49 adds first-class task lifecycle and dispatch-mode contracts, while 50-5 narrows the resulting file boundaries. If 49 work is still in-flight when 50-5 starts, coordinate to avoid conflicting ownership moves in the same files.

## Relationship to 46-Series

**Concierge-first architecture:** 46-6 declares the concierge as the single execution brain for DAN chat. Workers are dispatched compute nodes, not a parallel agent runtime. This simplifies the structural cleanup:

- **50-series narrows the files** — 50-1 inventories seams, 50-5 extracts schedule/progress modules, 50-3 retires facades.
- **46-6 cleans up the concierge prompt pipeline** — slot-based prompt assembly, stage overlay quality, typed handoff envelope, prompt duplication pruning. Runs after 50-2/50-3/50-5 produce cleaner file boundaries.
- **46-7 is deferred** — Worker bundle extraction waits until there is an external consumer. No speculative architecture.

The earlier framing that tried to "unify two orthogonal paths" is replaced. Workers handle workflow-node execution via `worker_resources` catalogs; the concierge handles chat execution via its prompt pipeline. Two different callers, not two competing architectures.

## Decisions

- **Patch-first, subtractive refactoring only.** Do not add a new subsystem unless it replaces multiple existing code paths.
- **Old owners must shrink in the same patch.** If a new helper/module takes responsibility, the previous owner must lose it immediately.
- **Shared invariants belong at the callee boundary.** Routes, adapters, and helpers should not each remember to re-enforce core workflow/run contracts.
- **Concision must preserve hot-path performance.** Use focused validation and performance spot checks instead of “cleaner code” claims.
- **Key scripts are both technical and organizational.** The inventory should name authoritative owners, not just longest files.

## Notes

- This plan is the patch-first response to the latest structural review, which concluded that the repo is still fundamentally coherent but overgrown and drifting at its seams.
- The 50-series deliberately treats “split long files,” “pin down key scripts,” and “prune unnecessary code” as one connected effort. File size alone is not the problem; mixed ownership is.
- The essential sequence is **50-2 → 50-3 → 50-5 → 46-6**. This gets the highest-leverage structural and prompt-quality wins. The remaining plans (50-4, 50-6, 50-7, 50-8, 46-7) are real debt but deferrable until they actively block product work. After the essential sequence, new work should be product features and capability expansion, not structural repair.
