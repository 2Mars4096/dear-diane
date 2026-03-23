# 41: Internal Runtime Submodule Restructure

**Status:** not-started
**Goal:** Restructure DAN's core runtime into four explicit internal submodules, `llm_core`, `agent_runtime`, `concierge_orchestrator`, and `workflow_runtime`, so behavior is easier to reason about, test, and scale without splitting the repository or publishing separate packages yet.

## Motivation

The repository already contains many of the right concepts, but several of the real control seams are still soft:

- chat and agent-loop behavior are still concentrated in `src/dan/server/chat_manager.py`
- concierge orchestration, direct LLM calls, and execution helpers are still mixed inside `src/dan/server/concierge/`
- workflow execution is a real subsystem, but surface adapters and startup paths still duplicate configuration and provider wiring
- local and server bootstrap paths are not fully aligned, so `--local` behavior can drift from the main server path

This makes the system harder to change safely. The immediate goal is not open-sourcing sub-packages; it is to create durable internal boundaries so DAN can keep shipping features without deepening the current coupling.

### Future scaling this enables

Separable modules empower concrete future scenarios without requiring them today:

- **Standalone CLI agent:** embed `llm_core` + `agent_runtime` in a lightweight CLI tool without importing the server or editor.
- **Headless workflow runner:** compose `workflow_runtime` + `llm_core` for batch/CI execution without chat, concierge, or editor dependencies.
- **Independent concierge scaling:** run orchestration in a separate process or service, delegating to agent runtimes over explicit interfaces.
- **Provider-layer replacement:** swap `llm_core` provider backends (e.g., local model server, custom API) without touching agent or workflow code.
- **Module-level testing:** test each module in isolation with mocked dependencies, enabling faster CI and more targeted regression suites.

## Non-Goals

- Do **not** split the repository into multiple distributions or public packages in this plan family.
- Do **not** redesign the editor or user-facing product surfaces as the primary goal.
- Do **not** change the `dan_graph_v1` contract unless a later implementation sub-plan identifies a strictly necessary compatibility-safe adjustment.
- Do **not** require a one-shot "big bang" move; compatibility shims are acceptable while the refactor lands.

## Target Architecture

The core implementation target remains **four internal runtime submodules**:
`llm_core`, `agent_runtime`, `concierge_orchestrator`, and `workflow_runtime`.

`meta/` and `models/` are called out below because they currently cut across those boundaries and will block the split if left implicit, but they are **adjacent/shared boundary layers**, not additional top-level product modules to optimize around.

### 1. `llm_core`

The single path for model invocation:

- provider registry construction
- model routing / provider overrides
- retries, timeouts, budgets, and telemetry
- PII wrapping / request protection
- shared request and response contracts for completion and streaming calls

### 2. `agent_runtime`

The reusable single-agent execution loop:

- prompt assembly
- history / memory / context injection
- tool loop execution
- plan / review / synthesis helpers that belong to one agent session
- build-query / direct-task style agent behavior behind explicit adapters

### 3. `concierge_orchestrator`

The message intake and delegation layer:

- triage
- decomposition
- route selection
- session management
- queueing / approvals / progress
- root-vs-child orchestration rules

### 4. `workflow_runtime`

The workflow graph subsystem:

- builder / compiler / loader / decompiler
- engine / scheduler / executor registry
- workflow execution contracts
- run-state, checkpoint, and workflow-level adapters

### Adjacent: `meta/` (planning & generation)

Workflow planning, intent compilation, code generation, and graph quality assessment:

- intent extraction and schema
- planner and codegen
- structural mutations and repair
- graph quality and build contract validation
- discovery and authoring helpers

Currently imports from `server.graph_mutator` and `server.skill_library` (known violations). Should depend only on `models/`, `llm_core`, and optionally `agent_runtime` — not on `server/` directly.

### Shared Foundation: `models/`

