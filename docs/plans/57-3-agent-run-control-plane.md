# 57-3: Agent Run Control Plane

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** not-started
**Goal:** Add a durable Agent run plane that can launch Super-DAN-style task execution from the backend and stream task progress to V2.

## Tasks
- [ ] 1. Define Agent run API contracts
  - [ ] 1-1. `POST /api/agent-runs` creates a run from objective, profile, workspace, mutation permission, and surface context
  - [ ] 1-2. `GET /api/agent-runs/{run_id}` returns current status and final report
  - [ ] 1-3. `WS /api/agent-runs/{run_id}/events` streams task progress and terminal events
  - [ ] 1-4. `POST /api/agent-runs/{run_id}/stop` cancels or interrupts work
  - [ ] 1-5. `POST /api/agent-runs/{run_id}/retry` starts a bounded retry from the last blocker
- [ ] 2. Bridge Super DAN as the first Agent backend
  - [ ] 2-1. Extract a callable backend wrapper around the existing Super DAN live runner without requiring CLI argument parsing
  - [ ] 2-2. Preserve Super DAN event rows and `.dan-super/runs/.../events.jsonl` traces
  - [ ] 2-3. Map Super DAN terminal states into the generic Agent run status model
  - [ ] 2-4. Keep CLI `dan super-organism` behavior unchanged
- [ ] 3. Define Agent run storage
  - [ ] 3-1. Persist run metadata, profile, workspace, objective, selected backend, event-log path, status, and final summary
  - [ ] 3-2. Link Agent runs back into chat threads via `task_run_ref`
  - [ ] 3-3. Support reconnect after frontend reload
- [ ] 4. Add safety and approval boundaries
  - [ ] 4-1. Thread workspace root, approval mode, mutation permission, and available tools into the Agent run
  - [ ] 4-2. Preserve read-only vs mutation vs external-side-effect envelopes
  - [ ] 4-3. Make stop/cancel behavior deterministic and observable
- [ ] 5. Validate with narrow end-to-end tests
  - [ ] 5-1. Fake-provider Agent run completes and streams progress
  - [ ] 5-2. Stop/cancel closes streams and persists interrupted state
  - [ ] 5-3. Super DAN CLI still works after backend extraction

## Decisions
- The first Agent backend should be Super DAN because it already has the best live execution UX.
- The generic Agent run contract must not expose Super-DAN-specific terms as required frontend concepts.
- A backend wrapper is preferred over shelling out to the CLI.

## Notes
- Server-side organism-log discovery already sees `.dan-super/runs/**/events.jsonl`; this should be reused for V2 task timelines instead of inventing a second trace format.
