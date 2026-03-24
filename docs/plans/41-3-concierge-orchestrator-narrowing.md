# 41-3: Concierge Orchestrator Narrowing

**Parent:** [41-internal-runtime-submodule-restructure](41-internal-runtime-submodule-restructure.md)
**Status:** completed
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

## Boundary Definition

### 1-1. Responsibility Matrix

| Concern | Owner | Not |
|---------|-------|----|
| Message triage (intent classification, route selection) | concierge | agent_runtime |
| Task decomposition (breaking tier-2 work into child tasks) | concierge | agent_runtime |
| Route dispatch (which agent mode, which execution path) | concierge | agent_runtime |
| Session management (root vs child, history, depth limits) | concierge | agent_runtime |
| Approval gates, clarification requests | concierge | agent_runtime |
| Progress reporting, queueing, per-project serialization | concierge | agent_runtime |
| Tier/budget management (SessionTier 0/1/2 → model budget) | concierge | llm_core |
| Single-agent completion loop (chat + tools until done) | agent_runtime | concierge |
| Tool dispatch and execution | agent_runtime | concierge |
| Prompt assembly and context injection | agent_runtime | concierge |
| Direct LLM calls (complete/stream) | llm_core (via gateway) | concierge |
| PII tokenization/detokenization | llm_core (gateway concern) | concierge |
| Domain learning / memory accumulation | shared interface (injected) | concierge-owned |
| Computer-use execution (browser automation) | agent_runtime / executor | concierge |
| Workflow graph execution (run engine) | workflow_runtime | concierge |

**Key insight:** Concierge decides *what* happens next. It never owns *how* a task is executed. The boundary is: concierge selects the route, constructs the session, dispatches — then hands off. Execution results flow back through session results.

### 1-2. Root-Session vs Child-Session Ownership

The session model (`session.py`) already encodes a clean tree structure:

- **Root sessions** are created by `SessionManager.create_root()` when a new user message arrives. They carry the `SurfaceMessage`, triage result, autonomy resolution, and tier. `root_id == id`.
- **Child sessions** are created by `SessionManager.create_child(parent_id, task, tier)`. They inherit `root_id` from the parent, increment `depth`, and are subject to budget guards (`max_depth=4`, `max_children=8`, `max_total_sessions=32`).
- **Ownership rule:** The *root session* is concierge-owned — it represents the orchestration lifecycle from message intake to final response. *Child sessions* are dispatched units of work — concierge creates them, tracks their state, aggregates their `SessionResult.child_results`, but the *execution* inside a child session belongs to the tier executor / agent_runtime.
- **Depth constraints:** `can_spawn_child()` enforces depth and fan-out limits. SINGLE-tier (tier 1) sessions cannot have children. Only MULTI-tier (tier 2) sessions decompose.
- **State machine:** Sessions follow `pending → running → {waiting, completed, failed, cancelled}` with explicit transition validation.

### 1-3. Tier vs Stage Semantics

**Tier** is a budget/depth concept. **Stage** is a behavior/prompt concept. They are related but not identical.

**Tier** (defined in `session.py::SessionTier`):
- `INSTANT (0)` — greetings, confirmations, zero-cost social turns. No tools, no child sessions.
- `SINGLE (1)` — questions with clear answers, status checks, lookups. Single-turn execution, cannot spawn children.
- `MULTI (2)` — research tasks, reports, code projects, workflows. May decompose into child sessions.
- Ownership: Triage assigns the tier. Concierge uses it for budget and dispatch decisions. The tier resolver maps tiers to model budgets.

**Stage** (computed by `tier_executors.py::_determine_stage()`):
- `conversation` — default conversational mode
- `conversation_plan` — planning intent or plan mode
- `conversation_debug` — debug mode
- `workflow_build` — workflow creation/editing (build mode or workflow action hints)
- `file_review` — file-oriented route
- `experience_fallback` — experience lookup from memory
- `direct_task` — agent-intent on run/memory/web routes
- Ownership: Derived from triage route + mode + action hints. Used to select prompt overlays (`_stage_prompt_overlay()`) and model overrides (`_resolve_session_model_override()` → `tier_resolver.resolve_model(stage)`).

**Relationship:** Tier determines *resource budget* (which model class, can decompose?). Stage determines *behavior* (which system prompt overlay, which execution style). A tier-2 session might have stage `direct_task` or `workflow_build` — same budget, different behavior.