Graph schema, node types, and edge types are a shared foundation layer — not owned exclusively by any single submodule. Consumed by `workflow_runtime` (engine, executors), `agent_runtime` (mutation/build profiles), `concierge_orchestrator` (triage scenario matching), `meta/` (planning/generation), and surface adapters (server routers, editor).

### Cross-cutting: learning / memory

Memory kernel, behavior store, preference extraction, correction memory, and domain learning are currently scattered across `engine/` and `concierge/`. These create the deepest coupling cycles in the codebase (`concierge/runtime.py` has 20 `engine/` imports; `engine/` has 5 `server/` imports, mostly for learning-adjacent state). This plan family does not create a separate learning module, but each sub-plan must decide how the pieces it touches consume learning/memory interfaces — through explicit injection rather than hard cross-module imports.

## Dependency Direction

Desired direction after the refactor:

```text
models/                               ← shared schema layer (no runtime deps)

llm_core                              ← no upstream deps on agent/concierge/server/editor
  ↑                                      (wraps or absorbs current providers/)
agent_runtime                         ← depends on llm_core, models/
  ↑
concierge_orchestrator                ← depends on agent_runtime + llm_core
                                         CURRENT VIOLATION: 20 engine/ imports in runtime.py (memory/learning)
                                         CURRENT VIOLATION: engine/ imports concierge/ in 3 places (learning)
                                         → these cycles must be broken via shared interfaces or injection

workflow_runtime                      ← uses llm_core through explicit runtime interfaces
                                         may consume agent_runtime interfaces for agent-style executors
                                         CURRENT VIOLATION: 6 engine/executor → server/ imports

meta/ (planning/generation)           ← depends on models/, llm_core, agent_runtime
                                         CURRENT VIOLATION: 9 imports from server.graph_mutator/skill_library

server / cli / editor / adapters      ← composition roots; depend on the modules above
```

Rules:

- `llm_core` must not depend on `server`, `concierge`, or editor code.
- `agent_runtime` should not own transport-specific concerns.
- `concierge_orchestrator` should not contain raw provider access when `llm_core` exists.
- `workflow_runtime` should stay surface-agnostic and expose explicit interfaces to surfaces. It may consume `agent_runtime` interfaces for agent-style executors (tool loops, reflection), but must not depend on concierge or server modules.
- `models/` (graph schema, node types, edge types) is a shared layer consumed by multiple submodules — it is not owned exclusively by `workflow_runtime`.
- `meta/` (planning, generation, intent compilation) is treated as an adjacent boundary layer in this plan family. It should depend on `models/`, `llm_core`, and optionally `agent_runtime`, but must not import directly from `server/`. Currently it imports from `server.graph_mutator` and `server.skill_library` in 5+ places — these are known violations to resolve.
- server / CLI / local mode should become composition roots, not alternative runtime implementations.

## Plan Principles

- **Behavioral refactor first:** improve real ownership boundaries, not just folder names.
- **One call path for model usage:** direct `providers.resolve(...).complete(...)` shortcuts should disappear behind `llm_core`.
- **Thin surface adapters:** server/CLI/editor layers should translate requests and stream events, not own the core logic.
- **Parity is part of the architecture:** local mode and server mode must compose the same core runtime features.
- **Contracts before extraction:** define interfaces and tests before large file moves.

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [41-1](41-1-llm-core-and-model-gateway.md) | LLM Core & Model Gateway | Create one shared model-invocation layer for provider routing, retries, PII, budgets, and telemetry | P1 | not-started |
| [41-2](41-2-agent-runtime-extraction.md) | Agent Runtime Extraction | Pull the reusable single-agent loop out of `ChatManager` and define explicit runtime/session contracts | P1 | not-started |
| [41-3](41-3-concierge-orchestrator-narrowing.md) | Concierge Orchestrator Narrowing | Keep concierge focused on intake, routing, decomposition, session control, and progress rather than owning execution internals | P1 | not-started |
| [41-4](41-4-workflow-runtime-isolation.md) | Workflow Runtime Isolation | Strengthen the graph-engine boundary so workflow execution stays surface-agnostic and adapter-driven | P1 | not-started |
| [41-5](41-5-composition-root-and-surface-parity.md) | Composition Root & Surface Parity | Unify server/local/runtime wiring so the same capabilities and stores are composed consistently across surfaces | P1 | not-started |
| [41-6](41-6-module-contracts-and-regressions.md) | Module Contracts & Regressions | Add boundary contracts, import-direction guardrails, and parity regressions so the new structure stays intact | P1 | not-started |

