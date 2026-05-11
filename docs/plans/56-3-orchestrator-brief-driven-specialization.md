# 56-3: Orchestrator Brief-Driven Specialization

**Parent:** [56-universal-organism-and-cell-restructure](56-universal-organism-and-cell-restructure.md)
**Status:** in-progress
**Goal:** Migrate every place that previously specialized a cell by building a custom `WorkerDefinition` to instead let the orchestrator discover roles and emit `RoleSpec` / `WorkerBrief` data composed from the `56-2` template library, so 100% of task-specific behavior lives in declarative brief data rendered through a fixed prompt architecture instead of imperative builder code.

## Tasks

- [x] 1. Define the brief contract
  - [x] 1-1. Add `src/dan/worker/brief.py` with typed `RoleSpec` and `WorkerBrief`; `RoleSpec` carries `role_label`, `responsibility`, `success_criteria`, `dependencies`, `artifact_targets`, `collaboration_contract`, and `trace_role`, while `WorkerBrief` carries `task`, `scope`, `hard_constraints`, `soft_constraints`, `tool_policy`, `runtime_policy`, `validation_policy`, `lifecycle_policy`, `mailbox_policy`, `prompt_context`, `prompt_slots`, `contract_snippets`, `fail_predicates`, `recovery_hints`, `output_contract`, `sampling_policy`, `heartbeat_policy`, `semantic_status_contract`, `decision_trigger_policy`, `evidence`, `context_packet`, `metadata`
  - [x] 1-2. Add `request_from_brief(brief) -> ExecutionRequest` to convert briefs into the existing `WorkerCoreExecutor` input
  - [x] 1-3. Add a prompt renderer that turns `RoleSpec` + `WorkerBrief` + policy objects into the fixed prompt architecture; callers may not bypass it with complete bespoke prompts
  - [x] 1-4. Add typed status/decision contracts: `HeartbeatPolicy` (runtime cadence, semantic cadence, stale timeout, material-event triggers), `SemanticStatusContract` (current focus, last material change, next intended action, blocker, confidence, risk flags, artifact refs), and `DecisionTriggerPolicy` (conditions for async observer/reviewer/replan calls)
  - [x] 1-5. Add `AgentLifecyclePolicy` and `MailboxPolicy` in the contract layer, reusing existing spawn/capability ceilings where possible: max spawned cells, max depth, idle timeout, stale heartbeat timeout, close/resume behavior, cancel propagation, note-vs-followup semantics, and wake/no-wake message handling
  - [x] 1-6. Confirm `OutputContract` from `46-7` accommodates brief-supplied `expected_return_shape` + JSON-Schema backstop without changes
- [ ] 2. Migrate orchestrators to discover roles and emit briefs
  - [ ] 2-1. `coding_execution_organism` emits a task-specific role graph (`RoleSpec` + `WorkerBrief`) instead of assuming orchestrator / worker / aggregator / validator stages
  - [x] 2-2. `super_organism` live lanes emit task-specific roles, with website-specificity (required files, anti-template phrases, preferred tools) living entirely in role/brief policy data; broad live tasks can now add optional planner and plan-validator briefs before the builder while narrow edits still go straight to builder
  - [ ] 2-3. `project_execution` and research-reader paths emit task-specific research/review/synthesis roles only when the orchestrator decides they are needed
  - [ ] 2-4. `incident_execution` paths emit task-specific response/verification/communication roles from the incident brief, not from fixed organism code
- [x] 3. Validators stop being a code concept
  - [x] 3-1. Replace `_build_live_*_validator(...)` functions with orchestrator-discovered review/audit roles composed through `templates.review_brief(...)` or equivalent role profiles
  - [x] 3-2. Replace role-string read-only inference with explicit brief policies (`tool_policy`, `validation_policy`, `output_contract`, `runtime_policy`) carried by the brief, keeping legacy classifier fallbacks only during migration
  - [x] 3-3. Confirm reviewer/auditor sampling is selected by `sampling_policy` from the brief, usually but not universally the deterministic baseline
- [ ] 4. Lock the migration with regressions
  - [ ] 4-1. Add a regression that runs the same task through (a) the legacy per-variant builder path and (b) the new brief-driven path and asserts equivalent organism-log event sequences for representative coding, validation, and research-reader cases
  - [ ] 4-2. After legacy paths are removed in `56-5`, the regression collapses to brief-driven only
  - [x] 4-3. Add an additive compatibility regression proving legacy coding/project/incident/Super DAN task-shaped inputs compose brief-driven `OrganismPlan` data and execute through `execute_universal_organism(...)`

