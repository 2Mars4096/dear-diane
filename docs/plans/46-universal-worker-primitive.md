# 46: Universal Worker Primitive

**Status:** in-progress
**Goal:** Introduce a single universal `Worker` primitive for compute and contract-bearing nodes, with a clean role system built on top, where most circumstances require zero configuration — it just works — without forcing all control-flow/runtime semantics into the same primitive.

## Vision

When people ask "how do you define workers?", the answer is:

> We have one meta worker. It's a minimal contract orchestrator. We build roles on top of it — LLM agent, tool runner, script executor, manager, reviewer — but most of the time you don't configure anything. You give it a prompt and tools, it figures out the rest.

The Worker is not another agent framework. It's a **contract boundary**: input schema → execute → output schema. How it executes is just configuration. An LLM call is one possible execution mode. A script is another. No execution at all (pure pass-through) is another. The Worker doesn't care — it manages the contract.

This plan is intentionally **not** claiming that every runtime/control concept in DAN should literally become a Worker. `Worker` is the universal compute/contract primitive. Pure control-flow semantics such as branch selection, loop scheduling, fork/join, and turn-taking protocols may remain specialized runtime primitives even when they are exposed through Worker-friendly authoring surfaces.

The Worker must also stay **lightweight**. Shared resources such as tool definitions, memory contents, long instruction packs, provider settings, retry defaults, and run history should usually live outside the Worker and be referenced explicitly. Workers need clear handles to those systems and a predictable way to resolve them when needed, but they should not inline every piece of global context.

## Zero-Config Philosophy

A bare `Worker` with just an `id` is a valid node. It passes inputs through to outputs. Add a `model` and it becomes an LLM agent. Add `code` and it becomes a script runner. Add `tool_ids` and it becomes a tool executor. Add `body_graph` and it becomes a composite worker. Add `sub_workers` and it becomes a coordinator over other workers.

You never have to specify `role="executor"` or `behavior="llm"`. The Worker auto-detects its execution mode from what's configured. Roles are optional labels for human readability and preset loading — not execution switches.

**Script-first, token-second.** If something can be done with a script, don't burn tokens. The executor dispatch explicitly prefers: code > direct tool invocation > LLM with tools > LLM only.
**Reference-first, bulk-second.** If context can be inherited or resolved through a shared registry, don't copy it onto every Worker. The Worker should carry clear references and local overrides, not duplicated global state.

## Role System

Roles are named configuration presets, not different types. A "reviewer" is just a Worker with a review-oriented persona and validation tools. A "manager" is just a Worker with `authority=LEAD` and sub-workers.

Built-in roles ship as factory functions:

```python
from dan.worker import role

reviewer = role("reviewer", model="claude-4", persona="Validate output quality...")
manager  = role("manager", model="claude-4", sub_workers={"team_a": "sg_a"})
fetcher  = role("tool_runner", tool_ids=["web_search", "web_fetch"])
coder    = role("script", code="import json; result = json.loads(data)")
```

Custom roles are just Workers with custom config. No registration needed.

## Clean Module Structure

The Worker lives in its own top-level module, not scattered across `models/` and `executors/`:

```
src/dan/worker/
  __init__.py       # public API: Worker, WorkerConfig, role(), WorkerAuthority
  model.py          # Worker, WorkerConfig, WorkerAuthority, LLMHints, shared-ref policies
  executor.py       # WorkerExecutor (universal dispatch)
  roles.py          # Built-in role factories and preset registry
  presets.py        # Legacy type ↔ Worker conversion (for 46-3 equivalence)
```

Downstream code imports from `dan.worker`:
```python
from dan.worker import Worker, WorkerConfig, role
```

## Current State: Why This Is Needed

DAN has 21 node types, 20 executor registrations, 15+ builder methods. The compute/contract surface is fragmented, and the composite-node contract (10 fields) is copy-pasted across 7 types. This is not compact enough to manage or scale.

