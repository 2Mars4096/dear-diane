# 19-3: Structural Repair Engine

**Parent:** [19-meta-orchestrator](19-meta-orchestrator.md)
**Status:** in-progress
**Goal:** Extend the self-evolving orchestrator's repair capability from prompt-level fixes (levels 0–1) to structural changes: parameter/tool swaps (level 2), graph mutations (level 3), and full workflow redesign (level 4). The engine diagnoses what went wrong, classifies the severity, and applies the proportional fix.

## Existing Baseline

| Component | Location | What exists | Gap |
|---|---|---|---|
| Retry policies (7-1) | `executors/llm.py`, `executors/tool.py` | Configurable retry with backoff for transient failures | Level 0 only; no escalation path |
| Self-generating rules (9D/17-3) | `engine/rule_generator.py` | `CausalPrinciple → Hyperedge` (prompt-level guardrails/skills) | Level 1 only; cannot change node config, graph topology, or tools |
| `CausalPrinciple` | `engine/error_memory.py` | `condition/action/reason/confidence/tags` | No `repair_level` field — all principles are treated as prompt fixes |
| `GraphMutator` | `server/graph_mutator.py` | 13 mutation types, `PATTERN_LIBRARY`, validation, revision tracking | Available for level 3; not driven by automated diagnosis |
| `WorkflowPlanner` (19-2) | `meta/planner.py` (planned) | Generates/adapts workflows from goals | Available for level 4; not invoked from repair context |
| `ErrorMemoryIndex` (9D) | `engine/error_memory.py` | RAG over past errors with structured `ErrorRecord` | Diagnosis input; no severity classification |
| `RunStore` | `server/run_store.py` | Run summaries with errors, event logs | Diagnosis input |
| Runtime graph patch path | `engine/scheduler.py`, `server/graph_mutator.py` | Temporary graph copy + validated mutation pipeline | **Level 2 path** for parameter/tool swaps without mutating persisted graphs |

## Tasks

