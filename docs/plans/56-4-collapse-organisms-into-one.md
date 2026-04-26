# 56-4: Collapse Organism Families into One

**Parent:** [56-universal-organism-and-cell-restructure](56-universal-organism-and-cell-restructure.md)
**Status:** not-started
**Goal:** Replace the multiple organism execution functions (`coding_execution_organism`, `project_execution_reference_organism`, `super_organism` live lanes, `incident_execution`, `reference_demo`) with one universal organism that handles research, coding, validation, and many-cell fanouts purely by `OrganismPlan` shape.

## Tasks

- [ ] 1. Design the universal organism shape
  - [ ] 1-1. Add `src/dan/worker/organisms/universal_organism.py` with `execute_universal_organism(plan: OrganismPlan, ...) -> OrganismResult`
  - [ ] 1-2. `OrganismPlan` is a flexible role/task DAG rather than a fixed orchestrator-worker-aggregator-validator pipeline. It carries orchestrator-discovered `RoleSpec` nodes, `WorkerBrief` templates, optional reducer/review/audit roles, repair-loop policy, runtime budget policy, capsule/readiness/scheduler hooks, and task-specific artifact/acceptance policy data.
  - [ ] 1-3. Reuse the early dependency substrate explicitly: Phase 1 graph edges/fan-out/fan-in/checkpointing, Plan 37 eager ready-queue dispatch / completion-driven successor release / shared concurrency budgeting, and Plan 55 agent-task DAG semantics.
  - [ ] 1-4. `OrganismPlan` dependencies support hard, soft, suspected, and disproven edges; readiness predicates; artifact-kind and unlock-key requirements; parent workflow-stage boundaries; and dynamic dependency revisions from evidence/capsule updates.
  - [ ] 1-5. The organism reuses existing `parallel_worker_pool`, `execute_organ_pattern`, `assemble_context_packet`, scheduler readiness/admission, capsule bus, repair loop, member-completion callbacks, and speculative validation primitives
  - [ ] 1-6. Add first-class critical-path diagnostics to every run: critical path lower bound, total remaining work divided by capacity, straggler pressure, terminal barrier tails, and missed-parallelism candidates.
  - [ ] 1-7. Surface a full-fidelity organism event log so `dan organism-log` / `dan-organism-log analyze` and the editor timeline panel keep working while gaining policy provenance and dependency visibility
  - [ ] 1-8. Add the meta-orchestrator runtime loop: typed event bus, append-only event sink, deterministic run-state reducer, critical-path ready queue, command queue, semantic observer queue, decision ledger, and admission gate for LLM-proposed plan/policy/repair actions
  - [ ] 1-9. Add high-frequency status support for step-loop workers: runtime heartbeat, semantic heartbeat, stale-worker detection, cancellation tokens, material-event detection, and compact per-worker state tables
  - [ ] 1-10. Treat LLM observer/reviewer/orchestrator calls as scheduled async jobs. Status events never automatically call the LLM; trigger policies enqueue compact snapshots for semantic review, and deterministic gates decide whether proposed actions are admitted.
  - [ ] 1-11. Add queue policy settings that are easy to tune per run: concurrency, priority, retry budget, stale timeout, coalescing cadence, drop/merge behavior for noncritical semantic/UI updates, and dead-letter handling. Lossless handling is reserved for full event logs, safety/budget events, and admitted commands.
- [ ] 2. Archive the per-organism execution code paths before rebuilding live behavior
  - [ ] 2-1. Move the legacy organism execution files into `src/dan/worker/organisms/_archive/` at the start of the rebuild so the old stage-orchestration logic is preserved for reference/replay but is not treated as the implementation to incrementally mutate
  - [ ] 2-2. Concrete archive list:
    - `src/dan/worker/organisms/coding_execution.py` (DAN Code execution organism, ~3000 lines of scheduler/readiness/repair wiring)
    - `src/dan/worker/organisms/project_execution.py` (DAN Research / project-execution organism, owns `build_research_orchestrator_worker`, `recommended_deep_research_reader_briefs`, `recommended_deep_research_reader_count`, `resolve_deep_research_reader_briefs_for_task` plus the project-execution stage runner)
    - `src/dan/worker/organisms/incident_execution.py` (Incident Commander execution; the deterministic action registry / boundary / verification helpers stay alive but move to a non-organism module if needed)
    - `src/dan/worker/organisms/reference_demo.py` (reference deterministic demo + live runners)
    - `src/dan/worker/organisms/super_organism.py` (the deterministic Super DAN demo + live website / coding execution lanes; the standalone CLI website materializer in `src/dan/cli/super_organism.py` collapses with `56-5`)
  - [ ] 2-3. Public exports in `src/dan/worker/organisms/__init__.py` keep their names but rebind to thin compatibility facades over the new universal-agent infrastructure; this preserves `coding_execution_organism`, `execute_coding_organism`, `project_execution_reference_organism`, `execute_project_execution_organism`, `run_super_organism_demo`, `run_reference_organism_demo/live`, `execute_incident_action`, etc. without importing runtime code from `_archive`
  - [ ] 2-4. Reusable typed contracts (`CodingTask`, `ProjectExecutionTask`, `OrganismObservability`, `OrganismStageRecord`, `IncidentBenchmarkScenario`, `IncidentExecutionReport`, `IncidentExecutionRequest`, `IncidentPhaseRecord`, `IncidentVerificationResult`, `IncidentActionBoundary`, `IncidentActionRegistry`, `IncidentActionResult`, `IncidentActionStatus`, `SuperOrganismCell`, `SuperOrganismReport`, `SuperOrganismScenario`, `SuperOrgan`, `BoardSignal`, `ClaimNode`, `DeliveryNode`, `ReallocationDecision`) move to `src/dan/worker/organisms/contracts/` so the universal organism + brief composers can import them without dragging legacy execution code along
  - [ ] 2-5. Keep `coding_conversation`, `research_conversation`, `incident_conversation`, `dan_conversation` outside the archive — they are durable conversation controllers (multi-turn session state, mailbox semantics, clarification handling), not organisms, and they remain the public surface for the CLI shells
