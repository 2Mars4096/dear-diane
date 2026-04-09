# 52-6: Durable Agent Runtime

**Parent:** [52-multicellular-composition](52-multicellular-composition.md)
**Status:** completed
**Goal:** Extend the universal-worker runtime into a durable mailbox-backed agent control plane where bounded execution is just a stricter policy over the same runner.

## Tasks
- [x] 1. Add durable mailbox/session policy models on top of `StandaloneWorkerRunner`
  - [x] 1-1. Represent one ordered mailbox per agent session plus background-task slots
  - [x] 1-2. Keep bounded execution expressible as a `DurableAgentPolicy.bounded(...)` preset instead of a second runtime
- [x] 2. Implement the durable runner membrane without replacing the worker core
  - [x] 2-1. Reuse `StandaloneWorkerRunner` for per-message execution while preserving continuation across mailbox turns
  - [x] 2-2. Persist the effective durable policy on the session so message ordering and close-when-idle behavior stay stable
- [x] 3. Export the runtime and lock it with focused regressions
  - [x] 3-1. Cover ordered mailbox processing and continuation reuse
  - [x] 3-2. Cover bounded-policy reduction plus background-task capacity and closure behavior

## Decisions
- One agent instance owns one ordered mailbox. Concurrency comes from background child-task slots, not multiple inbound user queues.
- Durable mode is the only runtime model. Bounded execution is a stricter policy preset (`max_user_messages=1`, `close_when_idle=True`) over that same runtime.
- The patch stays additive over `StandaloneWorkerRunner`; it does not replace `WorkerCoreExecutor` or the existing cell/tissue/organ/organism membrane.

## Notes
- `src/dan/worker/runner.py` now defines `DurableAgentPolicy`, `DurableMailboxMessage`, `DurableBackgroundTask`, `DurableAgentSessionState`, `DurableAgentTurnResult`, and `DurableAgentRunner`.
- Each mailbox message now carries a stable `message_index`, and the runner records the effective durable policy on the session so background-task completion can honor bounded / close-when-idle closure correctly.
- `run_until_idle(...)` now waits for background work to drain before concluding that the durable session is idle.
- The public worker API re-exports the durable runtime types through `src/dan/worker/__init__.py`.
- Validation:
  - `PYTHONPATH=src pytest -q tests/test_worker/test_core_executor.py -k 'standalone_runner or durable_agent_runner'` (`8 passed`)
  - `PYTHONPATH=src python -m py_compile src/dan/worker/runner.py src/dan/worker/__init__.py tests/test_worker/test_core_executor.py`
