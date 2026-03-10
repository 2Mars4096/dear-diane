# 31-11: Cross-Session Resume

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** not-started
**Goal:** Enable seamless task resumption across sessions — "I started a literature review yesterday, continue where I left off" works without the user reconstructing context.

## Problem

DAN has engine-level checkpoints for workflow runs and conversation memory (26-3) for chat history, but no **task-level resume** that bridges the gap. When a user returns after hours or days, they must re-explain what they were working on. The `Project`/`Task` scope (25-6) tracks the right entities but doesn't persist enough structured state to enable one-command resume.

## Tasks

- [ ] 1. **Task state model**
  - [ ] 1-1. Extend `Task` model with structured resume fields: `status: Literal["active", "paused", "blocked", "completed"]`, `completed_steps: list[str]`, `pending_steps: list[str]`, `current_blocker: str | None`, `artifacts: dict[str, str]` (key → file path or memory ID), `last_activity: datetime`
  - [ ] 1-2. Auto-populate on task completion/pause: concierge extracts structured state from the conversation and persists it
  - [ ] 1-3. `TaskSnapshot` — serializable summary for quick-resume prompt injection

- [ ] 2. **Resume protocol**
  - [ ] 2-1. On session start (any surface), check for active/paused tasks in `ProjectStore`
  - [ ] 2-2. Quick-resume prompt: "You have an active task: '{task_name}' (started {time_ago}). {completed_count} steps done, {pending_count} remaining. Current state: {snapshot}. Continue?"
  - [ ] 2-3. `/resume` command: explicitly resume the most recent task; `/resume <task_name>` for specific task
  - [ ] 2-4. Auto-resume: if user's message clearly relates to an existing task (semantic similarity > threshold), auto-attach to that task without asking

- [ ] 3. **State persistence**
  - [ ] 3-1. Persist task state to `ProjectStore` on every significant state change (step completion, artifact creation, blocker detection)
  - [ ] 3-2. Compact task history: keep full detail for last 3 steps, summaries for older steps
  - [ ] 3-3. Artifact references: link to workflow run IDs, file paths, memory items — don't duplicate data, reference it

- [ ] 4. **Tests and docs**
  - [ ] 4-1. Unit tests: task state serialization, resume prompt generation, auto-resume matching
  - [ ] 4-2. Integration test: start task on one surface, resume on another
  - [ ] 4-3. Update architecture, changelog

## Dependencies

- `ProjectStore` / `Task` model (25-6) for task persistence
- `ConversationMemoryStore` (26-3) for history
- `Concierge` for session-start hooks
- `UserProfile` (26-3) for recent workflow tracking

## Estimate

1.5 days

## Notes

- The key insight is that resume needs structured state (completed steps, pending steps, blockers), not just conversation history. Raw chat history is too verbose and loses structure.
- Auto-resume (2-4) is the premium UX: user just starts talking about their task, and DAN seamlessly picks up where it left off without requiring explicit commands.