- [ ] 3. Rebuild organism callers on the universal-agent infrastructure
  - [ ] 3-1. `coding_execution_organism(...)` becomes a compose-then-call compatibility helper that builds an `OrganismPlan` from coding briefs and forwards to `execute_universal_organism(...)`
  - [ ] 3-2. `project_execution_reference_organism(...)` does the same for the project / research reference path
  - [ ] 3-3. `super_organism` live website / coding lanes do the same
  - [ ] 3-4. `incident_execution` execution paths do the same
  - [ ] 3-5. `reference_demo` paths do the same
- [ ] 4. Add full event logging and policy provenance
  - [ ] 4-1. Log the rendered `OrganismPlan` snapshot, dependency graph, stage/task state transitions, ready-queue decisions, policy profiles, policy overrides, prompt slot values, selected tool policy, sampling policy, runtime budget policy, validation policy, repair policy, and artifact policy
  - [ ] 4-2. Log scheduler proposals, admissions, rejections, capacity decisions, dependency blockers/unlocks, capsule readiness events, downstream starts, incremental reducer output, speculative validation start/reuse/discard, and final promotion decisions
  - [ ] 4-3. Preserve raw evidence through raw refs or bounded sidecar artifacts when payloads are large, while keeping event rows sufficient for deterministic replay and debugging
  - [ ] 4-4. Redact provider credentials and local secrets, but do not collapse policy/prompt/dependency decisions into opaque summaries
  - [ ] 4-5. Log heartbeat and semantic-status events separately: liveness/elapsed/budget/tool state for runtime heartbeat; current focus, last material change, next action, blockers, confidence, risk flags, and artifact refs for semantic heartbeat
  - [ ] 4-6. Log every semantic decision proposal in the decision ledger with source (`compiled_policy`, `worker_self_status`, `observer_llm`, `reviewer_llm`, `orchestrator_llm`), trigger event ids, compact snapshot refs, proposed action, admission result, rejection reason, TTL, reversibility, and admitted command id
  - [ ] 4-7. Log idempotency keys and state versions for admitted commands so retries are safe under at-least-once delivery
- [ ] 5. Lock the unified shape with regressions
  - [ ] 5-1. The existing `tests/test_worker/test_coding_organism.py` basket still passes through the universal organism path
  - [ ] 5-2. The reference organism, super-organism live, incident-execution, and project-execution baskets still pass through the universal organism path
  - [ ] 5-3. Add a single new test that proves the same `execute_universal_organism(...)` runs both a coding-shaped plan and a research-shaped plan with no code changes between the two
  - [ ] 5-4. Add a dependency/critical-path regression proving completion-driven unlocks can start downstream work before unrelated sibling tasks finish, matching the Plan 37 ready-queue behavior
  - [ ] 5-5. Add a four-worker fanout regression proving all ready workers dispatch concurrently up to policy limits, progress events update run state without additional orchestrator LLM calls, and downstream reducers/reviewers start from completion/capsule unlocks
  - [ ] 5-6. Add a semantic-observer regression proving high-frequency status events are coalesced into compact snapshots, observer/reviewer LLM calls are batched or cadence-triggered, and proposed plan mutations are blocked unless the deterministic admission gate accepts them

## Decisions

