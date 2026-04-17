# 54-2: Incident Commander Organism

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** not-started
**Goal:** Build the first larger production-shaped organism above the current specialist stack, proving that DAN can investigate, choose actions, execute bounded remediation, verify outcomes, and converge on explicit terminal states for operational incidents.

## Tasks

- [ ] 1. Freeze the exact first proving benchmark
  - [ ] 1-1. Choose 3 bounded incident scenarios (for example: failed scheduled workflow, degraded research artifact, broken coding run or stale run state, stuck browser/download session, or failed external adapter delivery)
  - [ ] 1-2. Define explicit terminal states (`resolved`, `contained`, `blocked`, `escalated`, `needs_approval`)
- [ ] 2. Define the organism-level architecture
  - [ ] 2-1. Triage organ
  - [ ] 2-2. Diagnosis organ (reusing deep research where possible)
  - [ ] 2-3. Action-gate organ
  - [ ] 2-4. Verification organ
  - [ ] 2-5. Final synthesis / operator handoff
  - [ ] 2-6. Reuse the same `supervisor_brief -> worker_report -> review_decision` membrane so incident loops sharpen or stop cleanly instead of spinning
- [ ] 3. Define the action routing boundary
  - [ ] 3-1. Deterministic containment / retry / rollback / pause adapters
  - [ ] 3-2. Optional delegation into the existing coding organism when the chosen action is repair
  - [ ] 3-3. Explicit human-approval / escalation boundaries
- [ ] 4. Add the organism runtime and durable controller seam
  - [ ] 4-1. Add `incident_execution.py`
  - [ ] 4-2. Add `incident_conversation.py`
- [ ] 5. Prove the loop on one exact acceptance harness
  - [ ] 5-1. Investigate -> choose action -> act -> verify -> close
  - [ ] 5-2. Show that the organism stops honestly when approval or human intervention is required

## Decisions

- Incident Commander is not a bigger `dan code`; coding repair is one possible delegated action, not the top-level identity.
- The first version should prefer recommendation + deterministic safe actions before open-ended auto-repair.
- Verification and closure are first-class; the organism should not loop indefinitely.
- Incident Commander should use the same recurrent supervision contract as DAN-v2 rather than inventing a bespoke endless remediation loop.

## Notes

- This is the first proving organism for the broader DAN-v2 rewrite because it naturally composes research, validation, synthesis, and optional coding repair.
- The incident surface should be able to say “do not patch yet” or “rollback / escalate” when that is the right answer, including non-code incidents around browser sessions, adapters, or stuck operator state.