## Tasks

### 1. Define the orchestration boundary
- [x] 1-1. Write down the exact responsibilities of concierge vs agent runtime vs workflow runtime. *(See Boundary Definition § 1-1 above.)*
- [x] 1-2. Define root-session vs child-session ownership explicitly. *(See § 1-2 above.)*
- [x] 1-3. Make `tier` a budget/depth concept and `stage` a behavior/prompt concept, with clear ownership for each. *(See § 1-3 above.)*

### 2. Remove direct execution leakage
- [x] 2-1. Replace direct provider access with calls through `llm_core`. **Done:** concierge runtime and tier executors now use `dan.llm_surface.complete_chat_surface()` for their lightweight completion helpers, the gateway contract regressions cover both gateway-first and fallback behavior, and import-boundary tests now forbid runtime/tier-executor imports from the raw provider layer.
- [x] 2-2. Move agent-style synthesis/decomposition behavior behind `agent_runtime` helpers where appropriate. **Done:** the pure decomposition/synthesis review helpers now live in `dan.agent_runtime.synthesis`, the LLM-call shell now lives in `dan.agent_runtime.synthesis_runtime`, and `dan.agent_runtime.orchestration` now owns the pure multi-step execution heuristics, child-session envelope planning, and the serial/mixed/parallel child-execution policy helper. Concierge tier executors now consume those runtime helpers directly, and direct runtime coverage lives in `tests/test_agent_runtime/test_synthesis.py`, `tests/test_agent_runtime/test_synthesis_runtime.py`, and `tests/test_agent_runtime/test_orchestration.py`.
- [x] 2-3. Keep concierge focused on deciding *what should happen next*, not owning every step of *how it is executed*. **Done:** concierge now delegates decomposition/synthesis review helpers, the LLM-call shell, child-session envelope planning, and the serial/mixed/parallel child-execution policy to `agent_runtime`, while keeping intake, triage, route/session control, child creation, queue/progress ownership, dispatcher callbacks, remediation spawning, and final result setting local.
- [x] 2-4. Move `pii_tokenizer.py` (~19KB) out of concierge — PII tokenization is an `llm_core` gateway concern. **Done:** canonical implementation lives in `dan.llm_core.pii_tokenizer`; concierge runtime now initializes `SensitiveWordRegistry` from that module, `command_registry.py` points `/pii` handling at `dan.llm_core.pii_tokenizer.handle_pii_command`, and `server/concierge/pii_tokenizer.py` remains only as a compatibility facade.
- [x] 2-5. Decide ownership for `domain_learning.py` (~40KB) — domain knowledge accumulation is closer to memory/agent-profile behavior than orchestration. Should it move to `agent_runtime` or remain concierge-owned with an explicit rationale? **Decision:** keep it concierge-adjacent for now as an explicit memory/knowledge helper cluster, but continue peeling pure adaptations and feature gates into helper modules (`domain_learning_adaptations.py`, `feature_gates.py`) rather than growing the core orchestrator.
- [x] 2-6. Decide ownership for `computer_use.py` (~26KB) and `computer_policy.py` (~11KB) — these are execution-specific concerns (browser automation) that leak into the orchestration layer. **Decision:** treat them as execution/runtime modules, not concierge-core orchestration logic. They remain colocated under `server/concierge/` for now, but as a dedicated controller/policy cluster rather than precedent for widening the orchestrator.
- [x] 2-7. Break the concierge→engine dependency cycle. `concierge/runtime.py` currently has 20 imports from `dan.engine` (memory kernel, behavior store, adaptation registry, correction memory, outcome trackers, learning tiers, domain taxonomy, preference extractor, memory extractor, user profile). These are mostly memory/learning operations that concierge consumes but should not own. Options: (a) define a shared memory/learning interface that both engine and concierge consume via injection, (b) move the consumed interfaces into a lightweight shared module, or (c) have the composition root inject pre-built memory/learning services into concierge at startup. This cycle is the single largest obstacle to module separability. **Done:** runtime now consumes injected learning/memory seams (`learning_bundle.py`, `memory_services.py`, `memory_enrichment.py`) and the boundary tests lock in that concierge runtime no longer imports engine internals directly.