## Decisions

- `RoleSpec` + `WorkerBrief` are the only specialization channel between orchestrator and cell. Not `WorkerDefinition` fields, not custom system prompts, not custom instruction strings, and not hidden infra-level caps.
- A brief is always composable from snippets + task-specific prompt slot text. Orchestrators may use helper profiles (e.g. coding, review, research), but those helpers do not create fixed roles; they only fill slots for roles the orchestrator selected.
- The `OutputContract` substrate from `46-7` is preserved unchanged; the brief just builds it more uniformly.
- Brief construction is the new copy-paste risk surface. Mitigation: keep snippet vocabulary small and orthogonal so common patterns do not need new templates.
- The prompt renderer owns fixed architecture and section order; briefs own task-specific slot content. No caller should bypass the renderer by concatenating bespoke complete prompts.
- Avoid a rigid `execution_mode` enum as a behavior switch. If a lightweight `stage_label` / `trace_role` exists, it is observability only. Runtime behavior follows the actual policies: which tools are allowed/preferred/forbidden, whether mutation is allowed, what output contract is required, how validation is scored, what evidence must be inspected, and what budget profile applies.
- Agent-to-agent prompt pass-through is brief-first, not prompt-string-first. Upstream agents may propose roles and prompt slot content, but downstream prompts are re-rendered from typed `RoleSpec`, `WorkerBrief`, policies, `ContextPacket`, and evidence/artifact refs at the recipient boundary.
- Worker status is also contract-first. Step-loop workers emit runtime heartbeats and compact semantic self-status at boundaries; the meta-orchestrator reduces those events locally and only schedules LLM observer/reviewer calls when `decision_trigger_policy` admits them.
- Lifecycle belongs in contracts rather than ad-hoc queues. Existing spawn/cap limits remain the enforcement substrate, with the new lifecycle policy making idle close, max depth, stale heartbeat handling, resume, and descendant cancellation explicit.
- Mailbox semantics must distinguish passive notes from wakeful follow-up tasks. A status/comment/message can be recorded without triggering another model turn; only admitted follow-up commands wake the worker.

## Notes

- 2026-04-26: brief contracts and renderer are now available and wired through `request_from_brief(...)` into the existing `WorkerCoreExecutor`. Product orchestrator migration is still open; legacy code paths remain live until compatibility facades are built.
- 2026-05-11: Super DAN live brief composition now injects shared, extendable stage snippets for organism self-awareness, workspace boundary policy, tool descriptions, and motivating decision questions across builder, builder retry, validator, and repair roles. The pacing contract stays in contract snippets instead of being repeated inside task text.
- 2026-05-11 planner slice: Super DAN live now has optional planner and plan-validator brief shapes. The planner creates run-local numeric plan files only for broad objectives, the plan validator audits coherence before execution, and the builder/repair/validator briefs receive a compact `plan_context` so completed checkboxes can be ticked and audited without making planning mandatory.
- 2026-04-26 follow-up: Super DAN live website/generic lanes now compose `coding_brief(...)` and `review_brief(...)` first, derive universal cells from those briefs, and source website files/tool ids/anti-template phrases from `select_orchestrator(...)` policy data instead of `_build_live_*` worker/validator builders or CLI globals.
- 2026-04-26 facade follow-up: `src/dan/worker/organisms/legacy_facades.py` now provides additive brief-driven plan composers for coding, project/research, incident, reference-demo, and Super DAN task shapes. These do not yet replace the legacy execution functions, but they establish the compose-then-run compatibility surface for the remaining product rewiring.
- This is where the duplicated `cli/super_organism.py` builders actually disappear. After 56-3, those ~70-line `_build_live_*_worker` / `_build_live_*_validator` functions become 5–15 line brief composers.
- Brief composers can be unit-tested as pure data factories — no LLM, no runtime, no fixtures — which is a real reliability win over today's mostly-untested cell builders.
- `WorkerBrief` is a control-plane object, not a raw prompt file. It should be logged as structured JSON for replay/debugging, but the live handoff should pass contracts and refs, not opaque full prompt text.
- Important migration discipline: do not invent a parallel runtime. `WorkerCoreExecutor` continues to be the only execution path; `request_from_brief(...)` is just a thin adapter.
- The semantic status contract should be small enough to emit frequently. It is a worker-authored status payload, not a full progress essay and not a second hidden prompt channel.
