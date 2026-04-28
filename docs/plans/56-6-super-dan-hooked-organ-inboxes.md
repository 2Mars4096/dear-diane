# 56-6: Super DAN Hooked Organ Inboxes

**Parent:** [56-universal-organism-and-cell-restructure](56-universal-organism-and-cell-restructure.md)
**Status:** in-progress
**Goal:** Turn Super DAN's passive event stream into durable event-driven organ inboxes, lease-gated wakeups, and worktree-aware parallel execution policy while keeping the critical path single-writer.

## Operating Model

Super DAN should move from "emit logs, then summarize at the end" toward a deterministic event-driven organism loop:

```text
event -> hook rule -> packet -> organ inbox -> queue policy/admission -> lease/owner lock -> worker/model call -> new events
```

The hook layer is internal organism infrastructure. It should not start as arbitrary external shell hooks or a workflow-engine plugin surface. Hooks enqueue typed packets; deterministic admission and lease policy decides when those packets may run.

## Tasks

- [x] 1. Define Super DAN hook and inbox contracts
  - [x] 1-1. Add typed contracts for `SuperHookRule`, `SuperPacket`, `SuperInbox`, `SuperQueuePolicy`, `SuperLease`, `SuperOwnerLock`, and `SuperWorktreeTask`.
  - [x] 1-2. Normalize the current `.dan-super/runs/*/events.jsonl` rows into a stable event taxonomy for run lifecycle, model requests/responses, tool calls, validation, repair, synthesis, heartbeat, and queue state.
  - [x] 1-3. Add causal metadata to hook packets: `packet_id`, `source_event_id`, `parent_packet_id`, `run_id`, `turn_id`, `state_version`, `idempotency_key`, `priority`, and `created_at`.
  - [x] 1-4. Make packet payloads bounded projections, with raw event/artifact refs kept in the append-only event log.

- [ ] 2. Add durable local state for replay and crash recovery
  - [x] 2-1. Persist hook state under `.dan-super/state/` using JSONL or SQLite: packets, inbox cursors, leases, owner locks, hook rules, queue metrics, and dead letters.
  - [x] 2-2. Rebuild in-memory inbox state by replaying event logs plus hook state on startup.
  - [x] 2-3. Make hook dispatch idempotent so replay does not duplicate validation, repair, synthesis, or progress packets.
  - [ ] 2-4. Record every admission, coalescing, preemption, backpressure, lease acquisition, lease expiry, ack, nack, and dead-letter decision as a structured event.

- [x] 3. Implement internal hook routing rules
  - [x] 3-1. Route `tool.completed` for material writes to the validation inbox.
  - [x] 3-2. Route `live.validation.completed` with `passed=false` to first-write recovery when no required edit exists, and to repair only when a concrete patch failed validation.
  - [x] 3-3. Route `live.validation.completed` with `passed=true` to the synthesis inbox.
  - [x] 3-4. Route `live.website_repair.completed` back to the validation inbox.
  - [x] 3-5. Prepare reader/scout hooks for future research-like Super DAN work: `reader.completed` and `scout.completed` enqueue brain review packets instead of directly waking the orchestrator model.
  - [x] 3-6. Route stale heartbeat or long-idle model/tool spans to immune/reallocation packets without interrupting healthy active work.
  - [x] 3-7. Route `live.website_first_write_recovery.completed` back to validation, while suppressing another recovery hook once the no-write recovery attempt is exhausted.
  - [x] 3-8. Suppress repair packets from terminal failed validation events once the bounded website repair pass is already exhausted.

- [ ] 4. Add queue policy and reactivity controls
  - [x] 4-1. Support operator profiles: `immediate`, `balanced`, and `batch`.
  - [x] 4-2. Represent each inbox policy with `max_pending`, `max_active_leases`, `max_wait_ms`, `priority_bands`, and `queue_full_action`.
  - [x] 4-3. Implement queue-full actions: `coalesce`, `backpressure`, `priority_preempt`, `drop_stale`, and `dead_letter`.
  - [x] 4-4. Use coalescing for reader/scout evidence so the orchestrator can review the latest batch instead of every individual completion.
  - [x] 4-5. Use priority preemption for validation failures and safety/immune events.
  - [x] 4-6. Use backpressure for builder/patch packets that mutate the same owner scope.
  - [x] 4-7. Drop stale heartbeat/progress packets once a newer equivalent packet exists.
  - [x] 4-8. Surface queue depth, oldest age, active lease count, and coalesced/drop counts in the live console and JSON output.

