# 57-5: Organism Backend Bridge

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** in-progress
**Goal:** Decide and implement the clean bridge between V2 Agent runs and DAN's organism execution surfaces, starting with Super DAN while preserving a path to the universal organism and other bounded organisms.

## Tasks
- [ ] 1. Inventory candidate backends
  - [x] 1-1. Super DAN live runner from `src/dan/cli/super_organism.py`
  - [ ] 1-2. Universal organism runner from `src/dan/worker/organisms/universal_organism.py`
  - [ ] 1-3. Legacy facade composers from `src/dan/worker/organisms/legacy_facades.py`
  - [ ] 1-4. DAN Code / DAN Research bounded organisms as later adapters, not first-class V2 modes
- [ ] 2. Define `AgentBackendAdapter`
  - [x] 2-1. Inputs: objective, workspace root, profile policy, mutation permission, approval envelope, surface context, thread/run ids
  - [x] 2-2. Outputs: run id, status, event stream, final summary, artifacts, validation result, blocker report, trace/log refs
  - [ ] 2-3. Lifecycle: start, stream, stop, retry, resume/reconnect, final-state query
  - [x] 2-4. Safety: explicit read-only/mutation/external-side-effect envelope and tool basket
  - [x] 2-5. Inputs also include structured attachment refs and an opaque surface delivery handle for progress sinks
- [ ] 3. Choose the first bridge strategy
  - [x] 3-1. Prefer direct callable Super DAN wrapper if it can reuse live execution without CLI parsing or subprocess shellout
  - [ ] 3-2. Prefer universal organism facade if Super DAN live can be represented as `OrganismPlan` without losing current quality
  - [x] 3-3. Avoid shelling out to `dan super-organism` unless a short-term prototype needs it and cleanup is planned
  - [x] 3-4. Preserve CLI behavior and tests no matter which bridge is selected
- [ ] 4. Normalize event streams
  - [x] 4-1. Map Super DAN `live.*`, `super.hook.*`, validation, repair, and completion rows into generic Agent events
  - [x] 4-2. Preserve raw `.dan-super/runs/turn-XX/events.jsonl` paths for inspection and replay
  - [x] 4-3. Keep a stable frontend-facing vocabulary: `planned`, `worker_started`, `tool_used`, `artifact_changed`, `validation_started`, `repair_started`, `completed`, `failed`, `blocked`, `stopped`
  - [x] 4-4. Make event normalization reversible enough for debugging by carrying source event ids/types
  - [x] 4-5. Preserve provider token usage rows as normalized `token_usage_recorded` events
  - [ ] 4-6. Feed normalized events into the surface progress sinks from [57-9](57-9-agent-event-hooks-and-telegram-progress.md)
- [ ] 5. Define backend selection policy
  - [x] 5-1. Use Super DAN for general coding/build/website/operator tasks in the first rollout
  - [ ] 5-2. Use Chat-only or legacy research paths for read-only research until Agent research quality is proven
  - [x] 5-3. Keep backend selection internal to the Agent control plane, not exposed as top-level UI mode clutter
  - [x] 5-4. Log selected backend and rationale on every run
- [ ] 6. Validate bridge quality
  - [ ] 6-1. Fake-provider Super DAN Agent run completes through the backend adapter
  - [ ] 6-2. Existing `dan super-organism` CLI smokes still pass
  - [x] 6-3. Event stream reconnect can recover from the persisted raw trace
  - [x] 6-4. Agent run final summary links to artifacts and trace logs

## Decisions
- The V2 frontend should call a generic Agent backend contract, not Super DAN internals directly.
- Super DAN remains the first proving backend because it currently has the strongest task execution behavior.
- The first bridge uses a direct callable wrapper around Super DAN live internals, not a subprocess shellout.
- A deterministic adapter remains available for provider-free smoke tests and server regressions.
- Universal organism remains the intended long-term substrate, but Plan 57 should not wait for all Plan 56 migration work before exposing a useful Agent path.
- Shelling out to the CLI is acceptable only as a temporary spike, not the durable architecture.

## Notes
- 2026-04-30: The backend bridge now preserves token accounting. Super DAN `model.responded` rows include provider usage, the mapper projects those rows to `token_usage_recorded`, and backend results surface aggregate `token_usage` plus per-round records for the V2 store.
- 2026-04-30: `src/dan/server/chat_v2_backend.py` now defines `AgentBackendAdapter`, `AgentBackendRunRequest`, `AgentBackendRunResult`, the default Super DAN adapter, deterministic adapter, internal backend selection, and `run_agent_backend(...)`. Super DAN rows are projected into normalized Agent events as they are written, and raw `.dan-super/runs/turn-XX/events.jsonl` paths are retained as trace refs.
- This plan is the place to think through "organism vs Super DAN" without blocking the rest of Plan 57.
- The likely practical order is: define adapter contract, extract a callable Super DAN wrapper, normalize events, then replace the implementation behind the adapter with universal-organism facades once parity is proven.
- The frontend should be able to render the same Agent run whether it came from Super DAN today or the universal organism later.
- Surface-specific progress rendering belongs outside this adapter. The adapter emits normalized Agent events plus raw trace refs.
