# 57-11: Progress and Checkpoint UX

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** not-started
**Goal:** Render live Agent/Super DAN progress, checkpoint summaries, validation state, queue state, and planner/executor progress from normalized events across GUI, TUI, Telegram, and compact CLI surfaces.

## Tasks
- [ ] 1. Freeze the shared progress vocabulary
  - [ ] 1-1. Reuse normalized `AgentRunEvent` values for planning, worker start, tool use, artifact change, validation, repair, queue injection, blocker, and completion.
  - [ ] 1-2. Add any missing fields needed for checkpoint summaries: learned facts, changed files, next step, queued commands, and mutation risk.
  - [ ] 1-3. Keep raw organism logs as debug refs, not the primary user-facing progress API.
- [ ] 2. Add checkpoint summary events
  - [ ] 2-1. Emit summaries at phase boundaries after inspect, plan, mutate, validate, repair, and finalization.
  - [ ] 2-2. Include what changed, what remains, and whether the next step may mutate files.
  - [ ] 2-3. Include queued user messages and whether each is pending, admitted, or waiting for terminal completion.
  - [ ] 2-4. Keep summaries deterministic and compact; do not spend LLM calls just to narrate progress.
- [ ] 3. Make validation visible
  - [ ] 3-1. Show validation intent before the validator runs.
  - [ ] 3-2. Show deterministic and model validation results separately when both exist.
  - [ ] 3-3. Show whether repair will run, has run, or is exhausted.
  - [ ] 3-4. Expose validation artifacts and open-log refs without requiring Development mode.
- [ ] 4. Surface planner and executor progress
  - [ ] 4-1. Render the current run-local plan root and active plan file.
  - [ ] 4-2. Render active checklist item ids, completed ticks, deferred DAG tasks, and current frontier ids.
  - [ ] 4-3. Show worktree task admission, diff application, and final validation status as distinct progress phases.
  - [ ] 4-4. Keep plan files as the source of truth while the UI renders concise projections.
- [ ] 5. Implement surface renderers
  - [ ] 5-1. GUI: enrich the V2 Agent run panel with checkpoint, validation, queue, and artifact sections.
  - [ ] 5-2. TUI: render colored panels/tables over the same event stream.
  - [ ] 5-3. Telegram: keep one compact progress bubble with important phase transitions only.
  - [ ] 5-4. Plain CLI: keep line-oriented output available for logs and non-Rich terminals.
- [ ] 6. Validate progress behavior
  - [ ] 6-1. Test event-to-summary rendering without LLM calls.
  - [ ] 6-2. Test validation and repair states in GUI/TUI/Telegram renderers.
  - [ ] 6-3. Test queued user messages appear in checkpoint summaries.
  - [ ] 6-4. Test branch events render differently from queued continuation work.

## Decisions
- Progress UX is event-driven and deterministic by default.
- GUI and TUI may be richer than Telegram, but all should consume the same event semantics.
- Validation is a first-class phase, not a hidden final check.
- Planner/checklist progress should be visible without asking users to open raw plan files.

## Notes
- Checked out from idea-cart items `IC-008`, `IC-009`, `IC-010`, `IC-014`, and the progress coverage portions of `IC-013`.
- This plan builds on [57-9](57-9-agent-event-hooks-and-telegram-progress.md) and the Super DAN planner/frontier events tracked in [56-6](56-6-super-dan-hooked-organ-inboxes.md).
