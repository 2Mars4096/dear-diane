# 19-7: System Architect Mode

**Parent:** [19-meta-orchestrator](19-meta-orchestrator.md)
**Status:** in-progress
**Goal:** Enable the meta-orchestrator to decompose a complex, high-level user intent into a coordinated multi-workflow system — with shared memory, cross-workflow tools/skills, and a routing layer — then build and wire all components.

## Problem

The existing `WorkflowPlanner` (19-2) builds a single workflow from a goal. But many real intents require multiple cooperating workflows:

- A system with an ingestion pipeline, a processing workflow, and a reporting workflow sharing a common data store
- An interactive assistant backed by multiple specialized sub-workflows routed by intent
- A continuous monitoring system with trigger-based dispatch to different action workflows

Today, the user must manually decompose such systems, build each workflow separately, and wire the shared state. The meta-orchestrator should handle this decomposition and construction.

## Approach

Add a `SystemArchitect` that sits above the `WorkflowPlanner`:

1. **Analyze** the user's intent to determine if it requires a single workflow or a multi-workflow system
2. **Decompose** into individual workflow specs with explicit interfaces (inputs, outputs, shared state)
3. **Identify cross-cutting concerns** — shared tools, skills, memory collections, and state that span workflows
4. **Plan each workflow** by invoking the existing `WorkflowPlanner` per-spec
5. **Wire the system** — shared memory keys, event routing, concierge/router configuration
6. **Validate** the assembled system end-to-end before handing off to execution

## Primary Files

| File | Role |
|------|------|
| `src/dan/meta/architect.py` (new) | `SystemArchitect` — decomposition, cross-cutting analysis, system assembly |
| `src/dan/meta/planner.py` | Called per-workflow by architect |
| `src/dan/meta/authoring.py` (19-6) | Called to create shared tools/skills |
| `src/dan/meta/controller.py` | Extended to manage multi-workflow sessions |
| `src/dan/engine/memory_store.py` | Shared GLOBAL-scoped memory across workflows |
| `tests/test_meta/test_architect.py` (new) | Unit + integration tests |

## Tasks

- [x] 1. **System decomposition**
  - [x] 1-1. Define `WorkflowSpec` model: `name`, `goal`, `inputs` (list of named typed ports), `outputs` (list of named typed ports), `shared_state_keys` (memory keys this workflow reads/writes), `required_tools`, `required_skills`, `triggers` (how this workflow is activated: manual | schedule | event | upstream-output)
  - [x] 1-2. Define `RoutingConfig` model: `strategy` (intent_classifier | round_robin | manual), `intent_model` (LLM model for classification), `fallback_workflow` (default when no intent matches)
  - [x] 1-3. Define `SystemPlan` model: `workflows: list[WorkflowSpec]`, `shared_tools: list[ToolSpec]` (from 19-6), `shared_skills: list[SkillSpec]` (from 19-6), `shared_memory_keys: list[str]`, `routing_config: RoutingConfig | None`
  - [x] 1-4. `SystemArchitect.decompose(goal: str, context: DiscoveryResult) -> SystemPlan` — LLM decomposes intent into `SystemPlan`, grounded by self-knowledge (19-5) and existing workflows

- [x] 2. **Complexity classification**
  - [x] 2-1. `SystemArchitect.classify(goal: str) -> "single" | "multi"` — determine if the goal needs one workflow or a system of workflows
  - [x] 2-2. If `single`, delegate directly to `WorkflowPlanner` (existing path, no overhead)
  - [x] 2-3. If `multi`, proceed with full decomposition pipeline

- [ ] 3. **Cross-cutting concern resolution**
  - [ ] 3-1. For each `shared_tool` in the `SystemPlan`, check `ToolRegistry` — if missing, invoke `RuntimeAuthor` (19-6) to create it
  - [ ] 3-2. For each `shared_skill`, check `SKILL_LIBRARY` — if missing, invoke `RuntimeAuthor` to create it
  - [x] 3-3. For `shared_memory_keys`, configure `MemoryStore` GLOBAL-scope entries with appropriate schemas
  - [ ] 3-4. Human approval gate for generated cross-cutting artifacts (if `human_override` enabled)

