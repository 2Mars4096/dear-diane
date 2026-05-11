# Idea Cart

Last reviewed: 2026-05-11

## Active Items

### IC-001 - Use Chat/Agent V2 for Human Run Messages
- Status: committed
- Priority: high
- Kind: decision
- Scope: Super DAN active-run UX
- Point: Human messages during a Super DAN run should be owned by Chat/Agent V2, not by a separate Super DAN-only chat queue.
- Why: Chat/Agent V2 already has thread, active-run, queue-lane, status, cancel, retry, and surface semantics.
- Checkout target: implementation plan
- Acceptance: Super DAN live runs can receive user messages through V2 active-run queue records without duplicating queue concepts.

### IC-002 - Add Super DAN Active-Run Bridge
- Status: committed
- Priority: high
- Kind: follow-up
- Scope: Super DAN and Chat/Agent V2 integration
- Point: Add a small bridge that binds a running Super DAN task to Chat/Agent V2 active-run state.
- Why: User messages need to attach to the current run rather than waiting for the CLI run to finish.
- Checkout target: implementation checklist
- Acceptance: A V2 Agent run can route queued operator messages into the matching Super DAN live execution context.

### IC-003 - Poll Operator Messages at Safe Checkpoints
- Status: committed
- Priority: high
- Kind: constraint
- Scope: Super DAN runtime loop
- Point: Super DAN should poll queued operator messages at safe boundaries such as after model responses, after tool calls, before writes, before validation, before repair, and before final completion.
- Why: The user can steer long-running work without interrupting unsafe half-completed operations.
- Checkout target: implementation checklist
- Acceptance: New user messages are admitted at deterministic checkpoints and never in the middle of a mutating tool call.

### IC-004 - Keep Queue Lanes Explicit
- Status: committed
- Priority: high
- Kind: constraint
- Scope: active-run message semantics
- Point: Support explicit `/append`, `/continue`, `/pause`, `/cancel`, and `/status` lanes instead of guessing message intent automatically.
- Why: Explicit lanes prevent confusing or unsafe behavior when the user sends a message during active execution.
- Checkout target: UX contract
- Acceptance: Active-run messages are classified into clear lanes with visible behavior and no hidden auto-interrupt mode.

### IC-005 - Convert User Messages into Structured Context
- Status: committed
- Priority: high
- Kind: idea
- Scope: operator message admission
- Point: Admitted user messages should become structured updates such as hard constraints, soft preferences, target-path changes, validation requirements, pause/cancel commands, or follow-up objectives.
- Why: Workers need operational context, not another opaque chat blob appended to the prompt.
- Checkout target: implementation plan
- Acceptance: Queue admission produces typed context packets or operator-intent policy updates consumed by the next Super DAN stage.

### IC-006 - Split Long Super DAN Runs into Staged Briefs
- Status: committed
- Priority: high
- Kind: idea
- Scope: Super DAN prompt/runtime design
- Point: Long-running Super DAN tasks should use staged briefs: understand, inspect, plan edits, mutate, validate, repair, and finalize.
- Why: Smaller stages improve steerability, reduce prompt drift, and make progress visible earlier.
- Checkout target: prompt/runtime plan
- Acceptance: Long tasks can execute through multiple compact stages while small tasks may still use one coherent worker call.

### IC-007 - Pass Compact Context Between Stages
- Status: committed
- Priority: high
- Kind: constraint
- Scope: prompt context and performance
- Point: Stages should exchange compact context packets instead of replaying the full transcript or full tool history.
- Why: Smaller API contexts should improve latency and keep each stage focused.
- Checkout target: implementation checklist
- Acceptance: Stage prompts receive concise facts, artifact refs, changed files, validation failures, and operator constraints without unnecessary transcript replay.

### IC-008 - Generate Progress Updates from Events
- Status: committed
- Priority: high
- Kind: idea
- Scope: live progress UX
- Point: Most interim user-visible updates should be deterministic renderings of run events rather than extra LLM narration calls.
- Why: This gives Codex-like responsiveness without spending tokens just to narrate.
- Checkout target: UX implementation plan
- Acceptance: File reads, writes, validation starts, validation results, repairs, queue admissions, and blockers produce concise progress messages from structured events.

### IC-009 - Add User-Visible Checkpoint Summaries
- Status: committed
- Priority: medium
- Kind: follow-up
- Scope: active-run progress UX
- Point: Important checkpoints should summarize what was learned, what changed, what is next, queued messages, and whether the next step may mutate files.
- Why: Users need a mental model of the run and a chance to steer before important transitions.
- Checkout target: UX contract
- Acceptance: Checkpoint summaries appear at phase boundaries without flooding the user with low-level event rows.

