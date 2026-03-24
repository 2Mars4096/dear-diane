# 41-4: Workflow Runtime Isolation

**Parent:** [41-internal-runtime-submodule-restructure](41-internal-runtime-submodule-restructure.md)
**Status:** completed
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
- [x] 1-1. Identify the internal contracts that make up the workflow subsystem: graph model, build/load/compile, execute, checkpoint, resume, and event stream. **Note:** checkpoint/resume support currently spans engine, run-manager, and publish/runtime adapters rather than one cleanly isolated API. The contracts defined here should document that current layering honestly instead of assuming a cleaner boundary than the code actually has.
- [x] 1-2. Document which pieces are canonical runtime APIs vs server-only adapters.
- [x] 1-3. Make the runtime dependencies on model access explicit through `llm_core` or a runtime-safe adapter. Agent-style executors (`LLMExecutor` tool loops, `ReflectionExecutor`) may also need `agent_runtime` interfaces after 41-2 lands. **Done:** `Engine` now exposes a first-class `model_gateway`, threads it through `ExecutionContext`, executor-side provider resolution prefers that explicit gateway path, the completion-only executor helpers build/cache a runtime gateway from config instead of falling back to raw `AsyncOpenAI`, the `LLMExecutor` provider seam resolves through a gateway-backed provider adapter when a real or synthesized runtime gateway exists, and the engine composition seam accepts injected `provider_registry` / `model_gateway` / `embedding_registry` instances so runtime wiring no longer has to originate inside `Engine`. Default engine construction still builds those registries as a composition-time convenience, but there is no raw no-gateway provider fallback left in the workflow-runtime execution path.

### 2. Reduce surface coupling
- [x] 2-1. Audit `server`, `publish`, and CLI integration points that currently reach into workflow internals in ad hoc ways. **Done:** `src/dan/cli/run.py` now builds its execution engine through a single helper seam, `src/dan/cli/adapter.py` does the same for the legacy workflow-mode runner, and `src/dan/server/routers/adapters.py` now prefers the shared `RunManager._make_engine()` seam before falling back to the local constructor for direct-test compatibility.
- [x] 2-2. Pull adapter responsibilities toward `run_manager`, publish runtime, and composition-root modules rather than letting them leak into engine internals. **Done:** `RunManager` and `publish.runtime.LocalRuntime` now centralize their `Engine` construction seams, accept an explicit `model_gateway`, and the startup/chat-factory composition roots thread the shared gateway into those workflow adapters instead of relying only on implicit engine-owned provider setup.
- [x] 2-3. Keep graph execution testable without server startup. *(isolation test added: `tests/test_workflow_runtime/test_isolation.py`)*
- [x] 2-4. Resolve pre-existing reverse-dependency violations where `engine/` and `executors/` import from `server/`:
  - ~~`engine/skill_tracker.py` → `dan.server.skill_library`~~ — **landed:** `SkillRefiner` accepts optional `skill_text_lookup: Callable[[str], str | None]`; no server import in engine. Composition roots can inject `SKILL_LIBRARY` lookup when `SkillRefiner` is wired.
  - ~~`engine/behavior_store.py` → `dan.server.telemetry`~~ — **landed:** `ParameterDecisionLogger` takes optional `build_event` callback; concierge passes a closure that builds `TelemetryEvent`. Fallback dict payload when `build_event` is absent.
  - ~~`engine/preference_extractor.py` → `dan.server.concierge.domain_learning`~~ — **landed:** `domain_keywords_provider: Callable[[], dict[str, list[str]]] | None` on `PreferenceExtractor`; concierge and CLI chat inject `get_domain_keyword_map`.
  - ~~`engine/plan_scheduler.py` → `dan.server.concierge.resources`~~ — **resolved**: this import no longer exists in the codebase. Remove from violation list.
  - ~~`engine/memory_kernel.py` → `dan.server.concierge.domain_learning`~~ — **landed:** domain consolidation moved to `domain_learning.consolidate_memory_kernel_domain_templates(kernel)`. `MemoryKernel` accepts optional `domain_consolidation_hook` (keyword-only); `startup.py` and `chat_factory.py` inject the function. **No `dan.server` imports remain under `src/dan/engine/`** (verified by boundary tests). The `engine`↔`server.concierge` circular import pair is resolved.
  - ~~`executors/llm.py` → `dan.server.concierge.pii_tokenizer`~~ — **landed:** PII implementation moved to `llm_core/pii_tokenizer.py`; `executors/llm.py` imports from `dan.llm_core`. `server/concierge/pii_tokenizer.py` re-exports for backward compatibility.
  At planning time, these imports meant engine/executors could not be treated as fully surface-independent. That runtime coupling has since been removed.

