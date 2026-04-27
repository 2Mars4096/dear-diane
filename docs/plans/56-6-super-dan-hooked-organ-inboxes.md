# 56-6: Super DAN Hooked Organ Inboxes

**Parent:** [56-universal-organism-and-cell-restructure](56-universal-organism-and-cell-restructure.md)
**Status:** not-started
**Goal:** Turn Super DAN's passive event stream into durable event-driven organ inboxes, lease-gated wakeups, and worktree-aware parallel execution policy while keeping the critical path single-writer.

## Operating Model

Super DAN should move from "emit logs, then summarize at the end" toward a deterministic event-driven organism loop:

```text
event -> hook rule -> packet -> organ inbox -> queue policy/admission -> lease/owner lock -> worker/model call -> new events
```

The hook layer is internal organism infrastructure. It should not start as arbitrary external shell hooks or a workflow-engine plugin surface. Hooks enqueue typed packets; deterministic admission and lease policy decides when those packets may run.

## Tasks

- [ ] 1. Define Super DAN hook and inbox contracts
  - [ ] 1-1. Add typed contracts for `SuperHookRule`, `SuperPacket`, `SuperInbox`, `SuperQueuePolicy`, `SuperLease`, `SuperOwnerLock`, and `SuperWorktreeTask`.
  - [ ] 1-2. Normalize the current `.dan-super/runs/*/events.jsonl` rows into a stable event taxonomy for run lifecycle, model requests/responses, tool calls, validation, repair, synthesis, heartbeat, and queue state.
  - [ ] 1-3. Add causal metadata to hook packets: `packet_id`, `source_event_id`, `parent_packet_id`, `run_id`, `turn_id`, `state_version`, `idempotency_key`, `priority`, and `created_at`.
  - [ ] 1-4. Make packet payloads bounded projections, with raw event/artifact refs kept in the append-only event log.

- [ ] 2. Add durable local state for replay and crash recovery
  - [ ] 2-1. Persist hook state under `.dan-super/state/` using JSONL or SQLite: packets, inbox cursors, leases, owner locks, hook rules, queue metrics, and dead letters.
  - [ ] 2-2. Rebuild in-memory inbox state by replaying event logs plus hook state on startup.
  - [ ] 2-3. Make hook dispatch idempotent so replay does not duplicate validation, repair, synthesis, or progress packets.
  - [ ] 2-4. Record every admission, coalescing, preemption, backpressure, lease acquisition, lease expiry, ack, nack, and dead-letter decision as a structured event.

- [ ] 3. Implement internal hook routing rules
  - [ ] 3-1. Route `tool.completed` for material writes to the validation inbox.
  - [ ] 3-2. Route `live.validation.completed` with `passed=false` to the repair or immune inbox when a bounded repair attempt is still allowed.
  - [ ] 3-3. Route `live.validation.completed` with `passed=true` to the synthesis inbox.
  - [ ] 3-4. Route `live.website_repair.completed` back to the validation inbox.
  - [ ] 3-5. Prepare reader/scout hooks for future research-like Super DAN work: `reader.completed` and `scout.completed` enqueue brain review packets instead of directly waking the orchestrator model.
  - [ ] 3-6. Route stale heartbeat or long-idle model/tool spans to immune/reallocation packets without interrupting healthy active work.

- [ ] 4. Add queue policy and reactivity controls
  - [ ] 4-1. Support operator profiles: `immediate`, `balanced`, and `batch`.
  - [ ] 4-2. Represent each inbox policy with `max_pending`, `max_active_leases`, `max_wait_ms`, `priority_bands`, and `queue_full_action`.
  - [ ] 4-3. Implement queue-full actions: `coalesce`, `backpressure`, `priority_preempt`, `drop_stale`, and `dead_letter`.
  - [ ] 4-4. Use coalescing for reader/scout evidence so the orchestrator can review the latest batch instead of every individual completion.
  - [ ] 4-5. Use priority preemption for validation failures and safety/immune events.
  - [ ] 4-6. Use backpressure for builder/patch packets that mutate the same owner scope.
  - [ ] 4-7. Drop stale heartbeat/progress packets once a newer equivalent packet exists.
  - [ ] 4-8. Surface queue depth, oldest age, active lease count, and coalesced/drop counts in the live console and JSON output.

- [ ] 5. Add lease and owner-lock semantics
  - [ ] 5-1. Give the brain/orchestrator a single active lease per run or per critical path so multiple triggers cannot start overlapping orchestration reviews.
  - [ ] 5-2. Use separate bounded leases for validation, synthesis, memory, scout, and repair organs where parallelism is safe.
  - [ ] 5-3. Attach lease heartbeats and expiry timestamps so crashed or stalled organs release work deterministically.
  - [ ] 5-4. Add owner locks for workspace scopes: whole workspace, file path, artifact group, plan document, and generated worktree.
  - [ ] 5-5. Make completion idempotent: an ack for an already-completed lease is harmless, and a stale ack cannot overwrite newer state.
  - [ ] 5-6. Emit a clear queue/lock event when an organ is busy and the next packet is held, coalesced, preempted, or dead-lettered.