- One organism, multiple plan shapes. The organism is the engine; the plan is the program.
- Roles are plan data. The orchestrator may create builder, researcher, reducer, reviewer, auditor, scheduler, synthesis, operator-communication, or any other role needed by the task, and may add/split/merge/cancel those roles as evidence changes.
- Repair loop infrastructure is part of the organism, but repair count, retry shape, fallback criteria, and stop policy are brief/plan policy data rather than fixed organism constants.
- Dependency management is not a new invention in `56-*`; the universal organism inherits the existing graph/ready-queue/task-DAG lineage from Plans 2, 37, and 55.
- Durable conversation controllers (`*_conversation.py`) stay separate because they own multi-turn session state, mailbox semantics, and clarification handling — that is not organism execution.
- Capsule bus, scheduler readiness, speculative validation, member-completion callbacks all stay where they are; the universal organism just stops re-implementing the wiring once per family.
- The meta-orchestrator is a hybrid controller: deterministic high-frequency control plus async LLM proposal lanes. It may exploit frequent semantic observer calls, but those calls produce candidate decisions, not direct state mutations.
- Status update frequency and LLM decision frequency are separate controls. Raw runtime events can be many per second; UI/state deltas can be throttled; semantic observer calls can be batched/cadenced; full replan calls remain trigger-based.
- The meta-orchestrator/admission gate makes the final runtime decision. LLM observers, reviewers, and workers can propose actions, but proposed decisions are ledger entries until deterministic admission turns them into commands.
- Keep critical-path scheduling primary. Do not add a standalone tool/operation queue in this plan unless evidence shows a bottleneck; use the ready queue, dependency blockers, artifact ownership policy, and tool-boundary admission to coordinate parallel agents.
- Queue settings should degrade noncritical work rather than stall the run. Under load, coalesce UI deltas and merge/drop stale semantic snapshots before blocking dispatch of already-admitted critical-path work.
- Commands use idempotency keys and state versions. Assume at-least-once delivery for internal queues and make retries safe through command dedupe, leases where needed, and replay from the event log.
- Backward-compatible facades (`coding_execution_organism`, `execute_coding_organism`, `project_execution_reference_organism`, etc.) stay as thin shims so external callers do not break, but their bodies become small.
- Archive instead of delete, but archive at the beginning of the rebuild rather than after a long incremental mutation. The old organism execution files move under `src/dan/worker/organisms/_archive/` so historical stage-orchestration logic stays diffable for trace replay/back-port while live behavior is rebuilt on the universal-agent infrastructure.
- Reusable typed contracts split out of the archived files into `worker/organisms/contracts/` so the universal organism never imports from the archive at runtime.
- Full event logging is a runtime contract, not optional observability. Universal organism runs must be explainable from logs: what plan was selected, which policies were active, why tasks were blocked/unblocked, what prompts were rendered, what tools ran, what artifacts became ready, why reducers/validators accepted or rejected, and why final delivery stopped.
- Agent-to-agent transfer uses typed handoff/control-plane contracts first and files only for large raw material. Live data moves as `RoleSpec`, `WorkerBrief`, `OrganismPlan`, `ContextPacket`, evidence refs, artifact refs, policy objects, and output contracts; large raw files, diffs, reports, or transcripts are referenced by `RawRef` / artifact locators and loaded only when needed.
- Heartbeats are the bridge between step-loop workers and async supervision. Runtime heartbeat handles liveness/timeouts/cancel safety; semantic heartbeat handles "what I am doing, what changed, what I will do next, and what I am worried about" for high-frequency semantic awareness.
- Semantic observers consume compact snapshots/projections, not the raw log. The full event log remains the source of truth for replay/debugging; snapshots are bounded inputs for LLM calls.

## Notes

- The pain this addresses: today, scheduler readiness wiring, capsule emission, member-completion callbacks, speculative validation, and repair-loop construction are partially re-implemented across `coding_execution.py`, `project_execution.py`, `incident_execution.py`, and the `super_organism` live lanes. Bug fixes have to land in N places.
- After 56-4, those wirings live once. Adding a new product is a new orchestrator + brief composer + plan shape, not a new organism implementation.
- This is the highest-risk slice in the `56-*` stack: `coding_execution.py` is ~3000 lines of scheduler / readiness / repair logic that is the de-facto reference. Migration must preserve its behavior and emergent invariants while moving the implementation onto the shared substrate.
- Rebuild order after archiving: contracts/policy/prompt renderer first, universal organism DAG runner second, then compatibility facades for `reference_demo`, `super_organism` live lanes, `incident_execution`, `project_execution`, and `coding_execution`. This is a full rebuild upon the universal-agent infra, not a gradual edit of the legacy organism implementations.
- The `super_organism` live lane already shares much of its validator wiring with DAN Code through the shared local runtime. The migration should make that sharing explicit by routing both through the same `OrganismPlan` shape.
- A single long provider call cannot provide meaningful high-frequency semantic control beyond token streaming. Universal cells that need supervision should run as step-loop workers with heartbeat/tool/artifact boundaries where the meta-orchestrator can continue, redirect, pause, cancel, or launch observers.