### 3. Clarify workflow-generation vs workflow-execution ownership
- [x] 3-1. Keep generation/planning helpers separate from the core workflow execution engine. **Done:** workflow planning/generation remains in `meta/` and server-side workflow-generation adapters, while `engine/` and the executor layer stay focused on graph execution.
- [x] 3-2. Decide which “build from intent” helpers belong to agent/conversation layers and which belong to workflow-runtime adapters. **Decision:** prompt/codegen/diagnosis/mutation-fallback helpers for “build from intent” stay above workflow-runtime in `meta/` plus the server-side workflow-generation adapter layer (`src/dan/server/agent_runtime/workflow_generation*.py`, `workflow_handoff.py`, `workflow_outcomes.py`); core workflow-runtime owns only graph execution, checkpoint/resume, and runtime-safe executor/provider seams.
- [x] 3-3. Keep the execution engine neutral about whether a graph came from builder DSL, markdown loader, chat mutation, or a saved file. **Done:** the engine still runs the shared `Graph` model without branching on graph origin; graph authoring/generation concerns stay above it.
- [x] 3-4. Decide the module ownership for `src/dan/meta/` (planning, intent compilation, generation). **Done:** `meta/` is a peer of `workflow_runtime`, with skill-library access injected and workflow-mutation access going through the neutral `dan.graph_mutator` seam instead of direct `server/` imports.

  **Decision (2026-03-23):** `meta/` is a peer module of workflow-runtime, NOT a sub-component of it. It sits between `workflow_runtime` and `server` in the dependency graph. Resolution path:
  - `GraphMutator` + `MutationPlan` + `PATTERN_LIBRARY`: used by `planner.py`, `controller.py`, `repair.py`, `discovery.py`. These graph-mutation interfaces are now consumed through the neutral `dan.graph_mutator` seam rather than direct `server/` imports.
  - `SKILL_LIBRARY` dict: used by `authoring.py` (writes) and `discovery.py` (reads). **Landed (2026-03-23):** both files now consume injected skill-library mappings instead of importing `dan.server.skill_library`; server composition roots pass the concrete `SKILL_LIBRARY` object.
  - The remaining workflow-planning mutation dependency is intentionally routed through `dan.graph_mutator`, keeping `meta/` out of `server/`.

### 4. Tighten workflow-runtime tests
- [x] 4-1. Add contract tests around graph execution, provider invocation, and resume/checkpoint behavior through the isolated runtime APIs. **Done:** `tests/test_workflow_runtime/test_isolation.py`, `tests/test_workflow_runtime/test_checkpoint_contracts.py`, `tests/test_parity/test_workflow_runtime_adapter_parity.py`, `tests/test_executors/test_provider_runtime.py`, and `tests/test_engine/test_rag.py` together verify importable graph execution, checkpoint persistence plus `Engine.resume()` state reuse, injected runtime-composition seams, and gateway-aware provider routing without requiring server startup.
- [x] 4-2. Add regressions confirming runtime behavior does not depend on server-only initialization order. *(isolation test: `tests/test_workflow_runtime/test_isolation.py`)*
- [x] 4-3. Add adapter tests for run manager / publish / local execution surfaces over the isolated runtime. **Done:** `tests/test_parity/test_workflow_runtime_adapter_parity.py` verifies default engine composition matches a run-manager-style prewired executor registry, preserves injected `tool_operator` executors, and confirms `publish.runtime.LocalRuntime` is constructible without server startup.