- [x] 1. Repair level classification
  - [x] 1-1. Extend `CausalPrinciple` model with `repair_level: Literal["prompt", "parameter", "structural", "redesign"] = "prompt"`. Backward-compatible default.
  - [x] 1-2. `RepairClassifier` class in new `meta/repair.py`:
    - `classify(principle: CausalPrinciple, graph: Graph, run_record: RunRecord) -> RepairLevel` — determines what kind of fix is needed based on:
      - **Prompt (level 1)**: action is a prompt-level instruction (matches 9D's existing negation/preference patterns)
      - **Parameter (level 2)**: action references model names, tool configs, temperature, token limits, or node parameters
      - **Structural (level 3)**: action references adding/removing/rewiring nodes, changing control flow, or adding validation steps
      - **Redesign (level 4)**: action indicates the fundamental approach is wrong, or the same failure has recurred after multiple level 1–3 repairs
    - Uses pattern matching on action text + recurrence history from `RuleLifecycleManager` (if a level-1 rule has been applied N times without improvement, escalate to level 2).
  - [x] 1-3. `RepairLevel` enum: `PROMPT = 1`, `PARAMETER = 2`, `STRUCTURAL = 3`, `REDESIGN = 4`. Includes helper `should_escalate(current_level, failure_count, max_attempts_per_level) -> RepairLevel`.

- [x] 2. Level 2 — parameter/tool swap repairs
  - [x] 2-1. `ParameterRepairGenerator` in `meta/repair.py`:
    - `generate_mutation(principle: CausalPrinciple, graph: Graph) -> MutationPlan` — produces runtime graph parameter mutations (no hyperedges):
      - Model swap: `EditNode.config.model`
      - Tool swap: `EditNode.config.tool_id`
      - Config change (whitelisted only): `temperature`, `max_tokens`, `timeout_seconds`, `retry_policy`, `tool_config`
    - Mutations are applied to a temporary graph copy at scheduler runtime; persisted graph JSON remains unchanged.
  - [x] 2-2. Canonical contract: when `principle.repair_level == "parameter"`, do **not** invoke `RuleGenerator`. Route directly to runtime parameter mutation application in the controller/scheduler path.

- [x] 3. Level 3 — graph mutation repairs
  - [x] 3-1. `StructuralRepairPlanner` in `meta/repair.py`:
    - `plan_repair(principle: CausalPrinciple, graph: Graph, error_context: str) -> MutationPlan | None` — uses an LLM call to analyze the principle + graph topology + error context and produce a `MutationPlan`:
      - Add validation node before a failing node
      - Add error handling branch (gate node with fallback path)
      - Replace a node with a composite sub-graph (e.g., replace single LLM call with review-revise loop)
      - Remove a node that's causing cascading failures
      - Rewire edges to change data flow
    - The LLM receives the graph exported as Python DSL (via `/export/python`) and the principle, and outputs a `MutationPlan` JSON.
  - [x] 3-2. Mutation validation: apply the plan via `GraphMutator.dry_run()` first. If dry run fails, feed errors back to the LLM for self-correction (max 2 retries). If still fails, escalate to level 4.
  - [x] 3-3. Mutation tracking: store structural mutation actions in a dedicated `RepairActionStore` (new), not `RuleLifecycleManager` (which is hyperedge-specific). Track `apply_count`, `success_count`, `failure_count`, and escalation metadata. If structural repair effectiveness remains below threshold after N runs, escalate to level 4.
  - [x] 3-4. Define `RepairActionRecord` model (`action_id`, `workflow_id`, `repair_level`, `payload`, `created_at`, `apply_count`, `success_count`, `failure_count`, `status`) used by `RepairActionStore` and redesign trigger heuristics.

- [x] 4. Level 4 — full redesign
  - [x] 4-1. `RedesignTrigger` in `meta/repair.py`:
    - `should_redesign(workflow_id: str, repair_history: list[RepairActionRecord]) -> bool` — returns true when:
      - N+ structural repairs have been applied without improvement
      - The same fundamental error pattern recurs across multiple repair attempts
      - The principle explicitly states the approach is wrong
    - When triggered, invokes `WorkflowPlanner.plan(goal, error_context)` where `error_context` includes the full repair history, failure patterns, and what's been tried.
  - [x] 4-2. Redesign preserves experience: the old workflow's experience (19-1) is passed to the planner as "what we tried and why it failed" so the new design avoids the same mistakes. (Implemented via `MetaController._update_experience(success=False)` which persists failure patterns before redesign.)
  - [x] 4-3. `RedesignResult` model: `new_graph: dict`, `old_workflow_id: str`, `reason: str`, `changes_summary: str`. Stored in experience memory for audit.

- [x] 5. Escalation controller
  - [x] 5-1. `RepairEscalator` in `meta/repair.py`:
    - `repair(principle: CausalPrinciple, graph: Graph, run_record: RunRecord) -> RepairAction` — the main entry point:
      1. Classify repair level
      2. Check escalation history (has a lower-level fix already been tried and failed?)
      3. Dispatch to the appropriate repair generator
      4. Return `RepairAction` — one of: `PromptFix(hyperedges)`, `ParameterFix(mutation_plan)`, `StructuralFix(mutation_plan)`, `Redesign(plan_result)`, `NoAction(reason)`
    - Respects `max_attempts_per_level` (default 2) — if a fix at level N has been tried twice without improvement, auto-escalate to level N+1.
  - [x] 5-2. Wire into the autonomous execution controller (19-4): after each failed run, the controller calls `RepairEscalator.repair()` for each new principle and applies the resulting actions.

- [x] 6. Configuration
  - [x] 6-1. `EngineConfig` fields:
    - `structural_repair_enabled: bool = False`
    - `repair_model: str | None` — LLM model for structural repair analysis (defaults to planner_model)
    - `max_repair_attempts_per_level: int = 2`
    - `auto_escalation_enabled: bool = True` — if false, structural/redesign repairs require human approval
    - `max_redesigns_per_goal: int = 2` — prevent infinite redesign loops

- [x] 7. Tests
  - [x] 7-1. Unit: `RepairClassifier` correctly classifies principles into repair levels based on action text.
  - [x] 7-2. Unit: `ParameterRepairGenerator` produces valid `MutationPlan`/`EditNode` payloads for model/tool/config changes with whitelist enforcement.
  - [x] 7-3. Unit: `StructuralRepairPlanner` with mocked LLM produces valid `MutationPlan` for common repair scenarios.
  - [x] 7-4. Unit: escalation logic — after 2 failed prompt fixes, classifier escalates to parameter; after 2 failed parameter fixes, escalates to structural; after 2 failed structural fixes, escalates to redesign.
  - [ ] 7-5. Integration: principle with `repair_level: "parameter"` → parameter mutation generated → applied to runtime graph copy → target node uses new model/config. (Deferred: requires full engine execution.)
  - [ ] 7-6. Integration: structural repair produces MutationPlan → dry_run validates → applied to graph → new node appears. (Deferred: requires full engine execution.)
  - [x] 7-7. Safety: max redesign cap enforced — after `max_redesigns_per_goal`, returns `NoAction` with reason.

## Decisions

- **Repair levels are progressive, not parallel.** The system always tries the smallest fix first and escalates only when lower-level fixes have been tried and failed. This avoids expensive restructuring for problems solvable by a prompt tweak.
- **Level 2 repairs use runtime graph mutation only.** Parameter swaps (model/tool/config) are expressed as whitelisted `EditNode` mutations on a temporary graph copy. Parameter-fix hyperedges are explicitly out of scope.
- **Level 3 repairs go through `GraphMutator`.** All structural changes use the existing mutation pipeline, which provides validation, revision tracking, and diagnostics. No direct graph JSON manipulation.
- **Level 4 is a fresh planning invocation.** Redesign doesn't try to patch the existing workflow further — it invokes `WorkflowPlanner.plan()` with the full error context so it can design a fundamentally different approach.
- **Escalation is bounded.** `max_attempts_per_level` and `max_redesigns_per_goal` prevent infinite repair loops. When limits are hit, the system stops and reports to the user.
- **Repair action tracking is persistent.** Each `RepairAction` gets a unique `action_id` and is persisted to `RepairActionStore`. Outcomes (`record_outcome`) update success/failure counts for accurate escalation decisions.

## Notes

- The escalation logic interacts with two stores: level 1 prompt fixes use 9D's `RuleLifecycleManager`, while levels 2–3 use `RepairActionStore`. Escalation compares effectiveness within the correct action family rather than forcing all repairs into hyperedge rule storage.
- Level 3 (structural repair) is the most LLM-intensive operation — it needs to understand graph topology, propose valid mutations, and handle self-correction. The repair model should be capable (gpt-4o class or above).
- The distinction between level 3 and level 4 is: level 3 modifies the existing graph (add/remove/rewire nodes), while level 4 scraps it and starts fresh. Level 3 preserves most of the workflow's structure; level 4 doesn't.
