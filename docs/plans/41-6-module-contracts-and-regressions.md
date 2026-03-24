# 41-6: Module Contracts & Regressions

**Parent:** [41-internal-runtime-submodule-restructure](41-internal-runtime-submodule-restructure.md)
**Status:** completed
**Goal:** Add explicit module-boundary contracts, dependency-direction checks, and parity regressions so the new internal submodule structure stays intact after the refactor lands.

## Context

The risk in a large architectural cleanup is not only landing it; it is keeping the boundaries from collapsing again. DAN already has a history of drift between runtime, chat, editor, and bootstrap layers when interfaces are only implied by package names.

This sub-plan turns the new structure into a maintained contract instead of a temporary reorganization.

**This is a cross-cutting concern, not a terminal step.** It should start early (alongside 41-1) and tighten progressively as each sub-plan lands, rather than being deferred to the end.

## Tasks

### 1. Define the boundary contract
- [x] 1-1. Document allowed dependency directions between `llm_core`, `agent_runtime`, `concierge_orchestrator`, `workflow_runtime`, and surface adapters. **Done:** Added "Module Dependency Rules" subsection to `docs/architecture.md` with full dependency direction table and enforcement notes.
- [x] 1-2. Identify temporary compatibility shims and mark which ones must be removed before the plan family is considered complete. **Done:** Added "Tracked Compatibility Shims" table (6 shims) to this plan file with location, purpose, and removal condition for each.
- [x] 1-3. Update architecture docs once the concrete module layout exists. **Done:** `llm_core/` already listed in directory layout (line 382) with purpose and file-level detail. Confirmed present.

### 2. Add regression coverage for the new seams
- [x] 2-1. Add contract tests for the shared model gateway behavior and provider registry composition. **Done:** 14 tests in `tests/test_llm_core/test_gateway_regressions.py` (landed with 41-1 tasks 5-1 through 5-4).
- [x] 2-2. Add parity tests for `ChatManager` adapter behavior over the extracted agent runtime. **Done:** `tests/test_parity/test_chat_manager_agent_runtime_parity.py` verifies `ChatManager.run_agent_turn()` / `stream_agent_turn()` match `BaseAgentRuntime` with the same gateway and prefer an injected runtime when provided.
- [x] 2-3. Add orchestration tests confirming concierge no longer owns raw execution shortcuts. **Done:** `tests/test_concierge/test_llm_gateway_contract_runtime.py` covers concierge's gateway/helper behavior, and `tests/test_concierge/test_import_boundaries.py` now forbids direct `resolve_model_gateway` / `resolve_llm_provider` imports from the concierge runtime helpers so raw shortcut ownership cannot drift back in.
- [x] 2-4. Add local-vs-server parity tests for capability composition and bootstrap behavior. **Done:** 5 tests in `tests/test_parity/test_capability_parity.py` — import checks, registration function availability, ≥80% capability name overlap, local⊆server subset assertion.
- [x] 2-5. Add workflow-runtime adapter tests proving the engine can still be composed from multiple surfaces. **Done:** `tests/test_parity/test_workflow_runtime_adapter_parity.py` verifies default engine composition matches a run-manager-style prewired registry, preserves injected `tool_operator` executors, and confirms `publish.runtime.LocalRuntime` is constructible without server startup.

### 3. Add structural guardrails
- [x] 3-1. Add import-direction boundary tests for forbidden dependency directions. **Mechanism:** `tests/test_module_boundaries.py` — AST-walking test that walks all `import`/`from…import` statements (including lazy/deferred) in 7 submodules and asserts forbidden reverse dependencies. The import-direction and circular-pair allowlists are now both empty; the test fails on any *new* violation or boundary cycle.
- [x] 3-2. Add file-size watchpoints for current hotspots so extraction regressions are caught. **Mechanism:** `test_god_module_watchpoints` and `test_god_module_total_lines` in `tests/test_module_boundaries.py` — counts lines in 7 god-module files and asserts each stays at/below a baseline (current count rounded up to nearest 100 + 100-line buffer). Also asserts total across all files stays bounded (+200). Failure messages name the 41-* extraction plan responsible for shrinking the offending file.
- [x] 3-3. Make new internal modules discoverable in docs so future contributors do not route new work back into legacy facades by default. **Done:** Added "Module Ownership Guide" table to `docs/architecture.md` mapping 6 common development tasks to target modules (with "Not in…" column to prevent legacy drift).