## Primary Files

- `src/dan/models/` — graph schema, node types, edge types. **Note:** this is a shared schema layer consumed by multiple submodules (chat mutation, meta planner, graph mutator), not owned exclusively by workflow runtime. The boundary contract (41-6) should reflect this.
- `src/dan/builder/` — builder DSL, compiler (~18KB), decompiler (~53KB)
- `src/dan/loader/` — markdown loader, compiler (~64KB), decompiler (~38KB)
- `src/dan/engine/` — scheduler (~149KB), memory kernel (~58KB), token optimization (~60KB)
- `src/dan/executors/` — node executors including `control_flow.py` (~108KB), `llm.py` (~60KB), `reflection.py` (~24KB)
- `src/dan/server/run_manager.py` — run lifecycle and execution adapters (~72KB)
- `src/dan/publish/` — published workflow runtime, HTTP server, portal, sessions

## API Surface

Documented 2026-03-23 (tasks 1-1, 1-2).

### Canonical workflow-runtime APIs (core runtime — no server dependency)

| Layer | Package | Key exports | Role |
|-------|---------|-------------|------|
| Schema | `models/` | `Graph`, node types (`LLMNode`, `CodeNode`, `HumanNode`, `GoalLoopNode`, `VoteNode`, `AgentTeamNode`, …), `DataEdge`, `ControlEdge`, `ContextEdge`, `Port`, `PortSchema` | Shared graph data model. Consumed by engine, builder, loader, meta, graph_mutator. No server deps. |
| Builder | `builder/` | `WorkflowBuilder`, `workflow()`, `NodeRef`, `PortRef`, `BuildError`, `decompile()` | Fluent Python DSL for programmatic graph construction. Compiles to `Graph`. No server deps. |
| Loader | `loader/` | `load()`, `compile_workflow()`, `load_agents()` | Markdown/YAML graph loading and compilation. Compiles to `Graph`. No server deps. |
| Engine | `engine/` | `Engine`, `EngineConfig`, `ExecutionContext`, `ExecutorRegistry`, `NodeExecutor`, `NodeResult`, `EngineEvent`, `EventType`, `RunResult`, `MemoryStore`, `StateStore`, `CheckpointStore` | Async graph execution scheduler. No direct `server/` imports remain; composition-root injection and explicit runtime seams keep it importable/testable without server startup. |
| Executors | `executors/` | `LLMExecutor`, `ControlFlowExecutor`, `ReflectionExecutor`, `RAGExecutor`, `CodeOperatorExecutor` | Node executor implementations. Model access now routes through `llm_core` or a runtime-safe gateway-backed adapter; no direct `server/` imports remain. |

### Server-only adapters (NOT part of core runtime)

| Layer | Package | Role |
|-------|---------|------|
| Run Manager | `server/run_manager.py` | Run lifecycle management — creates `Engine`, wires checkpoints, manages server-side run state |
| Publish Runtime | `publish/` | Published workflow HTTP server, portal, session management |
| Graph Router | `server/routers/graphs.py` | HTTP API for graph CRUD |
| Chat Factory | `server/chat_factory.py` | Wires chat → workflow execution |
| Startup | `server/startup.py` | Server composition root — assembles runtime with all adapters |

### Checkpoint / resume layering (honest documentation)

Checkpoint/resume is NOT cleanly isolated into a single API:
- `engine/checkpoint.py` — defines `CheckpointStore` protocol and `FileSystemCheckpointStore`
- `engine/scheduler.py` — calls checkpoint store at critical nodes during execution
- `server/run_manager.py` — manages checkpoint lifecycle (create, restore, clean up) and wires `CheckpointStore` into `Engine`
- `publish/runtime.py` — additional checkpoint adapter for published workflow runs

