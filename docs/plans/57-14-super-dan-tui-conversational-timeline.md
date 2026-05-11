# 57-14: Super DAN TUI Conversational Timeline

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** not-started
**Goal:** Replace the default Super DAN TUI progress projection with a flowing, event-driven conversational timeline while keeping raw telemetry available as a debug view.

## Tasks
- [ ] 1. Define the narration contract
  - [ ] 1-1. Map run, stage, model, tool, write, validation, repair, queue, and completion events to short human progress messages.
  - [ ] 1-2. Keep narration deterministic from structured event rows; do not add LLM calls just to narrate progress.
  - [ ] 1-3. State that narration may infer simple operational summaries from event fields, but must not invent unseen reasoning.
  - [ ] 1-4. Preserve semantic highlighting for paths, tools, skills, model names, statuses, and trace refs.
  - [ ] 1-5. Keep Super DAN core changes minimal: expose only necessary event descriptions, phase labels, public text snippets, or summary fields needed for progressive feedback.
  - [ ] 1-6. Ensure the default flow is sufficient for the user to track what just happened, what is happening now, what changed, and what comes next.
- [ ] 2. Coalesce noisy event streams
  - [ ] 2-1. Group repeated `file_read`, `file_write`, `file_edit`, `list_directory`, and `workspace_check` events into concise progress lines.
  - [ ] 2-2. Summarize `shell_command` starts/completions by command intent, affected paths, exit status, and key output lines.
  - [ ] 2-3. Avoid exposing every model round unless the round marks a new direction, retry, validation gate, repair, or finalization.
  - [ ] 2-4. Keep failures and blockers uncoalesced enough to remain actionable.
- [ ] 3. Render the default TUI as a timeline
  - [ ] 3-1. Add a TUI-only narrative projection layer over existing `.dan-super/runs/**/events.jsonl` rows.
  - [ ] 3-2. Render live updates as appendable timeline messages instead of fixed `Current / Context / Progress / Results` sections.
  - [ ] 3-3. Let the currently active step update in place while completed steps settle into scrollback/history.
  - [ ] 3-4. Keep narrow-terminal and plain-output fallbacks readable without Rich.
- [ ] 4. Keep raw events available
  - [ ] 4-1. Add a debug/raw-events mode or toggle for raw event names, tool ids, model rounds, and trace metadata.
  - [ ] 4-2. Make static event-log replay support both default narrative replay and raw telemetry replay.
  - [ ] 4-3. Ensure debug mode does not become the default TUI view.
- [ ] 5. Validate projection behavior
  - [ ] 5-1. Add unit tests for representative event-to-narrative messages.
  - [ ] 5-2. Add coalescing tests for repeated file reads, shell copy/verify commands, validation, repair, and final completion.
  - [ ] 5-3. Add Rich/plain render snapshots for the conversational timeline.

## Decisions
- The default TUI should feel like a live work conversation, not a telemetry dashboard.
- Raw `organism_log_v1` rows remain the source of truth; the timeline is an output-only projection.
- The narrator should not call the model or mutate core event schemas.
- Debug details stay accessible, but raw event names are not the default reading experience.
- Core Super DAN logic should not be reshaped for this UX. If the TUI needs better text, prefer adding small descriptive fields or renderer-local mapping over changing runner behavior, scheduling, validation, or tool execution.
- Minimal core exposure must not make the TUI vague. The rendered flow needs enough continuity that an operator can reorient after glancing away without opening the raw event log.

## Notes
- Checked out from idea-cart items `IC-017` and `IC-018`.
- This plan supersedes the first one-panel `Current / Context / Progress / Results` projection as the desired TUI direction, while keeping the earlier projection useful as fallback/debug scaffolding.
