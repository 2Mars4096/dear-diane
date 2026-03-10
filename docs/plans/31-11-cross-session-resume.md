# 31-11: Cross-Session Resume

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** in-progress
**Goal:** Enable seamless task resumption across sessions — "I started a literature review yesterday, continue where I left off" works without the user reconstructing context.

## Problem

DAN has engine-level checkpoints for workflow runs and conversation memory (26-3) for chat history, but no **task-level resume** that bridges the gap. When a user returns after hours or days, they must re-explain what they were working on. The `Project`/`Task` scope (25-6) tracks the right entities but doesn't persist enough structured state to enable one-command resume.

## Tasks

- [x] 1. **Task state model**
  - [x] 1-1. Extend `Task` model with structured resume fields: `status: Literal["active", "paused", "blocked", "completed"]`, `completed_steps: list[str]`, `pending_steps: list[str]`, `current_blocker: str | None`, `artifacts: dict[str, str]` (key → file path or memory ID), `last_activity: datetime`
  - [ ] 1-2. Auto-populate on task completion/pause: concierge extracts structured state from the conversation and persists it (deferred to integration)
  - [x] 1-3. `TaskSnapshot` — serializable summary for quick-resume prompt injection

- [x] 2. **Resume protocol**
  - [x] 2-1. On session start (any surface), check for active/paused tasks in `ProjectStore`
  - [x] 2-2. Quick-resume prompt: "You have an active task: '{task_name}' (started {time_ago}). {completed_count} steps done, {pending_count} remaining. Current state: {snapshot}. Continue?"
  - [x] 2-3. `/resume` command: explicitly resume the most recent task; `/resume <task_name>` for specific task
  - [x] 2-4. Auto-resume: only auto-attach when there is exactly one high-confidence candidate **and** the user message has resume-like intent (semantic similarity > threshold plus continuation cues such as "continue", "pick up", "where were we")
  - [x] 2-5. Ambiguity handling: if multiple plausible tasks exist, show the quick-resume prompt instead of auto-attaching; if the user says "start new" or dismisses the prompt, suppress auto-attach for that session

- [x] 3. **State persistence**
  - [x] 3-1. Persist task state to `ProjectStore` on every significant state change (step completion, artifact creation, blocker detection)
  - [ ] 3-2. Compact task history: keep full detail for last 3 steps, summaries for older steps (deferred to integration)
  - [x] 3-3. Artifact references: link to workflow run IDs, file paths, memory items — don't duplicate data, reference it

- [x] 4. **Tests and docs**
  - [x] 4-1. Unit tests: task state serialization, resume prompt generation, auto-resume matching (48 tests)
  - [ ] 4-2. Integration test: start task on one surface, resume on another (deferred to integration)
  - [x] 4-3. Update architecture, changelog

## Dependencies

- `ProjectStore` / `Task` model (25-6) for task persistence
- `ConversationMemoryStore` (26-3) for history
- `Concierge` for session-start hooks
- `UserProfile` (26-3) for recent workflow tracking

## Estimate

1.5 days

## Primary Files

- `src/dan/server/concierge/models.py` — `Task` model extension with resume fields (modify)
- `src/dan/server/concierge/resume.py` — `ResumeProtocol`, auto-resume matching, quick-resume prompt generation (new)
- `src/dan/server/concierge/project_store.py` — state persistence hooks (modify)
- `src/dan/server/concierge/runtime.py` — session-start resume check wiring (modify)
- `src/dan/server/capability_handlers.py` — `/resume` command handler (modify)

## Notes

- The key insight is that resume needs structured state (completed steps, pending steps, blockers), not just conversation history. Raw chat history is too verbose and loses structure.
- Auto-resume (2-4) is the premium UX: user just starts talking about their task, and DAN seamlessly picks up where it left off without requiring explicit commands.
- Auto-resume must be conservative. False positives are worse than missed resumes, so ambiguous matches fall back to prompt-first behavior.
- **State extraction cost:** Task 1-2 (auto-populate structured state) likely requires an LLM call per task completion/pause to extract `completed_steps`, `pending_steps`, and `current_blocker` from conversation context. Cost is ~1 LLM call per task lifecycle event (not per message). Document this as a known cost.
- **Registry note (31-16):** Register `/resume` via `CommandDescriptor` if 31-16 has landed. Otherwise, add to `_FAST_COMMAND_PREFIXES` with a TODO for registry migration.