### 3. Make routing contracts explicit
- [x] 3-1. Define route contracts for direct answer, direct task, workflow build, file review, run-control, and delegated child tasks. **Done:** `RouteDecision`, triage routing output, and stage resolution now define the contract shape explicitly across `models.py`, `triage.py`, and `tiered_dispatch.py`.
- [x] 3-2. Clarify when concierge should stay conversational vs spawn/route a specialized execution path. **Done:** stage resolution and tiered dispatch now make the conversational-vs-specialized branch explicit instead of leaving it implicit in one-off conditionals.
- [x] 3-3. Make approval and clarification steps explicit in the routing contract rather than relying on scattered conditionals. **Done:** `pending_actions.py` now defines an explicit `PendingReplyResolution` contract for cancel/resume/retry outcomes, and both concierge runtime replay plus tier-executor pending handling now consume that shared contract instead of open-coding confirm/clarify branches.

### 4. Keep queueing / progress / session state inside concierge
- [x] 4-1. Preserve `ConcurrentDispatcher`, progress reporting, and per-project serialization as concierge-owned concerns. **Done:** dispatcher/progress/session ownership remains inside concierge, with queueing/progress behavior exercised through the existing dispatcher and progress UX regressions.
- [x] 4-2. Make session state and route provenance visible enough that orchestration decisions can be debugged without reading giant logs. **Done:** `SessionTrace`, `export_tree()`, telemetry payloads, and persisted assistant-turn `session_tree` metadata now carry route target, action hints, stage, scenario, and autonomy provenance.
- [x] 4-3. Ensure local and server mode consume the same concierge behavior, not near-copies. **Done:** local mode now goes through the shared `build_chat_services()` / `build_concierge()` composition path rather than a bespoke concierge bootstrap.

### 5. Strengthen tests around orchestration
- [x] 5-1. Add regressions for route selection, child-task decomposition, approval gating, and stage/tier provenance. **Done:** `test_triage.py`, `test_tiered_dispatch.py`, `test_progress_ux.py`, `test_computer_policy.py`, and `test_tiering.py` now cover those orchestration contracts directly.
- [x] 5-2. Add regressions confirming concierge no longer owns raw provider-call shortcuts. **Done:** `tests/test_concierge/test_llm_gateway_contract_runtime.py` covers the gateway/helper behavior, and `tests/test_concierge/test_import_boundaries.py` now forbids direct `resolve_model_gateway` / `resolve_llm_provider` imports from the concierge runtime helpers.
- [x] 5-3. Add focused tests for root-vs-child behavior so the orchestrator role stays narrow as features grow. **Done:** `tests/test_concierge/test_session_manager.py` and the tiered-dispatch child-session regressions now cover root-vs-child state propagation, child routing, and session-tree export behavior.

## Primary Files

- `src/dan/server/concierge/runtime/__init__.py` — root orchestration, direct provider calls (~3200 lines, ~136KB)
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

### 2-4. PII tokenizer → `llm_core/pii_tokenizer.py`

**Decision:** Move `pii_tokenizer.py` to `llm_core/pii_tokenizer.py`.

PII tokenization is a gateway cross-cutting concern — it wraps every LLM call, not just concierge's. The `ModelGateway` already supports duck-typed PII sessions (`pii_session` kwarg on `complete()`/`stream()`), so the landing zone exists.

Migration steps:
1. `ModelGateway` already supports duck-typed PII sessions (41-1 task 3-1).
2. Move `concierge/pii_tokenizer.py` → `llm_core/pii_tokenizer.py` (physical move, later task).
3. Update `concierge/` and `executors/llm.py` imports to `from dan.llm_core.pii_tokenizer import ...`.
4. Gateway wires PII session creation internally — callers no longer manage tokenizer lifecycle.

### 2-5. `domain_learning.py` — shared protocol, stays in place for now

**Decision:** Domain learning stays physically in `concierge/` but must consume a protocol, not be directly imported by engine.

`domain_learning.py` (~40KB, 1110 lines) provides:
- Keyword-based domain detection (`detect_domains()`)
- Domain-specific knowledge extraction templates
- LLM-powered post-task reflection
- Rule-based content validation against accumulated domain knowledge

