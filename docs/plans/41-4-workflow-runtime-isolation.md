# 41-4: Workflow Runtime Isolation

**Parent:** [41-internal-runtime-submodule-restructure](41-internal-runtime-submodule-restructure.md)
**Status:** not-started
**Goal:** Make the workflow graph subsystem a clearer standalone internal runtime so execution, builder/compiler/loader logic, and workflow adapters can scale without inheriting chat/concierge coupling.

## Context

DAN already has a substantial workflow subsystem:

- graph models and taxonomy
- builder DSL
- loader/compiler/decompiler
- engine / scheduler / executors
- run manager and publish/runtime adapters

The workflow runtime is real, but it still needs a cleaner ownership boundary:

- model/provider setup is duplicated across runtime and surfaces
- some server/publish/run-manager concerns blur into workflow runtime behavior
- future orchestration work will be easier if workflow execution is clearly “another runtime” rather than an extension of chat logic

## Tasks

### 1. Define the workflow-runtime API surface
- [ ] 1-1. Identify the internal contracts that make up the workflow subsystem: graph model, build/load/compile, execute, checkpoint, resume, and event stream. **Note:** checkpoint/resume support currently spans engine, run-manager, and publish/runtime adapters rather than one cleanly isolated API. The contracts defined here should document that current layering honestly instead of assuming a cleaner boundary than the code actually has.
- [ ] 1-2. Document which pieces are canonical runtime APIs vs server-only adapters.
- [ ] 1-3. Make the runtime dependencies on model access explicit through `llm_core` or a runtime-safe adapter. Agent-style executors (`LLMExecutor` tool loops, `ReflectionExecutor`) may also need `agent_runtime` interfaces after 41-2 lands.

### 2. Reduce surface coupling
- [ ] 2-1. Audit `server`, `publish`, and CLI integration points that currently reach into workflow internals in ad hoc ways.
- [ ] 2-2. Pull adapter responsibilities toward `run_manager`, publish runtime, and composition-root modules rather than letting them leak into engine internals.
- [ ] 2-3. Keep graph execution testable without server startup.
- [ ] 2-4. Resolve pre-existing reverse-dependency violations where `engine/` and `executors/` import from `server/`:
  - `engine/skill_tracker.py` → `dan.server.skill_library`
  - `engine/behavior_store.py` → `dan.server.telemetry`
  - `engine/preference_extractor.py` → `dan.server.concierge.domain_learning`
  - `engine/plan_scheduler.py` → `dan.server.concierge.resources`
  - `engine/memory_kernel.py` → `dan.server.concierge.domain_learning`
  - `executors/llm.py` → `dan.server.concierge.pii_tokenizer`
  These imports mean engine/executors currently cannot be loaded without the server package, violating surface independence.

### 3. Clarify workflow-generation vs workflow-execution ownership
- [ ] 3-1. Keep generation/planning helpers separate from the core workflow execution engine.
- [ ] 3-2. Decide which “build from intent” helpers belong to agent/conversation layers and which belong to workflow-runtime adapters.
- [ ] 3-3. Keep the execution engine neutral about whether a graph came from builder DSL, markdown loader, chat mutation, or a saved file.
- [ ] 3-4. Decide the module ownership for `src/dan/meta/` (planning, intent compilation, generation). Currently `meta/` imports from `server.graph_mutator` (in `planner.py`, `controller.py`, `repair.py`) and `server.skill_library` (in `authoring.py`, `discovery.py`). These are reverse dependencies that need to be resolved — either by making `meta/` a peer of `workflow_runtime` with explicit interfaces, or by moving the `graph_mutator`/`skill_library` interfaces into a shared layer.

### 4. Tighten workflow-runtime tests
- [ ] 4-1. Add contract tests around graph execution, provider invocation, and resume/checkpoint behavior through the isolated runtime APIs.
- [ ] 4-2. Add regressions confirming runtime behavior does not depend on server-only initialization order.
- [ ] 4-3. Add adapter tests for run manager / publish / local execution surfaces over the isolated runtime.

## Primary Files

- `src/dan/models/` — graph schema, node types, edge types. **Note:** this is a shared schema layer consumed by multiple submodules (chat mutation, meta planner, graph mutator), not owned exclusively by workflow runtime. The boundary contract (41-6) should reflect this.
- `src/dan/builder/` — builder DSL, compiler (~18KB), decompiler (~53KB)
- `src/dan/loader/` — markdown loader, compiler (~64KB), decompiler (~38KB)
- `src/dan/engine/` — scheduler (~149KB), memory kernel (~58KB), token optimization (~60KB)
- `src/dan/executors/` — node executors including `control_flow.py` (~108KB), `llm.py` (~60KB), `reflection.py` (~24KB)
- `src/dan/server/run_manager.py` — run lifecycle and execution adapters (~72KB)
- `src/dan/publish/` — published workflow runtime, HTTP server, portal, sessions

## Decisions

- `workflow_runtime` remains an internal DAN subsystem, not a separately versioned package in this plan family.
- Workflow execution should be surface-agnostic even when workflow authoring and workflow-generation remain chat-aware.

## Notes

- This sub-plan is deliberately about boundary tightening, not about rewriting the engine. The goal is to make the runtime easier to compose and safer to extend.
- `src/dan/models/` is consumed by `graph_mutator.py` (chat-side), `meta/` (planning), and `loader/` (markdown) in addition to the engine. It should be treated as a shared schema layer in the dependency rules, not scoped exclusively under workflow runtime.