The engine owns the checkpoint *protocol* and *call sites*, but run-manager and publish own the *lifecycle* and *configuration*.

## Violation Analysis

Documented 2026-03-23 (task 2-4 analysis phase) and closed 2026-03-24.

### Summary

| # | Source | Target | Import style | Status |
|---|--------|--------|-------------|--------|
| 1 | `engine/skill_tracker.py` | `dan.server.skill_library` | Historical | **Resolved** — skill lookup now uses injected `skill_text_lookup` |
| 2 | `engine/behavior_store.py` | `dan.server.telemetry` | Historical | **Resolved** — telemetry event building now uses injected callbacks |
| 3 | `engine/preference_extractor.py` | `dan.server.concierge.domain_learning` | Historical | **Resolved** — domain keywords now come from injected providers |
| 4 | ~~`engine/plan_scheduler.py`~~ | ~~`dan.server.concierge.resources`~~ | — | **Resolved** — import removed |
| 5 | `engine/memory_kernel.py` | `dan.server.concierge.domain_learning` | Historical | **Resolved** — domain consolidation now uses an injected hook |
| 6 | `executors/llm.py` | `dan.server.concierge.pii_tokenizer` | Historical | **Resolved** — PII/tokenization now lives under `llm_core` |

### Resolution pattern

**Pattern:** Each violation follows the same fix pattern:
1. Define a minimal protocol/callable type in the engine module that needs it
2. Accept an optional injector at construction time (or on the `EngineConfig`)
3. When the injector is `None`, degrade gracefully (skip the feature or return empty)
4. The server composition root (`startup.py` / `run_manager.py`) injects the real implementation

This pattern now backs the landed engine/executor isolation work: the workflow runtime stays importable and testable without server packages while preserving full functionality when composed inside the server.

**Violation 1 — skill_tracker → skill_library:**
```python
# In engine/skill_tracker.py — protocol definition
SkillLookup = Callable[[str], str | None]  # (skill_name) -> skill_text
```
Constructor accepts `skill_lookup: SkillLookup | None = None`. When `None`, only memory-kernel search is used.

**Violation 2 — behavior_store → telemetry:**
```python
# In engine/behavior_store.py — local data model
@dataclass
class ParameterDecisionRecord:
    event_type: str  # always "parameter_decision"
    parameter_key: str
    parameter_value: str
    metadata: dict
```
The `log_decision()` method constructs this local dataclass. The server adapter maps it to `TelemetryEvent` when recording.

**Violation 3 — preference_extractor → domain_learning:**
```python
# In engine/preference_extractor.py — protocol
DomainKeywordResolver = Callable[[Any], dict[str, list[str]]]
```
Constructor accepts `domain_keyword_resolver: DomainKeywordResolver | None = None`. When `None`, `_resolve_domain_keywords()` returns `{}`.

**Violation 5 — memory_kernel → domain_learning (largest):**
```python
# In engine/memory_kernel.py — protocol
class DomainConsolidator(Protocol):
    def consolidate(self, domain: str, items: list[MemoryItem], kernel: "MemoryKernel") -> int: ...
```
Constructor accepts `domain_consolidator: DomainConsolidator | None = None`. When `None`, `_consolidate_domain_templates()` returns 0 immediately. The server-side implementation wraps `DomainPatternGeneralizer`, `DomainTemplateConsolidator`, `get_or_create_template`, and `save_domain_template`.

**Violation 6 — llm.py → pii_tokenizer:**
Resolved by `llm_core` gateway/tokenizer ownership plus the gateway-backed executor runtime seam.

## Decisions