It imports heavily from `dan.engine` (5 direct imports: `domain_taxonomy`, `learning_tiers`, `memory_kernel`, plus TYPE_CHECKING imports for `BehaviorStore`, `MemoryKernel`). These are memory/learning interfaces, not execution.

Resolution:
1. Define a `MemoryServices` protocol (see § Dependency Cycle Resolution) that bundles the memory/learning operations domain_learning needs.
2. `domain_learning.py` accepts `MemoryServices` via injection instead of importing engine internals.
3. Physical location stays in `concierge/` for now — it's domain *knowledge* used during orchestration decisions, not agent execution. But it must not be imported by engine code (currently only TYPE_CHECKING, which is acceptable).

### 2-6. `computer_use.py` + `computer_policy.py` → `agent_runtime`

**Decision:** Move both files to `agent_runtime/` as a specialized executor/tool.

`computer_use.py` (~25KB, 670 lines) is a Playwright-backed browser automation controller with an observe/act/verify loop, lease management, and `/computer` slash-command handling. `computer_policy.py` (~11KB) defines safety policy (domain allowlists, action classification, approval requirements, audit logging).

This is pure execution behavior — browser automation is a tool/capability, not an orchestration concern. It has zero imports from `dan.engine` and only imports `computer_policy` from within concierge.

Migration steps:
1. Move `computer_use.py` → `agent_runtime/computer_use.py`
2. Move `computer_policy.py` → `agent_runtime/computer_policy.py`
3. Update command_registry registration to import from new location.
4. The `ComputerUseController` becomes an agent_runtime executor alongside other tool executors.

## Dependency Cycle Resolution

### 2-7. Engine imports in `runtime.py` — inventory and resolution

`runtime.py` has 20 deferred `from dan.engine` imports (all inside functions, not top-level). Grouped by purpose:

**Memory / Knowledge (14 imports):**
- `memory_kernel.MemoryItem` (×5 call sites) — creating memory items
- `memory_kernel.MemoryScope` (×4) — scope constants
- `memory_kernel.MemoryType` (×5) — type constants
- `memory_kernel.classify_task_type` (×1) — task-type classification
- `memory_kernel.MemoryLifecycle` — via `domain_learning.py` (indirect)
- `memory_extractor.MemoryExtractor` (×1) — post-task memory extraction
- `user_profile.save_user_profile` (×1) — persisting user profile

**Adaptation / Tracking (3 imports):**
- `adaptation_registry.AdaptationRegistry` (×1) — behavior adaptation tracking
- `correction_memory.CorrectionStore` (×1 init, ×1 usage) — correction/retry memory
- `outcome_trackers.PromptTracker` (×1) — prompt outcome tracking

**Domain / Learning (3 imports):**
- `domain_taxonomy.format_domain_label` (×1) — domain label formatting
- `preference_extractor.PreferenceExtractor` (×1) — user preference extraction
- `learning_tiers.is_feature_enabled` (×2) — feature-flag gating

**Additional engine imports in other concierge files:**
- `triage.py`: `behavior_store.BehaviorStore` (TYPE_CHECKING only)
- `domain_learning.py`: `domain_taxonomy`, `learning_tiers`, `memory_kernel` (5 direct imports + 2 TYPE_CHECKING)
- `domain_preferences.py`: `domain_taxonomy`, `memory_adapters`, `memory_kernel`, `user_profile` (4 imports)

**Total across concierge package: ~30 engine imports.**

### Proposed Resolution: `MemoryServices` Protocol

Define a `MemoryServices` protocol/interface that bundles the memory operations concierge needs. The composition root (`startup.py`) constructs and injects it. This breaks the direct import cycle without moving all the engine code.

```python
# dan/llm_core/types.py or dan/shared/memory_protocol.py

from typing import Protocol, Any, runtime_checkable

@runtime_checkable
class MemoryServices(Protocol):
    """Operations concierge needs from the memory/learning subsystem."""

    async def store_memory(self, item: Any) -> None: ...
    async def recall(self, scope: str, query: str, **kw: Any) -> list[Any]: ...
    async def extract_memories(self, context: Any) -> list[Any]: ...
    async def extract_preferences(self, context: Any) -> dict[str, Any]: ...
    async def save_profile(self, profile: Any) -> None: ...
    def classify_task_type(self, text: str) -> str: ...
    def is_feature_enabled(self, feature: str) -> bool: ...
    def format_domain_label(self, domain: str) -> str: ...
```