- [x] 5. Add lease and owner-lock semantics
  - [x] 5-1. Give the brain/orchestrator a single active lease per run or per critical path so multiple triggers cannot start overlapping orchestration reviews.
  - [x] 5-2. Use separate bounded leases for validation, synthesis, memory, scout, and repair organs where parallelism is safe.
  - [x] 5-3. Attach lease heartbeats and expiry timestamps so crashed or stalled organs release work deterministically.
  - [x] 5-4. Add owner locks for workspace scopes: whole workspace, file path, artifact group, plan document, and generated worktree.
  - [x] 5-5. Make completion idempotent: an ack for an already-completed lease is harmless, and a stale ack cannot overwrite newer state.
  - [x] 5-6. Emit a clear queue/lock event when an organ is busy and the next packet is held, coalesced, preempted, or dead-lettered.

- [ ] 6. Keep the critical path contained
  - [x] 6-1. Add packet fields for `critical_path`, `conflict_scope`, `parallel_mode`, `merge_required`, and `owner_scope`.
  - [x] 6-2. Keep the main workspace lane single-writer for critical-path edits, plan files, durable state files, final summaries, and validation/synthesis gates.
  - [ ] 6-3. Serialize validation and synthesis gates for a candidate that touches the same owner scope.
  - [x] 6-4. Allow non-mutating read/scout/memory packets to proceed in parallel when they do not hold the brain lease.
  - [ ] 6-5. Make the final reducer/admission lane the only authority that can mark a live run `completed`, `failed`, or `blocked`.

- [ ] 7. Add worktree parallelism for conflicting owners
  - [ ] 7-1. Add a worktree execution lane for packets with conflicting write owners or exploratory patch alternatives.
  - [ ] 7-2. Create isolated worktrees under `.dan-super/worktrees/<task-id>` with deterministic task ids and branch names such as `super-dan/<run-id>/<task-id>`.
  - [ ] 7-3. Have worktree workers return diff packets with changed files, summary, validation evidence, candidate score, owner scope, and merge risk.
  - [x] 7-4. Keep worktree candidates non-authoritative until the main merge/admission lane applies or rejects them.
  - [ ] 7-5. Require the merge/admission lane to validate candidate diffs against the current main workspace before applying them.
  - [ ] 7-6. Support cleanup of stale, failed, superseded, or merged worktrees without deleting the event/diff record.
  - [ ] 7-7. Prefer worktrees when two write packets have overlapping file ownership and both have enough expected value to run.

- [ ] 8. Stage multi-server queue support without overcommitting early
  - [x] 8-1. Phase 1: local file or SQLite state only, with deterministic atomic claims on one machine.
  - [ ] 8-2. Phase 2: SQLite leased priority queue with atomic `claim if lease expired or unclaimed` semantics.
  - [ ] 8-3. Phase 3: external shared queue/backing store only if Super DAN needs multi-host workers.
  - [x] 8-4. Treat "5 coding workers, queue length 1" as one active mutation lane unless `max_active_leases` and owner locks prove independent owners.
  - [x] 8-5. Avoid plain FIFO semantics for serious execution; use priority, leases, owner locks, and idempotency.

- [x] 9. Expose operator-facing CLI controls
  - [x] 9-1. Add `--reactivity immediate|balanced|batch` for queue profile selection.
  - [x] 9-2. Add `--worktree-parallelism N` for bounded isolated patch exploration.
  - [x] 9-3. Add interactive `/status` output for inbox depth, active leases, owner locks, worktrees, and last decisions.
  - [x] 9-4. Add an optional `/queues` or `--queue-status` view for debugging hook routing without requiring raw JSONL inspection.
  - [x] 9-5. Keep the compact live console useful by showing only state transitions, not every low-level queue event unless verbose mode is enabled.

