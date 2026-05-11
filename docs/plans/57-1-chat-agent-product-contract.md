# 57-1: Chat / Agent Product Contract

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** not-started
**Goal:** Freeze the V2 user-facing contract so every turn is either lightweight Chat or bounded Agent execution.

## Tasks
- [ ] 1. Define the public mode vocabulary
  - [ ] 1-1. Define `chat` as foreground conversational response with no broad orchestration
  - [ ] 1-2. Define `agent` as durable task execution with explicit run lifecycle
  - [ ] 1-3. Remove legacy `ask` / `plan` / `debug` / `auto` mode clutter from visible V2 controls; keep only Chat and Agent while retaining compatible backend mappings where needed
- [ ] 2. Define explicit escalation behavior
  - [ ] 2-1. Let Chat recommend "run as Agent" for large, mutating, long-running, or multi-artifact work
  - [ ] 2-2. Require a separate user gesture before Chat launches Agent work; V2 must not silently auto-escalate
  - [ ] 2-3. Preserve explicit user choice when the user chooses Chat or Agent
- [ ] 3. Define task intent fields
  - [ ] 3-1. Objective text
  - [ ] 3-2. Workspace/artifact target
  - [ ] 3-3. Mutation permission and approval envelope
  - [ ] 3-4. Agent profile (`Fast`, `Balanced`, `Deep`, `Max`)
  - [ ] 3-5. Optional task family hints such as coding, website, research, operator, or workflow, treated as internal routing evidence
  - [ ] 3-6. Surface origin, native thread/message ids, and privacy scope from `SurfaceTurn`
  - [ ] 3-7. Structured attachment refs, including images/figures
  - [ ] 3-8. Topic queue binding: new task, existing task, append to active run, run after current, or control command
- [ ] 4. Define result semantics
  - [ ] 4-1. Chat returns an assistant message
  - [ ] 4-2. Agent returns a task run plus a final assistant summary
  - [ ] 4-3. Agent failures return honest blocker reports with logs and retry affordances

## Decisions
- Product mode count is capped at two in V2: Chat and Agent.
- Visible V2 controls should not include `auto` for Chat/Agent escalation or active-run queue placement. If work will become Agent work or enter a queue, the user chooses that with an explicit action.
- Internal lanes may still be rich, but they are not top-level UI modes.
- The product contract should be independent of whether the first Agent backend uses Super DAN, universal organism, DAN Code, or another implementation.
- Every V2 turn starts from a normalized surface turn before it becomes Chat or Agent work.

## Notes
- Existing backend values such as `ask`, `plan`, `debug`, and `auto` can remain compatibility details under the V1/legacy surface.
- Surface ingress, attachment, queue, and progress details are owned by [57-6](57-6-surface-ingress-and-control-triage.md), [57-7](57-7-thread-task-identity-and-topic-queues.md), [57-8](57-8-structured-attachments-and-vision-inputs.md), and [57-9](57-9-agent-event-hooks-and-telegram-progress.md).
