# 54-2: Incident Commander Organism

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Build the first larger production-shaped organism above the current specialist stack, proving that DAN can investigate, choose actions, execute bounded remediation, verify outcomes, and converge on explicit terminal states for operational incidents.

## Tasks

- [x] 1. Freeze the exact first proving benchmark
  - [x] 1-1. Choose 3 bounded incident scenarios (failed scheduled workflow, broken coding run / stale run state, failed external surface session)
  - [x] 1-2. Define explicit terminal states (`resolved`, `contained`, `blocked`, `escalated`, `needs_approval`) plus transient `open` while bounded work is still underway
- [x] 2. Define the organism-level architecture
  - [x] 2-1. Triage organ
  - [x] 2-2. Diagnosis organ (reusing deep research where possible)
  - [x] 2-3. Action-gate organ
  - [x] 2-4. Verification organ
  - [x] 2-5. Final synthesis / operator handoff
  - [x] 2-6. Reuse the same `supervisor_brief -> worker_report -> review_decision` membrane so incident loops sharpen or stop cleanly instead of spinning
- [x] 3. Define the action routing boundary
  - [x] 3-1. Deterministic containment / retry / rollback / pause adapters
  - [x] 3-2. Optional delegation into the existing coding organism when the chosen action is repair
  - [x] 3-3. Explicit human-approval / escalation boundaries
- [x] 4. Add the organism runtime and durable controller seam
  - [x] 4-1. Add `incident_execution.py`
  - [x] 4-2. Add `incident_conversation.py`
- [x] 5. Prove the loop on one exact acceptance harness
  - [x] 5-1. Investigate -> choose action -> act -> verify -> close
  - [x] 5-2. Show that the organism stops honestly when approval or human intervention is required

## Decisions

- Incident Commander is not a bigger `dan code`; coding repair is one possible delegated action, not the top-level identity.
- The first version should prefer recommendation + deterministic safe actions before open-ended auto-repair.
- Verification and closure are first-class; the organism should not loop indefinitely.
- Incident Commander should use the same recurrent supervision contract as DAN-v2 rather than inventing a bespoke endless remediation loop.

## Notes

- This is the first proving organism for the broader DAN-v2 rewrite because it naturally composes research, validation, synthesis, and optional coding repair.
- The incident surface should be able to say “do not patch yet” or “rollback / escalate” when that is the right answer, including non-code incidents around browser sessions, adapters, or stuck operator state.
- Landed first implementation slice: `incident_execution.py` freezes three incident scenarios plus action/approval boundaries, `incident_conversation.py` adds a durable Incident Commander controller, and DAN-v2 can now route `selected_lane=incident` into that controller while persisting the incident session in chat-thread control-plane metadata.
- Landed second implementation slice: `incident_execution.py` now includes deterministic incident execution requests/reports, an action-adapter registry, approval gating, verification, and a five-phase `investigate -> action_gate -> act -> verify -> close` trace for investigate/retry/contain/pause/rollback/repair/escalate flows.
- Landed third implementation slice: `DANV2Runtime` can now invoke that deterministic incident execution seam when a surface passes explicit structured `surface_context["incident_execution"]` data, so Incident Commander can close honest resolved/contained/blocked/needs_approval incident turns directly without falling through to the legacy substrate.
- Landed fourth implementation slice: incident `repair` actions now recurse into the existing DAN Code controller path inside `DANV2Runtime`, so Incident Commander can shape a bounded repair through code-specific objective/acceptance contracts before the shared execution substrate runs it.
- Landed fifth implementation slice: `DANV2Runtime` now accepts optional server `RunManager` context and can synthesize read-only failed-workflow investigation evidence from live run snapshots when no explicit `surface_context["incident_execution"]` payload is present, letting Incident Commander stop honestly on blocked/resolved scheduled-workflow investigations without hand-built surface metadata.
- Landed sixth implementation slice: failed scheduled workflow `contain` / `pause` actions now have the first real live side-effect adapter. `DANV2Runtime` can select the active run from `RunManager.list_runs()`, call `RunManager.cancel_run(...)`, and still emit the same five-phase incident report shape with `incident_execution_mode=live`.
- Landed seventh implementation slice: failed scheduled workflow `retry` actions now use a replay-safe live path. `RunManager` persists a replayable `launch_request` for direct `start_run(...)` launches, exposes `retry_run(...)` to reload the workflow graph and replay that launch contract, and `DANV2Runtime` can use that seam so Incident Commander stops honestly at `terminal_state=open` while the retried run settles instead of pretending the retry already resolved the incident.
- Landed eighth implementation slice: live workflow retry is now broader than fresh direct launches. `RunManager` now persists `goal_context` plus replay contracts for `resume_run(...)` and `rerun_from_checkpoint(...)`, can infer a best-effort direct retry for older persisted runs from `effective_run_policy`, and can reconstruct older checkpoint reruns from persisted `rerun_started` provenance events before `DANV2Runtime` asks for a live retry.
- Landed ninth implementation slice: DAN-v2 can now execute the dedicated coding organism directly inside `src/dan/server/control_plane.py` instead of only shaping a legacy handoff. The direct path reuses the same universal-agent coding organism blocks (`LocalOrganismToolRuntime` + `ToolLoopCompletionProvider` + `WorkerCoreExecutor` + `coding_execution_organism(...)`), covers both top-level code turns and nested Incident Commander `repair` turns, and still falls back to the old handoff path if the direct runtime raises unexpectedly.
- Landed tenth implementation slice: failed external-surface incidents are now live, not only deterministic. `DANV2Runtime` can infer read-only adapter/session evidence from the adapter router status surface, stop live failed adapters for `contain` / `pause`, and restart persisted `telegram`, `wechat`, or `whatsapp-web` surfaces for honest live `retry` while preserving the same five-phase incident report contract.
- Landed eleventh implementation slice: the direct DAN Code runtime is now the default path inside DAN-v2 rather than an opt-in migration branch. Surfaces can still disable it explicitly with `surface_context["direct_code_execution"]=false` or `DAN_V2_DIRECT_CODE_RUNTIME=0`, which keeps the old handoff path available for A/B testing and rollback while Incident Commander still prefers direct bounded repair by default.
- 2026-04-30 maintenance fix: Incident Commander worker metadata now derives terminal states from `typing.get_args(IncidentTerminalState)` because the terminal-state contract is a `Literal`, not an enum. This fixes the DAN-v2 startup failure that surfaced in Telegram as `An error occurred: value`.
- Residual limits after plan completion: older resume-only workflow retries still need an explicit persisted replay contract for true resume semantics, live adapter retry currently depends on persisted config for `telegram` / `wechat` / `whatsapp-web`, and broader surface migration still belongs to `54-3` / `54-4` rather than this first proving organism.