### IC-010 - Make Validation Visible
- Status: committed
- Priority: medium
- Kind: idea
- Scope: validation UX
- Point: Validation should be a first-class visible phase that explains what checks are running, what passed, what failed, and whether repair will run.
- Why: Visible self-checking improves trust and makes failed runs easier to debug.
- Checkout target: UX implementation plan
- Acceptance: Super DAN progress shows validation intent, result, repair decision, and recheck status.

### IC-011 - Add Safe Pause and Cancel Semantics
- Status: committed
- Priority: high
- Kind: constraint
- Scope: active-run lifecycle
- Point: Pause and cancel should stop at safe checkpoints, preserve run state, report changed files, and support later resume or continuation.
- Why: Long-running coding work must be interruptible without corrupting workspace or losing audit state.
- Checkout target: lifecycle plan
- Acceptance: `/pause` and `/cancel` do not kill the process blindly and leave a clear resumable or terminal state record.

### IC-012 - Keep Human Queue Separate from Organ Inboxes
- Status: committed
- Priority: medium
- Kind: decision
- Scope: queue architecture
- Point: Human chat messages should live in Chat/Agent V2, while Super DAN hook/inboxes should remain internal organ queues for validation, repair, synthesis, scout, and memory events.
- Why: Mixing human conversation with internal organ packets would make routing and safety harder to reason about.
- Checkout target: architecture note
- Acceptance: Documentation and code boundaries distinguish operator-message queue records from `.dan-super/state/` organ packets.

### IC-013 - Test Prompt and Active-Run UX Contracts
- Status: committed
- Priority: high
- Kind: follow-up
- Scope: regression coverage
- Point: Add tests for rendered prompt snapshots, active-run append/continue/cancel behavior, queued constraints before writes, progress events, and compact staged context.
- Why: Prompt and UX behavior will regress easily without focused tests.
- Checkout target: test checklist
- Acceptance: Regression tests cover prompt review output and the active-run message queue lifecycle.

### IC-014 - Show Planner and Executor Progress Live
- Status: committed
- Priority: medium
- Kind: follow-up
- Scope: planner/executor UX
- Point: Surface live progress for planning and execution, including current plan file, active checklist item, completed ticks, validator status, and queued user messages.
- Why: Users need to see whether the organism is planning, executing, validating, or waiting, without opening raw plan files or event logs.
- Checkout target: UX implementation plan
- Acceptance: A live run can render concise progress from plan/checklist events while the underlying plan files remain the source of truth.

### IC-015 - Keep Skill Invocation To Passive And Dollar Mention
- Status: committed
- Priority: high
- Kind: decision
- Scope: Super DAN skill UX
- Point: Support only passive auto skill selection and active `$skill-name` mentions; do not add a `$skill:name` exact syntax.
- Why: Two invocation levels are enough, keep prompts natural, and avoid an extra command-like grammar that the user does not want.
- Checkout target: UX contract and parser checklist
- Acceptance: `$frontend-design` and `$idea-cart` can bias or force skill selection, passive matching still works, and `$skill:frontend-design` is not documented or treated as a special syntax.

### IC-016 - Suggest Skills After Dollar Sign
- Status: committed
- Priority: medium
- Kind: follow-up
- Scope: skill invocation autocomplete
- Point: When a user types or sends a dollar sign in a supported surface, DAN should suggest matching skills, with `$idea-cart` as the first UX target to exercise.
- Why: Skill discovery should feel native and lightweight without requiring CLI flags or manual installation.
- Checkout target: UI/adapter implementation plan
- Acceptance: Dollar-sign detection can surface skill suggestions from the loaded catalog, including `$idea-cart`, without interfering with normal dollar amounts or shell/env-var text.

## Checkout Log

### 2026-05-11
- IC-001 through IC-016 -> checked out into Plan 57 follow-up docs: `57-10-active-run-operator-steering`, `57-11-progress-and-checkpoint-ux`, `57-12-super-dan-terminal-tui`, and `57-13-skill-mention-ux`.
- IC-015 and IC-016 -> originally carted from skill invocation UX discussion.
- IC-014 -> originally carted from planner/executor progress UX discussion.
- IC-001 through IC-013 -> originally carted from Super DAN UX/message-queue review.