- [ ] 6. Keep the critical path contained
  - [ ] 6-1. Add packet fields for `critical_path`, `conflict_scope`, `parallel_mode`, `merge_required`, and `owner_scope`.
  - [ ] 6-2. Keep the main workspace lane single-writer for critical-path edits, plan files, durable state files, final summaries, and validation/synthesis gates.
  - [ ] 6-3. Serialize validation and synthesis gates for a candidate that touches the same owner scope.
  - [ ] 6-4. Allow non-mutating read/scout/memory packets to proceed in parallel when they do not hold the brain lease.
  - [ ] 6-5. Make the final reducer/admission lane the only authority that can mark a live run `completed`, `failed`, or `blocked`.

- [ ] 7. Add worktree parallelism for conflicting owners
  - [ ] 7-1. Add a worktree execution lane for packets with conflicting write owners or exploratory patch alternatives.
  - [ ] 7-2. Create isolated worktrees under `.dan-super/worktrees/<task-id>` with deterministic task ids and branch names such as `super-dan/<run-id>/<task-id>`.
  - [ ] 7-3. Have worktree workers return diff packets with changed files, summary, validation evidence, candidate score, owner scope, and merge risk.
  - [ ] 7-4. Keep worktree candidates non-authoritative until the main merge/admission lane applies or rejects them.
  - [ ] 7-5. Require the merge/admission lane to validate candidate diffs against the current main workspace before applying them.
  - [ ] 7-6. Support cleanup of stale, failed, superseded, or merged worktrees without deleting the event/diff record.
  - [ ] 7-7. Prefer worktrees when two write packets have overlapping file ownership and both have enough expected value to run.

- [ ] 8. Stage multi-server queue support without overcommitting early
  - [ ] 8-1. Phase 1: local file or SQLite state only, with deterministic atomic claims on one machine.
  - [ ] 8-2. Phase 2: SQLite leased priority queue with atomic `claim if lease expired or unclaimed` semantics.
  - [ ] 8-3. Phase 3: external shared queue/backing store only if Super DAN needs multi-host workers.
  - [ ] 8-4. Treat "5 coding workers, queue length 1" as one active mutation lane unless `max_active_leases` and owner locks prove independent owners.
  - [ ] 8-5. Avoid plain FIFO semantics for serious execution; use priority, leases, owner locks, and idempotency.

- [ ] 9. Expose operator-facing CLI controls
  - [ ] 9-1. Add `--reactivity immediate|balanced|batch` for queue profile selection.
  - [ ] 9-2. Add `--worktree-parallelism N` for bounded isolated patch exploration.
  - [ ] 9-3. Add interactive `/status` output for inbox depth, active leases, owner locks, worktrees, and last decisions.
  - [ ] 9-4. Add an optional `/queues` or `--queue-status` view for debugging hook routing without requiring raw JSONL inspection.
  - [ ] 9-5. Keep the compact live console useful by showing only state transitions, not every low-level queue event unless verbose mode is enabled.

- [ ] 10. Add focused tests and acceptance coverage
  - [ ] 10-1. Unit-test hook routing from representative Super DAN event rows into the correct inbox packets.
  - [ ] 10-2. Unit-test coalescing, backpressure, priority preemption, stale heartbeat dropping, dead-lettering, and queue metrics.
  - [ ] 10-3. Unit-test lease acquisition, lease expiry, stale ack rejection, and idempotent completion.
  - [ ] 10-4. Unit-test owner-lock conflicts for same-file, same-artifact, and whole-workspace scopes.
  - [ ] 10-5. Unit-test worktree diff packet admission and rejection without mutating the main workspace before admission.
  - [ ] 10-6. Replay-test `.dan-super` event logs so restart does not duplicate repair, validation, or synthesis.
  - [ ] 10-7. Keep existing Super DAN CLI tests passing, including live progress, lenient validation, repair, and interactive UX coverage.

## Decisions

- Hooks enqueue packets; they do not call models or mutate state directly beyond durable queue state.
- The orchestrator/brain is single-lease on the critical path. Multiple events can request review, but only one review owns the authority lease at a time.
- Queue length is not the same as worker parallelism. Parallel mutation requires active leases plus non-conflicting owner scopes.
- Worktrees are for conflicting or exploratory parallelism. The main workspace remains the authoritative critical-path lane.
- External hooks, shell callbacks, and user-provided scripts are deferred until internal hook replay, idempotency, and lease semantics are reliable.
- The event log remains the source of truth. Inbox state is a replayable projection with durable cursors and leases.

## Notes

- 2026-04-27 discussion follow-up: the existing Super DAN live event stream and timestamped progress renderer are prerequisites for this plan, but they are not enough. The next patch should make events actionable through durable organ inboxes.
- Immediate responsiveness can be achieved by setting small `max_wait_ms` and low queue length, but safety still comes from leases, owner locks, coalescing, and deterministic admission.
- If the orchestrator is busy and the queue fills, policy should be packet-specific: reader/scout evidence coalesces, validation failures preempt, builder patches backpressure, and stale heartbeat/progress rows drop.
- This plan is about Super DAN organism hooks. It is not the older workflow-engine hook model and should not inherit workflow graph semantics unless a later bridge explicitly needs them.