- `workflow_runtime` remains an internal DAN subsystem, not a separately versioned package in this plan family.
- Workflow execution should be surface-agnostic even when workflow authoring and workflow-generation remain chat-aware.
- (2026-03-23) `meta/` is a peer module, not part of workflow-runtime. Its workflow-facing mutation dependency now goes through the neutral `dan.graph_mutator` seam instead of importing `server/` directly.
- (2026-03-23) `plan_scheduler.py → server.concierge.resources` violation confirmed removed. Dropped from violation list.
- (2026-03-24) No direct `engine/` or `executors/` → `server/` imports remain. The isolation and parity suites now cover both importability and runtime-safe composition behavior.
- (2026-03-23) Protocol-injection is the preferred pattern for all violations: define a minimal protocol in the engine module, accept an optional injector, degrade gracefully when absent.

## Notes

- This sub-plan is deliberately about boundary tightening, not about rewriting the engine. The goal is to make the runtime easier to compose and safer to extend.
- `src/dan/models/` is consumed by `graph_mutator.py` (chat-side), `meta/` (planning), and `loader/` (markdown) in addition to the engine. It should be treated as a shared schema layer in the dependency rules, not scoped exclusively under workflow runtime.
- 2026-03-23 follow-up: `meta/discovery.py` no longer imports `server.graph_mutator` for pattern discovery; it now serves pattern metadata from its local `_PATTERN_DESCRIPTIONS` table. `meta/discovery.py` and `meta/authoring.py` also no longer import `server.skill_library`; they use injected mappings instead. The remaining workflow-planning mutation dependency now goes through the neutral `dan.graph_mutator` seam rather than importing `server/` directly.
- 2026-03-23 follow-up: adapter parity is now covered at the composition seam rather than only through isolation notes. `tests/test_parity/test_workflow_runtime_adapter_parity.py` compares the default `Engine()` executor surface with a run-manager-style prewired registry and adds a lightweight publish-surface constructibility check via `LocalRuntime`.
- 2026-03-23 follow-up: provider-routing inside the executor layer is a bit tighter now too. `RAGExecutor` reranking now resolves its completion provider through `executors/provider_runtime.py` instead of reaching straight into `provider_registry`, so gateway-aware routing applies there as well; direct coverage lives in `tests/test_executors/test_provider_runtime.py` and `tests/test_engine/test_rag.py`.
- 2026-03-24 follow-up: `LLMExecutor` now resolves providers through `executors/provider_runtime.py` as well, preserving the PII-tokenizer wrapping path while removing the duplicate executor-local resolution logic. Direct coverage lives in `tests/test_executors/test_provider_runtime.py` and `tests/test_engine/test_llm_tool_calling.py`.
- 2026-03-24 follow-up: completion-style workflow executors now stay on the shared runtime seam even when an `ExecutionContext` was not fully prewired. `executors/provider_runtime.py` now builds/caches a `ModelGateway` from runtime config for `resolve_completion_provider()`, so router/orchestrator/vote/reflection/rerank completions no longer fall back to a raw `AsyncOpenAI` helper outside full engine composition. `resolve_llm_provider()` now returns a gateway-backed adapter when a gateway is available, and the remaining config-only path also synthesizes a gateway rather than opening a raw client path.
- 2026-03-23 follow-up: workflow-runtime model access is a bit more explicit too. `Engine` now keeps a `model_gateway` alongside its provider registry, `_make_context()` threads that gateway into `ExecutionContext`, and the workflow-runtime parity tests now lock in that the gateway is present on engine-built execution contexts when the provider registry is gateway-compatible.
- 2026-03-23 follow-up: the workflow adapter seams are thinner too. `RunManager` and `publish.runtime.LocalRuntime` now each own a single `_make_engine()` construction seam, accept an explicit `model_gateway`, and the startup/chat-factory composition roots now thread the shared gateway through those adapters. Direct coverage lives in `tests/test_parity/test_workflow_runtime_adapter_parity.py` and `tests/test_parity/test_composition_parity.py`.
