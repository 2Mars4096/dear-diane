# 49: Concierge Service Hardening

**Status:** in-progress
**Goal:** Make the concierge an always-available intake and supervision layer by introducing first-class task tracking above session trees, background dispatch that does not monopolize chat, task-scoped clarification/status contracts, and backend observability surfaces for future task-card and dispatcher-dashboard UX.

## Problem

The concierge foundations are much stronger after the rewrite and the 38/41 follow-up hardening, but the next product gap is now clearer:

- the durable unit of work is still too message- and session-shaped instead of task-shaped
- same-project execution still feels more blocking than it should because availability is controlled at the dispatcher/session layer, not at a first-class task layer
- pending clarification, retry, and queue behavior are still easier to reason about as project/message flow than as explicit task ownership
- progress exists, but it is still closer to "a stream is active" than "this task is running, paused, or done"
- operational visibility exists in traces and telemetry, but there is no clean backend contract for a manager-style dispatcher dashboard yet

The next hardening slice should therefore stay in concierge and control-plane code, not frontend code. The goal is to make the concierge behave like the always-on manager of work before any task-card or dashboard UI is built on top.

## Scope

In scope:

- a first-class task registry and task lifecycle above root sessions
- background dispatch semantics that let the concierge acknowledge work and return immediately
- task-aware ownership rules for clarification, retry, supersession, and follow-up turns
- a task-centric status, attention, and notification contract
- backend snapshot/stream contracts for future task-card and dispatcher-manager dashboard UX
- focused regressions and acceptance scenarios for multi-task concierge behavior

Out of scope:

- frontend task-card implementation or dashboard UI
- renumbering internal `SessionTier` enums or rewriting the existing tier executor architecture from scratch
- replacing Worker/linter foundations or reopening plans 46/47 as the home for this work
- broad workflow-generation hardening already tracked under the 48-series plans

## Sub-Plans

| # | Sub-Plan | Scope | Priority | Status |
|---|----------|-------|----------|--------|
| [49-1](49-1-task-registry-and-lifecycle.md) | Task Registry and Lifecycle | Add a first-class task model and registry above sessions | P1 | not-started |
| [49-2](49-2-background-dispatch-and-concierge-availability.md) | Background Dispatch and Concierge Availability | Make the concierge acknowledge and dispatch backgroundable work without blocking new input | P1 | not-started |
| [49-3](49-3-clarification-pause-resume-and-task-ownership.md) | Clarification, Pause/Resume, and Task Ownership | Bind follow-up turns and pending actions to explicit tasks | P1 | not-started |
| [49-4](49-4-status-attention-and-notification-contract.md) | Status, Attention, and Notification Contract | Define task-aware status summaries, attention rules, and notification semantics | P1 | not-started |
| [49-5](49-5-dispatcher-observability-and-dashboard-backend-contract.md) | Dispatcher Observability and Dashboard Backend Contract | Expose backend snapshot/stream contracts for manager-style inspection and future dashboard UX | P2 | not-started |

## Dependencies / Sequencing

Recommended execution order:

```text
49-1 (Task Registry and Lifecycle)
  └→ draft 49-5 snapshot/event schemas early so model fields do not drift
  ↓
49-2 (Background Dispatch and Concierge Availability)
  ├→ 49-3 (Clarification, Pause/Resume, and Task Ownership)
  └→ 49-4 (Status, Attention, and Notification Contract)
       ↓
49-5 (Dispatcher Observability and Dashboard Backend Contract)
```

Rationale:

- the task model must exist before background dispatch, pause/resume, or manager-facing visibility can be coherent
- background dispatch changes the concierge's availability contract, so clarification and notification behavior should be tightened against that newer model, not the old project-queue model
- the dashboard/backend contract should be shaped by the actual task registry and state machine rather than exposing today's lower-level session and websocket plumbing directly

## Success Criteria

- the concierge can accept a backgroundable task, acknowledge it immediately, and remain available for new user input
- non-trivial work is represented as a durable task with stable identity and explicit lifecycle state
- clarification, retry, and supersession behavior attaches to task ownership rather than loose project/message heuristics
- `/status` and natural-language status queries can summarize active, paused, completed, and failed tasks coherently
- the backend exposes a stable task/dispatcher snapshot and event contract suitable for future task cards and a manager dashboard
- the existing session tree remains available as an execution trace, but tasks become the primary control-plane object

## Decisions

- **Task is the user-facing control-plane object.** Session trees remain execution traces and execution envelopes.
- **Concierge stays always-on in product semantics.** This plan does not require renumbering internal tiers; it hardens availability and ownership behavior first.
- **Task cards and dashboard UX are downstream consumers.** This plan defines their backend contract but does not implement frontend surfaces yet.
- **Serialization moves down a level.** Project-level blocking should narrow toward task-level ownership and resource policy, not remain the default intake rule.

## Notes

- This plan is the backend-only continuation of the concierge hardening line that already passed through plans 34, 38-8, and 41-3.
- The intended future UX is still consistent with the "one concierge, many workers" model, but this plan deliberately starts with backend truth and lifecycle contracts before any visual redesign.
- The "dispatcher manager dashboard" idea is explicitly preserved here as a backend-contract target so later UI work can consume a deliberate shape instead of reverse-engineering traces, queues, and websocket state.