The Worker collapses the agent-like surface: one primary compute model, one primary compute executor, one primary compute builder method. Old compute-oriented types become legacy aliases that deserialize into Workers. Control-flow/runtime primitives may still remain specialized under the scheduler/executor layer.

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [46-1](46-1-worker-model-and-type-system.md) | Worker Model & Type System | `Worker`, `WorkerConfig`, `WorkerAuthority` with clean field organization; `LLMHints` and reference-based shared context; canonical contract metadata surface; add to `Node` union | P0 | in-progress |
| [46-2](46-2-worker-executor-and-dispatch.md) | Worker Executor & Auto-Detection | Universal `WorkerExecutor` with auto-detected execution mode; script-first dispatch; reuses existing executor internals | P0 | completed |
| [46-3](46-3-type-derivation-and-equivalence.md) | Roles, Presets & Equivalence Proof | Define built-in roles; build legacy-type ↔ Worker presets; prove Worker coverage for compute types and compatibility parity for control/runtime types | P0 | completed |
| [46-4](46-4-builder-dsl-and-authoring.md) | Builder DSL & Authoring Integration | `worker()` builder method; `worker_scope()` for composites; compiler/decompiler round-trip; preserve worker contract metadata through authoring surfaces | P1 | completed |
| [46-5](46-5-migration-and-compaction.md) | Migration, Deprecation & Compaction | Conversion utilities; gradual migration; worker-first generation surfaces; codebase compaction; clean documentation | P2 | completed |
| [46-6](46-6-concierge-worker-standardization.md) | Concierge/Worker Standardization | Standardize the turn -> queue -> task -> session -> executor -> result path, including handoff envelopes, capability connections, and prompt evolution | P1 | not-started |
| [46-7](46-7-reusable-universal-agent-bundle-extraction.md) | Reusable Universal-Agent Bundle Extraction | Extract a DAN-independent universal-agent core with adapter-based DAN integration and a proof that other projects can embed it cleanly | P1 | not-started |

## Dependencies / Sequencing

```text
46-1 (Worker Model)
  ↓
46-2 (Worker Executor)
  ↓
46-3 (Roles & Equivalence)
  ↓
46-4 (Builder & Authoring)
  ↓
46-5 (Migration & Compaction)
  ↓
46-6 (Concierge/Worker Standardization)
  ↓
46-7 (Reusable Universal-Agent Bundle Extraction)
```

Strictly serial. The model must exist before the executor, the executor before the equivalence proof, the proof before builder integration, the Worker-first migration before connection/prompt standardization, and the standardized runtime contracts before any reusable-bundle extraction.

## Implementation Lane

1. Execute plan 46 in a dedicated worktree separate from unrelated feature work.
2. Treat the real gate into plan 47 as stabilization of the Worker-first contract surface, not literal completion of every 46-5 rollout/compaction tail item.
3. Once Worker-first authoring, generation, and contract metadata are stable, 47 may proceed in a follow-on branch/worktree even if some 46 cleanup remains.
4. After the Worker-first surface is stable, standardize the concierge/root/child/worker execution contracts and prompt lifecycle before claiming the Worker is a reusable standalone bundle.
5. Only after those contracts are standardized should the reusable universal-agent core be extracted from DAN-specific adapters.

## Success Criteria

- A bare `Worker()` with no config is a valid node that passes data through
- Adding `model`, `code`, or `tool_ids` auto-selects the right execution mode — no explicit mode flag needed
- Every existing compute-oriented node type has a proven Worker equivalent, and every retained control/runtime type has a proven compatibility path (roles/presets from 46-3)
- All existing tests pass unchanged after adding `Worker` to the `Node` union
- At least one full workflow runs identically when expressed with Workers
- The `src/dan/worker/` module is self-contained and clean
- the concierge/root/child/worker path has one documented and standardized contract for queueing, handoff, capability access, and prompt evolution
- Built-in roles cover the common cases; custom roles are trivial to define
- Worker-native graphs preserve enough contract metadata (`description`, `role`, `persona`, port descriptions/schemas, boundary schemas) for downstream lint-config generation without per-type special-casing
- Workers can reference shared instruction, memory, tool, provider, and retry bundles through explicit fields that are easy to resolve at runtime
- Async/parallel behavior is explicit: workers run concurrently when dependencies and locks allow; downstream publication remains blocked on the tiered lint gate and any required resource locks
- a DAN-independent universal-agent core can be embedded by a non-DAN harness through adapter interfaces instead of importing DAN server/runtime internals directly

## Key Design Principles