### 4. Plan the compatibility clean-up
- [x] 4-1. List old compatibility entry points that can remain temporarily (`ChatManager`, old startup helpers, etc.). **Done:** "Tracked Compatibility Shims" section above lists 6 shims with locations and removal conditions.
- [x] 4-2. Decide the order for removing or thinning those shims after the main move is complete. **Done:** "Compatibility Cleanup Order" section above defines the 5-step removal sequence gated by plan dependencies.
- [x] 4-3. Ensure test names and architecture docs continue to point at the new ownership model instead of the legacy facades. **Done:** Architecture now points at package-backed module paths (`startup/__init__.py`, `chat_factory/__init__.py`, `llm_core/gateway/__init__.py`), and the recovered gateway contract coverage lives in stable pytest-discoverable filenames (`test_gateway_runtime.py`, `test_llm_gateway_contract_runtime.py`).

## Tracked Compatibility Shims

Legacy entry points kept temporarily alongside the new module structure. Each must be removed before the plan family is considered complete.

| Shim | Location | Purpose | Remove when |
|------|----------|---------|-------------|
| `resolve_completion_provider` | `executors/provider_runtime.py` | Legacy provider path kept alongside `resolve_gateway()` | All execution contexts have `model_gateway` |
| `_resolve_provider` | `executors/llm.py` | Legacy PII/registry logic below gateway check | Gateway is always available |
| `ChatManager` | `server/chat_manager.py` | Remains as surface adapter | Full agent-runtime extraction in 41-2 |
| `resolve_llm_provider` | `server/llm_gateway/__init__.py` | Thin wrapper delegating to ChatManager | Replace with direct `ModelGateway` usage after 41-5 |
| `_build_chat_provider_registry` | `server/startup/__init__.py` | Duplicate registry builder | Replace with `build_gateway()` in 41-5 |
| Provider setup | `server/chat_factory/__init__.py` | Duplicate provider/registry wiring | Replace with `build_gateway()` in 41-5 |

## Compatibility Cleanup Order

Shims should be removed in this sequence, gated by the corresponding plan landing:

