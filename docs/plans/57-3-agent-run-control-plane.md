# 57-3: Agent Run Control Plane

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** in-progress
**Goal:** Add a durable Agent run plane that can launch Super-DAN-style task execution from the backend and stream task progress to V2.

## Tasks
- [ ] 1. Define Agent run API contracts
  - [x] 1-1. `POST /api/v2/agent-runs` creates a run from objective, profile, workspace, mutation permission, and surface context
  - [x] 1-2. `GET /api/v2/agent-runs/{run_id}` returns current status and final report
  - [x] 1-3. `WS /api/v2/agent-runs/{run_id}/events` streams task progress and terminal events
  - [x] 1-4. `POST /api/v2/agent-runs/{run_id}/commands` records stop/cancel commands
  - [x] 1-5. `POST /api/v2/agent-runs/{run_id}/commands` records bounded retry commands
  - [x] 1-6. `POST /api/v2/agent-runs/{run_id}/commands` records append follow-up commands
  - [x] 1-7. `POST /api/v2/agent-runs/{run_id}/commands` records continue-after-current commands
- [ ] 2. Call through the organism backend bridge
  - [x] 2-1. Depend on the `AgentBackendAdapter` contract defined in [57-5-organism-backend-bridge.md](57-5-organism-backend-bridge.md)
  - [x] 2-2. Preserve raw backend event-log refs while exposing generic Agent run status
  - [x] 2-3. Keep backend selection internal to the Agent control plane
  - [x] 2-4. Keep CLI organism behavior unchanged when the first backend is Super DAN
- [ ] 3. Define Agent run storage
  - [x] 3-1. Persist run metadata, profile, workspace, objective, selected backend, event-log path, status, and final summary
  - [x] 3-2. Persist aggregate and per-round token usage for active run management
  - [ ] 3-3. Link Agent runs back into chat threads via `task_run_ref`
  - [x] 3-4. Support reconnect after frontend reload
- [ ] 4. Add safety and approval boundaries
  - [x] 4-1. Thread workspace root, approval mode, mutation permission, and available tools into the Agent run
  - [ ] 4-2. Preserve read-only vs mutation vs external-side-effect envelopes
  - [x] 4-3. Make stop/cancel behavior deterministic and observable
  - [x] 4-4. Preserve structured attachment refs and surface delivery handles as metadata, not prompt-only text
- [ ] 5. Validate with narrow end-to-end tests
  - [x] 5-1. Fake-provider Agent run completes and streams progress
  - [ ] 5-2. Stop/cancel closes streams and persists interrupted state
  - [ ] 5-3. Super DAN CLI still works after backend extraction when Super DAN is the selected adapter

## Decisions
- The first Agent backend should be Super DAN because it already has the best live execution UX.
- The generic Agent run contract must not expose Super-DAN-specific terms as required frontend concepts.
- A backend wrapper is preferred over shelling out to the CLI.
- Active-run follow-ups are explicit: append-to-checkpoint and continue-after-current are separate commands. The backend should not infer one from an ambiguous frontend "auto" mode.
- Detailed organism/Super DAN bridge tradeoffs are owned by [57-5-organism-backend-bridge.md](57-5-organism-backend-bridge.md).
- Agent run command/event hooks are owned by [57-9-agent-event-hooks-and-telegram-progress.md](57-9-agent-event-hooks-and-telegram-progress.md).

## Notes
- 2026-04-30: Agent run records and task snapshots now carry aggregate `token_usage` plus `token_usage_rounds`. The deterministic backend emits a provider-free usage record for tests, and the Super DAN bridge preserves provider usage from live model-response rows and final backend results.
- 2026-04-30: Added `POST /api/v2/agent-runs/{run_id}/execute`, the pluggable Agent backend runner, internal backend selection, task/run backend-result metadata, and `WS /api/v2/agent-runs/{run_id}/events` replay/streaming over persisted JSONL events. The default backend policy selects Super DAN; tests use the deterministic backend for provider-free execution.
- 2026-04-30: Added the first `task_run_ref` schema/response link for V2 Agent runs. Deep backfill into every legacy persisted chat message path remains open under 3-2.
- 2026-04-30: Workspace root now threads through the V2 Agent run surface: start-command payloads, `AgentRunRecord`, task snapshots, accepted events, and queue items persist the resolved workspace, defaulting to server-expanded `~` when the surface omits workspace context.
- 2026-04-29: Landed the first `/api/v2/agent-runs` control API slice with durable run snapshots, event replay/append, and normalized command append through `/api/v2/agent-runs/{run_id}/commands`. The endpoints are under the V2 prefix for rollout.
- Server-side organism-log discovery already sees `.dan-super/runs/**/events.jsonl`; this should be reused for V2 task timelines instead of inventing a second trace format.