1. **It just works.** Minimal config produces useful behavior. Auto-detection over explicit mode flags. Smart defaults everywhere.
2. **Contract orchestrator, not LLM wrapper.** The Worker manages input/output contracts. Execution is a pluggable detail.
3. **Script-first, token-second.** Prefer deterministic code over LLM calls. Save tokens for what actually needs reasoning.
4. **Roles, not types.** Behavior differences come from configuration presets (roles), not different Python classes.
5. **One primary compute model, one primary compute executor, one primary compute builder method.** That's the compaction target. Specialized control/runtime primitives may still exist below or beside that surface.
6. **Backward compatible.** Old graphs deserialize and execute unchanged. Migration is gradual and opt-in.
7. **Power by reference.** Workers point at shared context systems; they do not duplicate them.
8. **Explicit governance and blocking.** Delegation, spawning, task-tier caps, and resource-lock behavior should be visible in the model/runtime contract, not hidden in ad hoc scheduler logic.

## Decisions

- The Worker model has three clear field groups: **Identity** (role, persona, authority), **Capability** (model, tool_ids, code), **Composition** (body_graph, sub_workers, contract). LLM-specific tuning knobs live in an optional `LLMHints` sub-model, not flat on Worker.
- Control and validation metadata may still appear on Worker as optional contract hints, but this plan does not require every control-flow/runtime behavior to be executed solely by `WorkerExecutor`.
- The `src/dan/worker/` module is the canonical home. `models/worker.py` and `executors/worker.py` do not exist — everything lives under `worker/`.
- Human-style interaction remains derivable as Worker behavior (`tool_ids=["human_input"]`). Pure control primitives such as `if_else`, `while_loop`, and `goal_loop` do not need to be erased into a monolithic Worker implementation if specialized runtime semantics remain clearer and more stable.
- Plan 46 is the implementation prerequisite for plan 47. The linter module remains separate, but 46 is where the canonical contract/intent surface is normalized so 47 can consume it without special-casing the legacy node zoo.
- Worker `description`, `persona`, `role`, port descriptions, and boundary schemas are contract metadata, not UI decoration. Builder/compiler/decompiler/generation surfaces must preserve them faithfully.
- `parallel_subagents`, `orchestrator`, and `team` likely sit in the middle: the participating agents should be Workers, but the interaction pattern itself may compile to fork/join, routing, turn-taking, and stop-policy primitives rather than becoming one opaque super-Worker.
- Shared context must be referenced explicitly. The final Worker should know which instruction profiles, memory policies, context bundles, tool bundles, provider policies, and retry defaults it can resolve, without carrying the full contents inline.
- Delegation/spawn authority and task-tier caps are part of the Worker contract. They should be inspectable and enforceable, not left implicit.

## Notes

- `src/dan/worker/` is now the stable home for the Worker model, executor, roles, and presets, and the runtime node union plus builder/compiler/decompiler paths all understand canonical `worker` nodes.
- The current architecture boundary is explicit: Worker is the compute/contract primitive; pure scheduler semantics such as loop progression, fork/join, and turn-taking can stay specialized while still exposing Worker-friendly contracts and Workerized inner compute nodes.
- The current rollout state is also explicit in code: generation can prefer Worker-first simple compute stages via `DAN_WORKER_GENERATION`, and the public Python builder can opt a broader compute-like alias bucket (`llm` / `tool` / `code` / `input_node` / `reduce` / `rag` / `reflection` / `human` / `human_in_the_loop` / `vote` / `ensemble`) into canonical `worker` emission via `workflow(..., canonical_workers=True)` or `DAN_WORKER_BUILDER=canary|enabled`. Worker execution also resolves shared instruction, memory-policy, provider, retry, toolset, and authority refs from `graph.worker_resources` at runtime rather than forcing those heavier systems into the Worker model. The first-class Worker composition contract is now complete too, including external input/output schemas, control-state schema, local state, compaction/failure policy, projections, and boundary contract. Legacy compatibility lives behind `src/dan/models/legacy.py`, and `src/dan/worker/presets.py` names the bridged vs explicitly non-bridged runtime families.
- What is still not honest to claim today: that the Worker/universal-agent layer is already a reusable standalone bundle. The current runtime path still mixes concierge sessions, prompt overlays, DAN capability wiring, and Worker/legacy projections. Plans 46-6 and 46-7 exist to standardize those contracts first and then extract the reusable core instead of exporting DAN-specific coupling.
- Detailed implementation history now lives in `docs/changelog.md`. This plan tracks the current design boundary and the remaining migration/compaction tail, not every landed slice chronologically.