**Injection flow:**
1. `startup.py` constructs `MemoryKernel`, `BehaviorStore`, `AdaptationRegistry`, etc. as today.
2. `startup.py` wraps them in a `MemoryServicesImpl` that satisfies the protocol.
3. `ConciergeRuntime.__init__` receives `memory_services: MemoryServices` instead of importing engine internals.
4. Concierge code calls `self._memory.store_memory(...)` instead of `from dan.engine.memory_kernel import MemoryItem; ...`.

**Benefits:**
- Breaks the 30-import coupling between concierge and engine internals.
- Engine can evolve memory storage without touching concierge.
- Concierge can be tested with a mock `MemoryServices` — no engine imports needed.
- Aligns with the composition-root pattern already established in `startup.py`.

**Sequencing:** This depends on 41-1 (gateway exists) and 41-5 (composition root cleanup). The protocol can be defined now; wiring happens in 41-5.

## Notes

- This sub-plan should become easier once `41-1` and `41-2` exist. Trying to narrow concierge before those seams are real would just move code around without reducing coupling.
- `triage.py` currently imports from `dan.engine.behavior_store`. This means concierge depends on engine internals, creating a potential bidirectional coupling concern when 41-4 tightens the engine boundary. The interface `triage.py` needs should be formalized as a shared contract or injected at composition time.
- 2026-03-23 follow-up: extracted the behavior/adaptation bootstrap cluster out of `Concierge.__init__` into `server/concierge/learning_bundle.py`. `build_concierge()` now assembles that bundle before constructing the orchestrator, and a structural AST test locks in that `Concierge.__init__` no longer directly imports `dan.engine` learning modules. Remaining method-level engine imports in `runtime/__init__.py` are still the larger `MemoryServices` follow-up.
- 2026-03-23 follow-up: narrowed two more runtime-owned engine seams without changing turn behavior. `runtime/__init__.py` now gets learning-tier feature gates through the injected `ConciergeLearningBundle`, and correction analysis moved behind `server/concierge/correction_feedback.py` plus an injected analyzer hook. A new AST boundary test forbids `dan.engine.learning_tiers` and `dan.engine.correction_memory` imports in concierge runtime.
- 2026-03-23 follow-up: moved the post-turn preference/memory enrichment mechanics out of runtime as well. `server/concierge/memory_enrichment.py` now owns the `PreferenceExtractor`, `MemoryExtractor`, domain-label formatting, and `save_user_profile` imports, while `runtime/__init__.py` keeps the same `_try_extract_preferences()` / `_try_memory_extraction()` delegate surface for queue/orchestration code.
- 2026-03-23 follow-up: finished the runtime-side `MemoryServices` narrowing step. `server/concierge/memory_services.py` now owns concierge state persistence, memory-context retrieval, domain-expertise ranking, domain-reflection project scoping, and raw `MemoryItem` construction over the injected `MemoryKernel`. `runtime/__init__.py` now has zero direct `dan.engine` imports, and `triage.py` no longer carries its old type-only `BehaviorStore` edge either.
- 2026-03-23 follow-up: extracted `domain_taxonomy` into the neutral top-level module `dan.domain_taxonomy` and turned `engine/domain_taxonomy.py` into a compatibility facade. That removed another shared-vocabulary-in-the-engine coupling across `domain_learning.py`, `domain_preferences.py`, `memory_enrichment.py`, `chat_manager.py`, `memory_adapters.py`, `user_profile.py`, and `preference_extractor.py` without changing behavior.
- 2026-03-23 follow-up: narrowed `/domains` ownership by moving profile-memory synchronization behind `server/concierge/profile_domain_sync.py`. `domain_preferences.py` is now engine-import-free and focuses on command parsing/rendering, while the helper owns `ProfileAdapter`, `MemoryKernel` fact syncing, and `save_user_profile`.
- 2026-03-23 follow-up: tightened `domain_learning.py` without changing its overall ownership yet. The file now uses the neutral helper `dan.keyword_overlap.query_keyword_overlap()` instead of importing the private engine helper `memory_kernel._keyword_overlap`, and its old type-only `BehaviorStore` / `MemoryKernel` imports were dropped in favor of local `Any` annotations. Remaining engine coupling in `domain_learning.py` is now about real memory/learning semantics rather than leaked utility helpers.
- 2026-03-23 follow-up: narrowed `domain_learning.py` again by removing direct `learning_tiers` and `AdaptationCandidate` ownership from the main file. `feature_gates.py` now centralizes the learning-tier lookup, `DomainReflector` and the memory-kernel consolidation hook consume injected feature gates, and `domain_learning_adaptations.py` now owns the auto-discovery / keyword-expansion helpers. `domain_learning.py` itself is now down to `memory_kernel`-centric engine imports only.
- 2026-03-23 follow-up: remaining concierge-package engine coupling is now concentrated in explicit helper/knowledge modules such as `learning_bundle.py`, `memory_services.py`, `memory_enrichment.py`, `correction_feedback.py`, `profile_domain_sync.py`, and the still-heavier `domain_learning.py` rather than the main orchestrator, routing, or `/domains` command module.
- 2026-03-23 follow-up: concierge's small completion helpers now share `dan.llm_surface.complete_chat_surface()`. `Concierge._triage_llm_complete()`, `tier_executors.py::_cheap_llm_complete()`, and domain reflection all prefer `ChatManager.model_gateway` when present and fall back through the public provider seam only when the shared gateway is absent. The compatibility fallback still exists, but it now lives in one neutral helper instead of three copy-pasted call sites.
- 2026-03-23 follow-up: the pure synthesis/decomposition review helpers are thinner too. `src/dan/agent_runtime/synthesis.py` now owns planned-subtask extraction, decomposition-response parsing, deterministic synthesis-gap review, and synthesis-review JSON parsing, while `tier_executors.py` keeps thin compatibility wrappers plus the actual child-session orchestration.
- 2026-03-24 follow-up: extracted the remaining pure multi-step heuristics out of concierge as well. `src/dan/agent_runtime/orchestration.py` now owns child-tier estimation, mixed execution grouping, LLM-synthesis gating, token-usage rollup, and combined child-result rendering, while `tier_executors.py` keeps session-manager interaction, child creation, and dispatcher callbacks.
- 2026-03-24 follow-up: narrowed child-session planning too. `src/dan/agent_runtime/orchestration.py` now owns primitive child-session envelope planning via `plan_child_session()`, including route filtering, handoff construction, child-tier choice, parent-thread propagation, and mutation-tool gating, while `tier_executors.py` keeps `SessionManager` mutation, `SurfaceMessage`/`TriageResult` construction, and the actual child execution loop.
- 2026-03-24 follow-up: the remaining child execution policy is out of concierge too. `src/dan/agent_runtime/orchestration.py` now owns the serial/mixed/parallel scheduling helper via `execute_child_execution_policy()`, including previous-result carry-forward, mixed-group summarization, interruption forwarding, and child-result rollup, while `tier_executors.py` keeps child creation, cancellation/result setting, direct-fallback decisions, remediation spawning, and dispatcher callbacks.
- 2026-03-23 follow-up: completed the concierge-side PII rewiring by switching runtime registry initialization to `dan.llm_core.pii_tokenizer.SensitiveWordRegistry`. The old `server/concierge/pii_tokenizer.py` module is now purely a compatibility facade.
- 2026-03-23 follow-up: strengthened stage/tier provenance regressions without broadening scope. `tests/test_concierge/test_tiered_dispatch.py` now covers the explicit `workflow_build` action-hint path, and `tests/test_concierge/test_tiering.py` now asserts the orchestration-stage vocabulary stays aligned with both `CONCIERGE_STAGE_TIERS` and `_STAGE_PROMPT_OVERLAYS`.
- 2026-03-23 follow-up: finished neutralizing concierge's sibling-`server` imports by extracting shared seams (`dan.chat_events`, `dan.llm_surface`, `dan.chat_prompts`, `dan.telemetry_api`, `dan.web_surface`) and repointing concierge to them. The final `server`↔`server.concierge` circular-boundary pair is now gone, while `server/*` compatibility entry points remain available as facades.
- 2026-03-23 follow-up: session provenance is now visible beyond giant logs. `SessionTrace` and `export_tree()` now carry route target, action hints, route source, scenario, stage, and autonomy fields, and the persisted assistant-turn `session_tree` metadata in `tiered_dispatch.py` now forwards the same provenance for root-turn debugging and replay.
