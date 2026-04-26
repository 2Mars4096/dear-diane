# 56-4: Collapse Organism Families into One

**Parent:** [56-universal-organism-and-cell-restructure](56-universal-organism-and-cell-restructure.md)
**Status:** not-started
**Goal:** Replace the multiple organism execution functions (`coding_execution_organism`, `project_execution_reference_organism`, `super_organism` live lanes, `incident_execution`, `reference_demo`) with one universal organism that handles research, coding, validation, and many-cell fanouts purely by `OrganismPlan` shape.

## Tasks

- [ ] 1. Design the universal organism shape
  - [ ] 1-1. Add `src/dan/worker/organisms/universal_organism.py` with `execute_universal_organism(plan: OrganismPlan, ...) -> OrganismResult`
  - [ ] 1-2. `OrganismPlan` carries: orchestrator brief, worker pool brief template (parameterized per worker), aggregator brief (deterministic-by-default, optional LLM reducer), validator brief, repair-loop policy, capsule/readiness/scheduler hooks
  - [ ] 1-3. The organism reuses existing `parallel_worker_pool`, `execute_organ_pattern`, `assemble_context_packet`, scheduler readiness/admission, capsule bus, repair loop, member-completion callbacks, and speculative validation primitives
  - [ ] 1-4. Surface the same organism-log event vocabulary so `dan organism-log` / `dan-organism-log analyze` and the editor timeline panel keep working unchanged
- [ ] 2. Migrate organism callers
  - [ ] 2-1. `coding_execution_organism(...)` becomes a thin compose-then-call helper that builds an `OrganismPlan` from coding briefs and forwards to `execute_universal_organism(...)`
  - [ ] 2-2. `project_execution_reference_organism(...)` does the same for the project / research reference path
  - [ ] 2-3. `super_organism` live website / coding lanes do the same
  - [ ] 2-4. `incident_execution` execution paths do the same
  - [ ] 2-5. `reference_demo` paths do the same
- [ ] 3. Archive the per-organism execution code paths
  - [ ] 3-1. After all callers route through `execute_universal_organism(...)`, move the migrated organism execution files into `src/dan/worker/organisms/_archive/` so the legacy stage-orchestration code is preserved for trace replay and bug-fix back-porting but is no longer the live execution path
  - [ ] 3-2. Concrete archive list:
    - `src/dan/worker/organisms/coding_execution.py` (DAN Code execution organism, ~3000 lines of scheduler/readiness/repair wiring)
    - `src/dan/worker/organisms/project_execution.py` (DAN Research / project-execution organism, owns `build_research_orchestrator_worker`, `recommended_deep_research_reader_briefs`, `recommended_deep_research_reader_count`, `resolve_deep_research_reader_briefs_for_task` plus the project-execution stage runner)
    - `src/dan/worker/organisms/incident_execution.py` (Incident Commander execution; the deterministic action registry / boundary / verification helpers stay alive but move to a non-organism module if needed)
    - `src/dan/worker/organisms/reference_demo.py` (reference deterministic demo + live runners)
    - `src/dan/worker/organisms/super_organism.py` (the deterministic Super DAN demo + live website / coding execution lanes; the standalone CLI website materializer in `src/dan/cli/super_organism.py` collapses with `56-5`)
  - [ ] 3-3. Public exports in `src/dan/worker/organisms/__init__.py` keep their names but rebind to thin facades over `execute_universal_organism(...)`; this preserves `coding_execution_organism`, `execute_coding_organism`, `project_execution_reference_organism`, `execute_project_execution_organism`, `run_super_organism_demo`, `run_reference_organism_demo/live`, `execute_incident_action`, etc. as backward-compatible shims so external tests, CLI entry points, and adapter code do not break in one big step
  - [ ] 3-4. Reusable typed contracts (`CodingTask`, `ProjectExecutionTask`, `OrganismObservability`, `OrganismStageRecord`, `IncidentBenchmarkScenario`, `IncidentExecutionReport`, `IncidentExecutionRequest`, `IncidentPhaseRecord`, `IncidentVerificationResult`, `IncidentActionBoundary`, `IncidentActionRegistry`, `IncidentActionResult`, `IncidentActionStatus`, `SuperOrganismCell`, `SuperOrganismReport`, `SuperOrganismScenario`, `SuperOrgan`, `BoardSignal`, `ClaimNode`, `DeliveryNode`, `ReallocationDecision`) move to `src/dan/worker/organisms/contracts/` so the universal organism + brief composers can import them without dragging legacy execution code along
  - [ ] 3-5. Keep `coding_conversation`, `research_conversation`, `incident_conversation`, `dan_conversation` outside the archive — they are durable conversation controllers (multi-turn session state, mailbox semantics, clarification handling), not organisms, and they remain the public surface for the CLI shells
- [ ] 4. Lock the unified shape with regressions
  - [ ] 4-1. The existing `tests/test_worker/test_coding_organism.py` basket still passes through the universal organism path
  - [ ] 4-2. The reference organism, super-organism live, incident-execution, and project-execution baskets still pass through the universal organism path
  - [ ] 4-3. Add a single new test that proves the same `execute_universal_organism(...)` runs both a coding-shaped plan and a research-shaped plan with no code changes between the two

## Decisions

- One organism, multiple plan shapes. The organism is the engine; the plan is the program.
- Orchestrator / aggregator / validator stages are parameterized organs; presence, absence, and composition is set by the plan, not by hardcoding per family.
- Repair loop is part of the organism, not per-product. Repair brief composition uses the same `56-2` template library as initial briefs.
- Durable conversation controllers (`*_conversation.py`) stay separate because they own multi-turn session state, mailbox semantics, and clarification handling — that is not organism execution.
- Capsule bus, scheduler readiness, speculative validation, member-completion callbacks all stay where they are; the universal organism just stops re-implementing the wiring once per family.
- Backward-compatible facades (`coding_execution_organism`, `execute_coding_organism`, `project_execution_reference_organism`, etc.) stay as thin shims so external callers do not break, but their bodies become small.
- Archive instead of delete. The migrated execution files move under `src/dan/worker/organisms/_archive/` so the historical stage-orchestration logic stays diffable for trace replay and back-port while it is no longer the live execution path. After two release cycles with no archive imports detected, the archive folder is a candidate for removal.
- Reusable typed contracts split out of the archived files into `worker/organisms/contracts/` so the universal organism never imports from the archive at runtime.

## Notes

- The pain this addresses: today, scheduler readiness wiring, capsule emission, member-completion callbacks, speculative validation, and repair-loop construction are partially re-implemented across `coding_execution.py`, `project_execution.py`, `incident_execution.py`, and the `super_organism` live lanes. Bug fixes have to land in N places.
- After 56-4, those wirings live once. Adding a new product is a new orchestrator + brief composer + plan shape, not a new organism implementation.
- This is the highest-risk slice in the `56-*` stack: `coding_execution.py` is ~3000 lines of scheduler / readiness / repair logic that is the de-facto reference. Migration must preserve its behavior and emergent invariants while moving the implementation onto the shared substrate.
- Suggested migration order: `reference_demo` first (simplest), then `super_organism` live lanes, then `incident_execution`, then `project_execution`, then `coding_execution` last. Land each migration behind an env flag (`DAN_UNIVERSAL_ORGANISM`) so rollbacks are cheap.
- The `super_organism` live lane already shares much of its validator wiring with DAN Code through the shared local runtime. The migration should make that sharing explicit by routing both through the same `OrganismPlan` shape.
