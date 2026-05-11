# 57-7: Thread, Task Identity, and Topic Queues

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** in-progress
**Goal:** Make thread/task binding and topic queueing explicit so rapid Telegram/frontend/CLI turns can attach to existing work, start new work, or wait behind the right active task without blocking unrelated tasks.

## Tasks
- [ ] 1. Define V2 thread and task identity
  - [x] 1-1. Keep chat thread identity separate from Agent task identity and Agent run attempts
  - [ ] 1-2. Link final Agent run results back into the chat thread with `task_run_ref`
  - [x] 1-3. Store task snapshots with current phase, owner, queue state, latest progress, latest artifact refs, blocker, and trace refs
  - [x] 1-4. Preserve compatibility with existing `ConciergeTask` and project/task ids by using explicit field names
  - [x] 1-5. Bind every V2 session/task/run to a resolved workspace, defaulting to server-expanded `~`
- [ ] 2. Define the task binding algorithm
  - [ ] 2-1. Prefer explicit user references such as `/new`, `/task`, `/status`, `/cancel`, artifact names, and task ids
  - [x] 2-2. Use Telegram reply-to/progress-message lineage and forum topic id as strong binding evidence
  - [x] 2-3. Use recent active task, thread history, attachments, and semantic similarity as weaker evidence
  - [ ] 2-4. Ask a clarification when binding is ambiguous and the action is mutating or expensive
  - [ ] 2-5. Create a new task when the message is unrelated to the active task or explicitly requests a new objective
- [ ] 3. Define topic queue semantics
  - [x] 3-1. Queue key: user/privacy scope plus workspace/project/thread/task plus resource locks
  - [x] 3-2. Same-task follow-ups sent through the append action enter a checkpoint queue and inject only after a safe model/tool boundary
  - [x] 3-3. Same-task follow-ups sent through the continue-after action wait until the active run reaches a terminal state
  - [ ] 3-4. Unrelated tasks can run concurrently subject to provider and workspace caps
  - [x] 3-5. Status/cancel/approval/clarification turns bypass the work queue
  - [x] 3-6. Duplicate or near-duplicate turns coalesce within their explicit queue lane instead of launching redundant runs
- [ ] 4. Expose task retrieval and queue status
  - [x] 4-1. `GET /api/v2/tasks/{task_id}` returns the current task snapshot
  - [x] 4-2. `GET /api/v2/threads/{thread_id}/tasks` lists active/recent task cards
  - [ ] 4-3. Natural-language "what is happening?" resolves to the same task snapshot data
  - [x] 4-4. Queue position and reason are visible to the surface renderer
- [ ] 5. Validate with routing and queue regressions
  - [ ] 5-1. Rapid unrelated Telegram turns start separate tasks
  - [ ] 5-2. Replying to an active Agent progress message appends to that task
  - [x] 5-3. A second same-task mutating instruction sent through append injects at the next safe checkpoint
  - [x] 5-4. A second same-task mutating instruction sent through continue-after waits until the active run completes
  - [x] 5-5. Status/cancel/approval bypass queue blocking
  - [x] 5-6. Same-topic explicit follow-ups inherit a path-inferred active task workspace
  - [ ] 5-7. Group-topic routing does not leak across topics without explicit binding evidence

## Decisions
- Task is the durable unit of work for V2 Agent. Chat thread is the conversational container.
- Queueing is task/resource-aware, not just surface-session-aware.
- Active-run queue placement is never automatic in V2. The frontend and API expose two deliberate lanes: checkpoint append and continue after current.
- The triage layer resolves task identity before invoking ChatManagerV2 or AgentControlPlane.

## Notes
- 2026-04-30: The in-process Telegram adapter bridge now maintains the same bounded recent `user`/`assistant` history window as the standalone fleet and passes it, along with native reply metadata, into durable V2 Agent-run requests for backend context.
- 2026-04-30: Telegram fleet context now carries broad conversation keys, reply lane keys, and bounded history into the V2 payload. Agent start commands persist `history`, `reply_context`, and compact `surface_context`; queue items carry the same metadata for later checkpoint/continue handling.
- 2026-04-30: Same-topic follow-ups can now attach to an active task's workspace when the original task inferred its workspace from a message path. Exact topic keys still include workspace identity, but the store has a surface-topic fallback for explicit append/continue turns that omit the path.
- 2026-04-30: Workspace binding landed for V2 sessions. `SurfaceTurn` resolves `workspace_root` / `workspace_id` from explicit surface context or defaults to `~`, topic keys include workspace identity, and `ChatV2Store` persists workspace fields on task snapshots, Agent run records, queue items, and last surface-turn metadata.
- 2026-04-29: Added `src/dan/server/chat_v2_store.py`, a JSON/JSONL-backed V2 task/run store. `/api/v2/chat/message` now records task/run state when the V2 bridge delegates to legacy chat streams, and `/api/v2/agent-runs`, `/api/v2/tasks/{task_id}`, `/api/v2/threads/{thread_id}/tasks`, and Agent-run event/command endpoints expose durable snapshots and queue positions.
- This plan should reuse the completed Plan 27 concurrent dispatch and Plan 49 task lifecycle ideas, but V2 should expose the simpler Chat/Agent product shape.
- Same-task "append into active run" should start with safe checkpoint injection only; arbitrary mid-tool interruption can remain a later capability.
- Branching is a first-class task/thread operation, not a hidden retry. Branches must preserve lineage fields and make parent thread, branch point, active branch, and queued/running task ownership visible to every surface that can display threads.
