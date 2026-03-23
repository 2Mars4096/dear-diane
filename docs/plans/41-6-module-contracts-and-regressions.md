# 41-6: Module Contracts & Regressions

**Parent:** [41-internal-runtime-submodule-restructure](41-internal-runtime-submodule-restructure.md)
**Status:** not-started
**Goal:** Add explicit module-boundary contracts, dependency-direction checks, and parity regressions so the new internal submodule structure stays intact after the refactor lands.

## Context

The risk in a large architectural cleanup is not only landing it; it is keeping the boundaries from collapsing again. DAN already has a history of drift between runtime, chat, editor, and bootstrap layers when interfaces are only implied by package names.

This sub-plan turns the new structure into a maintained contract instead of a temporary reorganization.

**This is a cross-cutting concern, not a terminal step.** It should start early (alongside 41-1) and tighten progressively as each sub-plan lands, rather than being deferred to the end.

## Tasks

### 1. Define the boundary contract
- [ ] 1-1. Document allowed dependency directions between `llm_core`, `agent_runtime`, `concierge_orchestrator`, `workflow_runtime`, and surface adapters.
- [ ] 1-2. Identify temporary compatibility shims and mark which ones must be removed before the plan family is considered complete.
- [ ] 1-3. Update architecture docs once the concrete module layout exists.

### 2. Add regression coverage for the new seams
- [ ] 2-1. Add contract tests for the shared model gateway behavior and provider registry composition.
- [ ] 2-2. Add parity tests for `ChatManager` adapter behavior over the extracted agent runtime.
- [ ] 2-3. Add orchestration tests confirming concierge no longer owns raw execution shortcuts.
- [ ] 2-4. Add local-vs-server parity tests for capability composition and bootstrap behavior.
- [ ] 2-5. Add workflow-runtime adapter tests proving the engine can still be composed from multiple surfaces.

### 3. Add structural guardrails
- [ ] 3-1. Add import-direction boundary tests for forbidden dependency directions. **Mechanism:** follow the existing AST-walking pattern (see `tests/test_security/test_exec_builtins_audit.py`) — write a pytest test that walks `import` statements in each submodule and asserts forbidden reverse dependencies. **Priority forbidden directions:** `llm_core` must not import from `server`/`concierge`/`agent_runtime`; `agent_runtime` must not import transport-specific `server` modules; `workflow_runtime` must not import `concierge`.
- [ ] 3-2. Add file-size watchpoints for current hotspots so extraction regressions are caught: `chat_manager.py` (5725 lines), `concierge/runtime.py` (3225 lines), `engine/scheduler.py` (~3500+ lines), `executors/control_flow.py` (~2500+ lines). Assert these shrink or at least do not grow after the extraction sub-plans land.
- [ ] 3-3. Make new internal modules discoverable in docs so future contributors do not route new work back into legacy facades by default.

### 4. Plan the compatibility clean-up
- [ ] 4-1. List old compatibility entry points that can remain temporarily (`ChatManager`, old startup helpers, etc.).
- [ ] 4-2. Decide the order for removing or thinning those shims after the main move is complete.
- [ ] 4-3. Ensure test names and architecture docs continue to point at the new ownership model instead of the legacy facades.

## Primary Files

- `docs/architecture.md`
- `tests/`
- whichever new module roots are introduced during implementation

## Decisions

- Boundary enforcement should be pragmatic: start with high-value tests around real drift points instead of trying to build a perfect architecture linter immediately.
- Compatibility shims are acceptable only if they are tracked and intentionally temporary.

## Notes

- This sub-plan should start early and continue throughout the implementation family. It is not only a final cleanup phase.
- The AST-walking import-direction test should be one of the first deliverables (even before the submodules are created) so it can catch violations as code moves.
