# 46: Universal Worker Primitive

**Status:** not-started
**Goal:** Introduce a single universal `Worker` primitive that tops all existing node types, with a clean role system built on top, where most circumstances require zero configuration — it just works.

## Vision

When people ask "how do you define workers?", the answer is:

> We have one meta worker. It's a minimal contract orchestrator. We build roles on top of it — LLM agent, tool runner, script executor, manager, reviewer — but most of the time you don't configure anything. You give it a prompt and tools, it figures out the rest.

The Worker is not another agent framework. It's a **contract boundary**: input schema → execute → output schema. How it executes is just configuration. An LLM call is one possible execution mode. A script is another. No execution at all (pure pass-through) is another. The Worker doesn't care — it manages the contract.

## Zero-Config Philosophy

A bare `Worker` with just an `id` is a valid node. It passes inputs through to outputs. Add a `model` and it becomes an LLM agent. Add `code` and it becomes a script runner. Add `tool_ids` and it becomes a tool executor. Add `body_graph` and it becomes a composite orchestrator. Add `sub_workers` and it becomes a team manager.

You never have to specify `role="executor"` or `behavior="llm"`. The Worker auto-detects its execution mode from what's configured. Roles are optional labels for human readability and preset loading — not execution switches.

**Script-first, token-second.** If something can be done with a script, don't burn tokens. The executor dispatch explicitly prefers: code > direct tool invocation > LLM with tools > LLM only.

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
  model.py          # Worker, WorkerConfig, WorkerAuthority, LLMHints, CompositeContract
  executor.py       # WorkerExecutor (universal dispatch)
  roles.py          # Built-in role factories and preset registry
  presets.py        # Legacy type ↔ Worker conversion (for 46-3 equivalence)
```

Downstream code imports from `dan.worker`:
```python
from dan.worker import Worker, WorkerConfig, role
```

## Current State: Why This Is Needed

DAN has 21 node types, 20 executor registrations, 15+ builder methods. The composite-node contract (10 fields) is copy-pasted across 7 types. This is not compact enough to manage or scale.

The Worker collapses this: one model, one executor, one builder method. Old types become legacy aliases that deserialize into Workers. New code never touches them.

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [46-1](46-1-worker-model-and-type-system.md) | Worker Model & Type System | `Worker`, `WorkerConfig`, `WorkerAuthority` with clean field organization; `LLMHints` and `CompositeContract` sub-models; canonical contract metadata surface; add to `Node` union | P0 | not-started |
| [46-2](46-2-worker-executor-and-dispatch.md) | Worker Executor & Auto-Detection | Universal `WorkerExecutor` with auto-detected execution mode; script-first dispatch; reuses existing executor internals | P0 | not-started |
| [46-3](46-3-type-derivation-and-equivalence.md) | Roles, Presets & Equivalence Proof | Define built-in roles; build legacy-type ↔ Worker presets; prove every existing type is expressible as a Worker | P0 | not-started |
| [46-4](46-4-builder-dsl-and-authoring.md) | Builder DSL & Authoring Integration | `worker()` builder method; `worker_scope()` for composites; compiler/decompiler round-trip; preserve worker contract metadata through authoring surfaces | P1 | not-started |
| [46-5](46-5-migration-and-compaction.md) | Migration, Deprecation & Compaction | Conversion utilities; gradual migration; worker-first generation surfaces; codebase compaction; clean documentation | P2 | not-started |

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
```

Strictly serial. The model must exist before the executor, the executor before the equivalence proof, the proof before builder integration, and the proof before any migration.

## Implementation Lane

1. Execute plan 46 in a dedicated worktree separate from ongoing feature work.
2. Do not start plan 47 in parallel. The point of 46 is to normalize the node + contract surface first.
3. Plan 47 starts only after 46 has landed, or after a stable post-46 handoff branch/worktree exists with Worker-first authoring/generation surfaces and preserved contract metadata.

## Success Criteria

- A bare `Worker()` with no config is a valid node that passes data through
- Adding `model`, `code`, or `tool_ids` auto-selects the right execution mode — no explicit mode flag needed
- Every existing node type has a proven Worker equivalent (roles/presets from 46-3)
- All existing tests pass unchanged after adding `Worker` to the `Node` union
- At least one full workflow runs identically when expressed with Workers
- The `src/dan/worker/` module is self-contained and clean
- Built-in roles cover the common cases; custom roles are trivial to define
- Worker-native graphs preserve enough contract metadata (`description`, `role`, `persona`, port descriptions/schemas, boundary schemas) for downstream lint-config generation without per-type special-casing

## Key Design Principles

1. **It just works.** Minimal config produces useful behavior. Auto-detection over explicit mode flags. Smart defaults everywhere.
2. **Contract orchestrator, not LLM wrapper.** The Worker manages input/output contracts. Execution is a pluggable detail.
3. **Script-first, token-second.** Prefer deterministic code over LLM calls. Save tokens for what actually needs reasoning.
4. **Roles, not types.** Behavior differences come from configuration presets (roles), not different Python classes.
5. **One model, one executor, one builder method.** That's the compaction target. Everything else is a convenience alias or legacy compat.
6. **Backward compatible.** Old graphs deserialize and execute unchanged. Migration is gradual and opt-in.

## Decisions

- The Worker model has three clear field groups: **Identity** (role, persona, authority), **Capability** (model, tool_ids, code), **Composition** (body_graph, sub_workers, contract). LLM-specific tuning knobs live in an optional `LLMHints` sub-model, not flat on Worker.
- Control flow (condition, gate_mode) and validation (rules) stay on Worker as optional fields because they describe topology behavior, but are grouped and documented as "control flow" fields, not mixed into the identity or capability sections.
- The `src/dan/worker/` module is the canonical home. `models/worker.py` and `executors/worker.py` do not exist — everything lives under `worker/`.
- HumanNode and GateNode: both derivable as Worker roles. HumanNode = Worker with `tool_ids=["human_input"]`. GateNode = Worker with `condition` set and no model/code/tools. No special primitives needed.
- Plan 46 is the implementation prerequisite for plan 47. The linter module remains separate, but 46 is where the canonical contract/intent surface is normalized so 47 can consume it without special-casing the legacy node zoo.
- Worker `description`, `persona`, `role`, port descriptions, and boundary schemas are contract metadata, not UI decoration. Builder/compiler/decompiler/generation surfaces must preserve them faithfully.

## Notes

- This plan builds on the meta-worker concept: one cell type, differentiated by wiring and configuration — like a stem cell.
- The worktree for this work should be clean: `src/dan/worker/` is self-contained, tests are under `tests/test_worker/`, and the module has a clear public API. This is the dedicated prerequisite worktree for the follow-on 47 series.
- The `GENERATE_SPEC_NODE_TYPES` tuple currently targets `llm_operator`, `tool_operator`, `code_operator`, `gate`. Eventually it targets `worker` only.
- Directional communication (up/sideways/down) and dynamic topology (workers spawning workers at runtime) are future enrichments, not in scope here.