- [ ] 10. Add focused tests and acceptance coverage
  - [x] 10-1. Unit-test hook routing from representative Super DAN event rows into the correct inbox packets.
  - [ ] 10-2. Unit-test coalescing, backpressure, priority preemption, stale heartbeat dropping, dead-lettering, and queue metrics.
  - [x] 10-3. Unit-test lease acquisition, lease expiry, stale ack rejection, and idempotent completion.
  - [x] 10-4. Unit-test owner-lock conflicts for same-file, same-artifact, and whole-workspace scopes.
  - [x] 10-5. Unit-test worktree diff packet admission and rejection without mutating the main workspace before admission.
  - [x] 10-6. Replay-test `.dan-super` event logs so restart does not duplicate repair, validation, or synthesis.
  - [x] 10-7. Keep existing Super DAN CLI tests passing, including live progress, lenient validation, repair, and interactive UX coverage.
  - [x] 10-8. Cover exact template-hit static validation diagnostics, repair-prompt handoff, successful repair, failed repair, and exhausted-repair hook suppression.

## Decisions

- Hooks enqueue packets; they do not call models or mutate state directly beyond durable queue state.
- The orchestrator/brain is single-lease on the critical path. Multiple events can request review, but only one review owns the authority lease at a time.
- Queue length is not the same as worker parallelism. Parallel mutation requires active leases plus non-conflicting owner scopes.
- Worktrees are for conflicting or exploratory parallelism. The main workspace remains the authoritative critical-path lane.
- External hooks, shell callbacks, and user-provided scripts are deferred until internal hook replay, idempotency, and lease semantics are reliable.
- The event log remains the source of truth. Inbox state is a replayable projection with durable cursors and leases.

## Notes

- 2026-04-27 discussion follow-up: the existing Super DAN live event stream and timestamped progress renderer are prerequisites for this plan, but they are not enough. The next patch should make events actionable through durable organ inboxes.
- 2026-04-27 implementation slice: `src/dan/cli/super_hooks.py` now provides the first internal hook runtime, durable JSON/JSONL state under `.dan-super/state/`, hook packet idempotency, queue policies, leases, owner locks, worktree policy contracts, live progress hook rows, `--reactivity`, `--queue-status`, `--worktree-parallelism`, and interactive `/status` / `/queues`. Actual worktree execution and external/multi-server queue backends remain open.
- 2026-04-27 continuation slice: hook state can now be rebuilt idempotently from `.dan-super/runs/turn-XX/events.jsonl`; stale heartbeat packets replace older pending heartbeat packets instead of growing the queue; and worktree diff packets now pass through a non-authoritative admission/rejection record before any future main-lane merge.
- 2026-04-27 repair-exhaustion slice: final website validation events now carry `repair_attempted` and `repair_exhausted`, and hook routing suppresses another `repair_requested` packet once the one bounded repair pass has already failed. This keeps the hook projection honest until the repair lane becomes a real asynchronous executor with its own retry budget.
- 2026-04-27 no-write recovery slice: website live runs now treat "builder completed but changed no required files" as first-write recovery instead of validation repair. The CLI runs one bounded direct-write recovery pass, logs `[retry] first-write recovery ...`, revalidates if a patch lands, and hook routing no longer emits confusing repair packets for exhausted no-write failures.
- 2026-04-28 generic no-mutation routing slice: generic live no-mutation rows now route to first-write recovery rather than repair, final validation suppresses further recovery after the bounded recovery attempts are spent, and `live.generic_first_write_recovery.completed` feeds the validation inbox only when the recovery actually changed files.
- 2026-04-27 generic validation routing slice, updated 2026-04-28: generic live validation rows now carry mutated paths as changed-file evidence, and `live.generic_repair.completed` feeds the validation inbox like website repair completion. Quality failures after a real generic mutation route to repair/revalidation rather than `first_write_recovery`.
- Immediate responsiveness can be achieved by setting small `max_wait_ms` and low queue length, but safety still comes from leases, owner locks, coalescing, and deterministic admission.
- If the orchestrator is busy and the queue fills, policy should be packet-specific: reader/scout evidence coalesces, validation failures preempt, builder patches backpressure, and stale heartbeat/progress rows drop.
- This plan is about Super DAN organism hooks. It is not the older workflow-engine hook model and should not inherit workflow graph semantics unless a later bridge explicitly needs them.