- [x] 4. **Per-workflow planning**
  - [x] 4-1. For each `WorkflowSpec`, invoke `WorkflowPlanner.plan()` with the spec's goal, required tools, required skills, and interface constraints (input/output ports)
  - [x] 4-2. Pass shared context: other workflow specs (for cross-reference), shared memory key names
  - [x] 4-3. Collect all `PlannerOutput` results

- [ ] 5. **System wiring**
  - [ ] 5-1. If `routing_config` is present, generate a routing/concierge workflow that dispatches intents to the appropriate sub-workflows
  - [x] 5-2. Wire memory: ensure all workflows that declare the same `shared_state_keys` use consistent key names and value schemas
  - [ ] 5-3. Wire events: for `trigger: upstream-output` workflows, register a system-level subscriber on `GlobalEventBus` (23-1) that watches for workflow completion events and dispatches downstream workflows. Note: `GlobalEventBus` currently broadcasts to WebSocket client queues — the trigger handler subscribes as an internal consumer alongside external clients.
  - [x] 5-4. Generate a `SystemManifest` (JSON/YAML) that describes the full system: workflows, routing, shared state, dependencies

- [x] 6. **System validation**
  - [x] 6-1. Interface compatibility: output ports of upstream workflows match input ports of downstream workflows
  - [x] 6-2. Memory key consistency: shared keys have compatible schemas across all workflows that use them
  - [x] 6-3. Tool/skill availability: all required tools and skills exist in registry
  - [x] 6-4. No circular dependencies in the workflow dependency graph
  - [x] 6-5. Dry-run validation: attempt to build each workflow graph via builder, catch compilation errors

- [x] 7. **Controller integration**
  - [x] 7-1. Extend `MetaSession` to track multiple workflows: add `workflow_ids: list[str]` and `run_ids: list[str]` (currently tracks single `workflow_id`/`run_id`)
  - [x] 7-2. Extend `MetaController` to accept a `SystemPlan` and manage multi-workflow sessions
  - [x] 7-3. `MetaController.run_system(plan: SystemPlan)` — execute workflows in dependency order, feed outputs to downstream workflows via `MemoryStore` GLOBAL-scope keys
  - [x] 7-4. Repair escalation: if one workflow fails, determine whether the fix is local (repair that workflow) or systemic (re-decompose the `SystemPlan`)
  - [x] 7-5. System-level experience: persist the `SystemPlan` and its execution outcome for future reuse (via `ExperienceStore`)

- [ ] 8. **Tests**
  - [ ] 8-1. Unit: `classify()` correctly distinguishes single vs. multi-workflow intents
  - [ ] 8-2. Unit: `decompose()` produces a valid `SystemPlan` with consistent interfaces
  - [ ] 8-3. Unit: system validation catches mismatched port schemas
  - [ ] 8-4. Unit: system validation catches circular workflow dependencies
  - [ ] 8-5. Integration: full pipeline — decompose → create tools/skills → plan each → wire → validate → execute
  - [ ] 8-6. Integration: system with shared memory — upstream workflow writes, downstream workflow reads

- [ ] 9. **Documentation**
  - [x] 9-1. Update `architecture.md` — document `architect.py` and `SystemPlan` model
  - [ ] 9-2. Update `llm-api-guide.md` — document system architect capabilities for LLM callers

## Decisions

- (to be filled during execution)

## Notes

- This is the capstone of the self-authoring stack: 19-5 (knowledge) → 19-6 (authoring) → 19-7 (orchestration). Each layer builds on the previous. `SystemPlan` imports `ToolSpec` and `SkillSpec` from `dan.meta.authoring` (19-6).
- The `SystemArchitect` is itself an LLM agent — it uses the same `WorkflowPlanner` it orchestrates, just at a higher abstraction level.
- The routing/concierge workflow is itself a DAN workflow (likely using `Router` or `OrchestratorNode`). The architect generates it via the planner.
- Multi-workflow execution leverages the existing `MetaController` session loop. The extension is managing multiple runs with inter-workflow data flow.
- The `GlobalEventBus` (Phase 13, 23-1) provides the event infrastructure for trigger-based workflow activation.
- `MemoryStore` GLOBAL scope (Phase 9A) provides cross-workflow shared state. No new persistence mechanism needed.
