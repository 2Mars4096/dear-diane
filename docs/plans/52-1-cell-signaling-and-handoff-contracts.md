# 52-1: Cell Signaling And Handoff Contracts

**Parent:** [52-multicellular-composition](52-multicellular-composition.md)
**Status:** completed
**Goal:** Define the direct handoff and broadcast/event contracts that let independent cells coordinate without sharing one giant implicit prompt.

## Tasks
- [x] 1. Define direct handoff packets
  - [x] 1-1. Include task, evidence refs, output contract, budget/authority limits, and continuation hooks
  - [x] 1-2. Keep the handoff shape compact enough that cells can talk through refs and summaries before expanding detail
- [x] 2. Define broadcast/event signals
  - [x] 2-1. Status
  - [x] 2-2. Warning/failure
  - [x] 2-3. Budget pressure
  - [x] 2-4. Completion/escalation
- [x] 3. Separate cell-local and organism-level concerns
  - [x] 3-1. Keep point-to-point task handoff distinct from broader supervisory/event signals
- [x] 4. Add observability and traceability
  - [x] 4-1. Make every cross-cell handoff inspectable after the fact
- [x] 5. Validate on a small two- or three-cell coordination slice
  - [x] 5-1. Prove that cells can coordinate through contracts instead of hidden shared prompt state

## Decisions
- Cell communication should be explicit, typed, and inspectable.
- Handoffs should prefer refs and compact packets over wholesale prompt dumping.
- The direct packet surface is now a dedicated `CellHandoffPacket` contract that normalizes into the existing `ExecutionRequest` membrane instead of inventing a second implicit prompt path.
- Supervisory broadcasts stay structurally separate from point-to-point work packets through typed signal variants (`status`, `warning`, `failure`, `budget_pressure`, `completed`, `escalated`) plus a trace log that records both without flattening them together.

## Notes
- This is the first true multicellular boundary. If it is weak, everything above it will be mush.
- 2026-04-08: Landed `src/dan/worker/signaling.py` with typed `CellHandoffPacket`, compact `EvidenceRef` pointers, explicit budget/authority envelopes, continuation hooks, and typed supervisory signal variants. Added `src/dan/worker/composition.py` with a traceable append-only `CrossCellTraceLog`, signal builders, and a thin `execute_cell_handoff(...)` bridge that runs a packet through `WorkerCoreExecutor` without collapsing it back into one giant prompt blob.
- 2026-04-08 follow-up: continuation hooks now normalize into a first-class `CommunicationContract` on `ExecutionRequest`, while packet normalization also threads allowed memory write scopes and max tool calls into the same request membrane. Cells now receive reply/status/completion/escalation addresses as typed channels instead of parsing them back out of metadata.
- 2026-04-08: Added focused regressions in `tests/test_worker/test_model.py` covering packet-to-request normalization, trace inspection across direct handoffs vs supervisory broadcasts, and a small planner -> researcher -> writer coordination slice that passes typed refs/signals end to end.
- Focused proof command: `PYTHONPATH=src pytest -q tests/test_worker/test_model.py -k "cell_handoff_packet or trace_log_keeps_handoffs or three_cell_slice"`
