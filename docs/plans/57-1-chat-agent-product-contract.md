# 57-1: Chat / Agent Product Contract

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** not-started
**Goal:** Freeze the V2 user-facing contract so every turn is either lightweight Chat or bounded Agent execution.

## Tasks
- [ ] 1. Define the public mode vocabulary
  - [ ] 1-1. Define `chat` as foreground conversational response with no broad orchestration
  - [ ] 1-2. Define `agent` as durable task execution with explicit run lifecycle
  - [ ] 1-3. Remove `ask` / `plan` / `debug` / `agent` / `auto` as visible V2 mode clutter; retain compatible backend mappings where needed
- [ ] 2. Define automatic escalation behavior
  - [ ] 2-1. Let Chat recommend "run as Agent" for large, mutating, long-running, or multi-artifact work
  - [ ] 2-2. Define when Chat may auto-escalate vs when it must ask for confirmation
  - [ ] 2-3. Preserve explicit user choice when the user chooses Chat or Agent
- [ ] 3. Define task intent fields
  - [ ] 3-1. Objective text
  - [ ] 3-2. Workspace/artifact target
  - [ ] 3-3. Mutation permission and approval envelope
  - [ ] 3-4. Agent profile (`Fast`, `Balanced`, `Deep`, `Max`)
  - [ ] 3-5. Optional task family hints such as coding, website, research, operator, or workflow, treated as internal routing evidence
- [ ] 4. Define result semantics
  - [ ] 4-1. Chat returns an assistant message
  - [ ] 4-2. Agent returns a task run plus a final assistant summary
  - [ ] 4-3. Agent failures return honest blocker reports with logs and retry affordances

## Decisions
- Product mode count is capped at two in V2: Chat and Agent.
- Internal lanes may still be rich, but they are not top-level UI modes.
- The product contract should be independent of whether the first Agent backend uses Super DAN, universal organism, DAN Code, or another implementation.

## Notes
- Existing backend values such as `ask`, `plan`, `debug`, and `auto` can remain compatibility details under the V1/legacy surface.