## Dependencies / Sequencing

Recommended execution order:

```text
41-6 (Module Contracts & Regressions)     ← cross-cutting: starts early, tightens throughout every sub-plan
  ↕ (parallel companion to all below)

41-1 (LLM Core & Model Gateway)           ← establish the single call path first
  ├→ 41-5 (Composition Root & Surface Parity)   ← unify config/bootstrap around the same gateway
  ├→ 41-2 (Agent Runtime Extraction)            ← move single-agent logic once model access is centralized
  │    └→ 41-3 (Concierge Orchestrator Narrowing) ← concierge becomes orchestration-only after agent-runtime exists
  └→ 41-4 (Workflow Runtime Isolation)          ← can progress in parallel; consumes llm_core, optionally agent_runtime
```

Recommended first implementation slice:

1. Finish `41-1`
2. Land the bootstrap/parity fixes in `41-5`
3. Extract the agent loop in `41-2`
4. Narrow concierge in `41-3`
5. Clean up workflow runtime seams in `41-4`
6. Close remaining contract / regression gaps in `41-6`

## Success Criteria

- [ ] All LLM calls used by chat, concierge, meta-planning, and workflow execution route through one shared model gateway or a thin adapter over it.
- [ ] Provider override behavior, retry policy, timeout policy, telemetry, and PII protection behave consistently across chat, concierge, local mode, and workflow execution.
- [ ] `ChatManager` becomes a surface adapter over `agent_runtime` instead of the primary owner of the reusable agent loop.
- [ ] Concierge code owns intake, triage, session/routing/progress, and delegation policy, but no longer owns duplicated execution internals.
- [ ] Workflow execution remains independently testable and can be composed by server, local CLI, publish/runtime, and future surfaces without duplicate bootstrap logic.
- [ ] Local mode and server mode compose the same capability/tool/config/runtime graph, with explicit tests for parity.
- [ ] Module boundaries are documented and protected by tests so the structure does not regress into new god-modules.
- [ ] **Separability:** each module can be imported and unit-tested in isolation — `llm_core` without server/concierge, `agent_runtime` without server transport, `workflow_runtime` without chat/concierge, `meta/` without server-specific state. No circular import dependencies between the defined module boundaries.
- [ ] **No hidden dependency cycles:** the concierge↔engine and meta→server import cycles documented in this plan are resolved, either by extracting shared interfaces or by dependency injection at composition time.
- [ ] The named implementation target remains the four core runtime submodules; `meta/` and `models/` are explicitly documented as adjacent/shared layers rather than allowed to become a second overlapping control-plane.

## Decisions

- DAN remains one repository and one product while this refactor lands.
- These are **internal submodules**, not separately versioned OSS packages.
- `tiered` and `multi-layered` behavior remain policy/composition concerns, not separate top-level module families.
- The refactor should favor compatibility shims over risky large moves when needed, but only temporarily.

## Notes

- The immediate pressure points that justify this plan are the oversized coordination files (`chat_manager.py`, concierge runtime/executors, and duplicated startup paths), not a desire to make the tree look cleaner.
- The first durable seam to make real is the model invocation boundary; once that exists, the rest of the control-plane split becomes much easier.
- This plan family is intentionally scoped before any OSS packaging decision. If standalone packages ever happen later, this work should make the extraction straightforward rather than forcing API freezes now.
