# 41-3: Concierge Orchestrator Narrowing

**Parent:** [41-internal-runtime-submodule-restructure](41-internal-runtime-submodule-restructure.md)
**Status:** not-started
**Goal:** Narrow concierge to orchestration concerns such as intake, triage, routing, decomposition, approvals, queueing, and progress, while delegating execution details to `agent_runtime` and model invocation to `llm_core`.

## Context

The concierge package already contains many of the right concepts:

- project and task context
- triage
- queueing and dispatch
- tier/session handling
- progress UX
- decomposition and child-task routing

But some execution-specific behaviors still leak into concierge:

- direct model helper calls
- execution/synthesis helpers that should be agent-owned
- mixed ownership between root orchestration and child-task implementation details

That makes it harder to keep the root concierge intentionally narrow.

## Tasks

### 1. Define the orchestration boundary
- [ ] 1-1. Write down the exact responsibilities of concierge vs agent runtime vs workflow runtime.
- [ ] 1-2. Define root-session vs child-session ownership explicitly.
- [ ] 1-3. Make `tier` a budget/depth concept and `stage` a behavior/prompt concept, with clear ownership for each.

### 2. Remove direct execution leakage
- [ ] 2-1. Replace direct provider access with calls through `llm_core`.
- [ ] 2-2. Move agent-style synthesis/decomposition behavior behind `agent_runtime` helpers where appropriate.
- [ ] 2-3. Keep concierge focused on deciding *what should happen next*, not owning every step of *how it is executed*.
- [ ] 2-4. Move `pii_tokenizer.py` (~19KB) out of concierge — PII tokenization is an `llm_core` gateway concern. **Cross-ref:** 41-1 task 3-1 creates the gateway-side landing zone; this task handles the concierge-side removal and re-wiring. `executors/llm.py` also imports `pii_tokenizer` directly — that import should route through `llm_core` after the move.
- [ ] 2-5. Decide ownership for `domain_learning.py` (~40KB) — domain knowledge accumulation is closer to memory/agent-profile behavior than orchestration. Should it move to `agent_runtime` or remain concierge-owned with an explicit rationale?
- [ ] 2-6. Decide ownership for `computer_use.py` (~26KB) and `computer_policy.py` (~11KB) — these are execution-specific concerns (browser automation) that leak into the orchestration layer.
- [ ] 2-7. Break the concierge→engine dependency cycle. `concierge/runtime.py` currently has 20 imports from `dan.engine` (memory kernel, behavior store, adaptation registry, correction memory, outcome trackers, learning tiers, domain taxonomy, preference extractor, memory extractor, user profile). These are mostly memory/learning operations that concierge consumes but should not own. Options: (a) define a shared memory/learning interface that both engine and concierge consume via injection, (b) move the consumed interfaces into a lightweight shared module, or (c) have the composition root inject pre-built memory/learning services into concierge at startup. This cycle is the single largest obstacle to module separability.

### 3. Make routing contracts explicit
- [ ] 3-1. Define route contracts for direct answer, direct task, workflow build, file review, run-control, and delegated child tasks.
- [ ] 3-2. Clarify when concierge should stay conversational vs spawn/route a specialized execution path.
- [ ] 3-3. Make approval and clarification steps explicit in the routing contract rather than relying on scattered conditionals.

### 4. Keep queueing / progress / session state inside concierge
- [ ] 4-1. Preserve `ConcurrentDispatcher`, progress reporting, and per-project serialization as concierge-owned concerns.
- [ ] 4-2. Make session state and route provenance visible enough that orchestration decisions can be debugged without reading giant logs.
- [ ] 4-3. Ensure local and server mode consume the same concierge behavior, not near-copies.

### 5. Strengthen tests around orchestration
- [ ] 5-1. Add regressions for route selection, child-task decomposition, approval gating, and stage/tier provenance.
- [ ] 5-2. Add regressions confirming concierge no longer owns raw provider-call shortcuts.
- [ ] 5-3. Add focused tests for root-vs-child behavior so the orchestrator role stays narrow as features grow.

## Primary Files

- `src/dan/server/concierge/runtime.py` — root orchestration, direct provider calls (~3200 lines, ~136KB)
- `src/dan/server/concierge/tier_executors.py` — tiered execution with direct LLM helpers (~2050 lines, ~83KB)
- `src/dan/server/concierge/tiered_dispatch.py` — dispatch and routing (~30KB)
- `src/dan/server/concierge/dispatcher.py` — concurrent dispatcher (~20KB)
- `src/dan/server/concierge/triage.py` — triage logic (~42KB)
- `src/dan/server/concierge/session.py` — session state (~13KB)
- `src/dan/server/concierge/scheduler.py` — queueing and dispatch scheduling (~50KB); orchestration-owned per plan scope
- `src/dan/server/concierge/pii_tokenizer.py` — PII tokenization (~19KB); **migration target → `llm_core`**
- `src/dan/server/concierge/domain_learning.py` — domain knowledge accumulation (~40KB); **ownership decision needed**
- `src/dan/server/concierge/computer_use.py` — browser automation execution (~26KB); **migration target → `agent_runtime` or dedicated executor**
- `src/dan/server/concierge/computer_policy.py` — computer-use policy (~11KB); **follows `computer_use.py`**

## Decisions

- Concierge remains a first-class internal module; it is not replaced by workflow graphs.
- Root concierge sessions should bias toward orchestration and supervision, not detailed worker behavior.
- Queueing, progress, and cross-turn orchestration remain concierge-owned even after execution details move elsewhere.

## Notes

- This sub-plan should become easier once `41-1` and `41-2` exist. Trying to narrow concierge before those seams are real would just move code around without reducing coupling.
- `triage.py` currently imports from `dan.engine.behavior_store`. This means concierge depends on engine internals, creating a potential bidirectional coupling concern when 41-4 tightens the engine boundary. The interface `triage.py` needs should be formalized as a shared contract or injected at composition time.