1. Wire `build_gateway()` into startup (41-5) — then remove `_build_chat_provider_registry`, `chat_factory` provider setup
2. Extract agent runtime (41-2) — then thin `ChatManager` to adapter
3. Narrow concierge (41-3) — then remove direct provider calls
4. Remove `resolve_llm_provider` from `server/llm_gateway/__init__.py`
5. Remove legacy path from `executors/provider_runtime.py:resolve_completion_provider`

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
- 2026-03-23 follow-up: removed the remaining `providers/` → `engine/` type-only imports by switching `factory.py`, `costs.py`, and `tier_defaults.py` to local structural protocols. `meta/discovery.py` also stopped importing `server.graph_mutator` for pattern enumeration, and both `meta/discovery.py` and `meta/authoring.py` now use injected skill-library mappings instead of importing `dan.server.skill_library`.
- 2026-03-23 follow-up: `meta/planner.py`, `meta/controller.py`, and `meta/repair.py` now import graph-mutation primitives through a neutral `dan.graph_mutator` facade instead of reaching into `dan.server.graph_mutator`. Boundary allowlist is now 0 violations; circular allowlist is now 3 pairs. Added gateway contract coverage for the domain-reflection helper and updated path-sensitive parity/watchpoint tests to the recovered package-backed module layout.
- 2026-03-23 follow-up: extracted `RepairLevel` and `RepairClassifier` into `src/dan/repair_classification.py`, updated `meta/repair.py` to re-export that shared seam, and switched `engine/runtime_repair.py` off the `dan.meta.repair` import. Circular allowlist is now 2 pairs (`engine`↔`executors`, `server`↔`server.concierge`).
- 2026-03-23 follow-up: moved default executor registration into `src/dan/executor_defaults.py`, moved feedback-selector filtering into `src/dan/engine/conditions.py`, and updated `engine/scheduler.py` plus `executors/control_flow.py` to consume those neutral seams. Added `tests/test_executor_defaults.py`. Circular allowlist is now 1 pair (`server`↔`server.concierge`).
- 2026-03-23 follow-up: added `tests/test_parity/test_chat_manager_agent_runtime_parity.py` and the additive `ChatManager.run_agent_turn()` / `stream_agent_turn()` adapter seams, completing 2-2. `tests/test_agent_runtime/test_runtime.py` and the parity tests together show the extracted runtime can be exercised without booting the server stack.
- 2026-03-23 follow-up: extracted neutral shared seams (`src/dan/chat_events.py`, `src/dan/llm_surface.py`, `src/dan/chat_prompts.py`, `src/dan/telemetry_api.py`, `src/dan/web_surface.py`), kept the old `server/*` modules as compatibility re-exports where needed, and switched concierge imports to the neutral paths. `KNOWN_CIRCULAR_PAIRS` is now empty: the last `server`↔`server.concierge` pair is resolved.
- 2026-03-23 follow-up: added `tests/test_parity/test_workflow_runtime_adapter_parity.py`, completing 2-5. The workflow runtime is now covered at the composition seam across default engine startup, run-manager-style prewiring, and publish local runtime construction without booting the full server stack.
- 2026-03-23 follow-up: completed 2-3 by pairing the behavioral gateway-contract coverage with a new structural concierge boundary test. Concierge runtime helpers now consume the shared `complete_chat_surface()` seam without directly importing `resolve_model_gateway` or `resolve_llm_provider`.
- 2026-03-23 follow-up: tightened the structural concierge guardrails again by asserting `Concierge.__init__` no longer directly imports the engine learning stack. Behavior/adaptation bootstrap now lives in `server/concierge/learning_bundle.py`, and the AST test in `tests/test_concierge/test_import_boundaries.py` protects that split.
- 2026-03-23 follow-up: extended the concierge runtime boundary guardrail to cover learning-tier, correction-analysis, and post-turn enrichment hooks. `runtime/__init__.py` no longer imports `dan.engine.learning_tiers`, `dan.engine.correction_memory`, `dan.engine.preference_extractor`, `dan.engine.memory_extractor`, `dan.engine.domain_taxonomy`, or `dan.engine.user_profile`; those engine-backed concerns now enter through `learning_bundle.py`, `correction_feedback.py`, and `memory_enrichment.py`, and `tests/test_concierge/test_import_boundaries.py` locks that boundary.
- 2026-03-23 follow-up: completed the next concierge boundary contraction by moving the remaining `memory_kernel` calls behind `server/concierge/memory_services.py`. `tests/test_concierge/test_import_boundaries.py` now asserts `runtime/__init__.py` has zero direct `dan.engine` imports at all, while `tests/test_concierge/test_resources.py` verifies `build_concierge()` injects the memory-services seam from composition.
- 2026-03-23 follow-up: extracted `domain_taxonomy` to `src/dan/domain_taxonomy.py` as a neutral shared seam and reduced the old `engine/domain_taxonomy.py` path to a compatibility re-export. Added `tests/test_domain_taxonomy.py` to lock both the neutral API behavior and the legacy import path.
- 2026-03-23 follow-up: extended the same contract-tightening pattern to `domain_preferences.py`. The command module now imports only neutral/local helpers, while `profile_domain_sync.py` owns the engine-backed persistence details. `tests/test_concierge/test_import_boundaries.py` now also asserts `domain_preferences.py` has no direct `dan.engine` imports.
- 2026-03-23 follow-up: removed another private engine dependency from `domain_learning.py` by promoting `_keyword_overlap` into the neutral helper `src/dan/keyword_overlap.py`. `tests/test_keyword_overlap.py` locks the shared helper behavior, and `tests/test_concierge/test_import_boundaries.py` now asserts `domain_learning.py` no longer imports `memory_kernel._keyword_overlap`.
- 2026-03-23 follow-up: continued shrinking `domain_learning.py` by moving learning-tier gating and adaptation-registry ownership behind explicit helpers. `feature_gates.py` now owns the engine tier lookup, `domain_learning_adaptations.py` owns `AdaptationCandidate` usage, and the updated boundary tests assert `domain_learning.py` no longer imports either `dan.engine.learning_tiers` or `dan.engine.adaptation_registry`.
