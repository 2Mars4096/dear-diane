# 57-15: Super DAN TUI Session Transcript

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** completed
**Goal:** Make `dan super-tui` feel like one continuous Codex-style conversation across turns, with `/reset` as the explicit visible transcript boundary and per-run event logs remaining separate.

## Tasks
- [x] 1. Define visible transcript semantics
  - [x] 1-1. Treat a Super DAN TUI turn as an execution/run boundary, not a visual conversation boundary.
  - [x] 1-2. Keep prior user messages, selected skill mentions, commands, assistant progress lines, final summaries, and trace refs visible across new turns.
  - [x] 1-3. Keep each execution trace in its own `.dan-super/runs/turn-XX/events.jsonl` file.
  - [x] 1-4. Distinguish the human-facing transcript from internal hook/inbox state and raw event logs.
  - [x] 1-5. Keep transcript behavior in the TUI/session layer; do not change Super DAN core run semantics except for small descriptive metadata needed for display.
  - [x] 1-6. Preserve enough prior context that the user can understand a new turn without mentally reconstructing earlier runs from event-log paths.
- [x] 2. Add transcript storage and recovery
  - [x] 2-1. Define a small TUI transcript record shape for `user`, `assistant_progress`, `assistant_final`, `system_notice`, and `debug_ref` entries.
  - [x] 2-2. Persist visible transcript records under `.dan-super/tui/` or an equivalent recoverable local state path.
  - [x] 2-3. Store run ids, turn ids, event-log refs, selected skills, and final status as metadata on transcript entries.
  - [x] 2-4. Bound replay on startup so very long sessions stay readable while older transcript records remain recoverable.
- [x] 3. Render history and active runs together
  - [x] 3-1. Render prior transcript history before the active composer when the TUI starts or a new turn begins.
  - [x] 3-2. During a run, show the active narrative timeline from [57-14](57-14-super-dan-tui-conversational-timeline.md).
  - [x] 3-3. On run completion, settle the live progress into the visible transcript as a compact assistant summary plus trace link.
  - [x] 3-4. Avoid clearing the screen or replacing prior conversation just because a new turn starts.
- [x] 4. Define reset behavior
  - [x] 4-1. Make `/reset` the visible transcript boundary: archive/clear TUI transcript state and start a new visible conversation.
  - [x] 4-2. Keep `/reset state` focused on internal `.dan-super/state/` reset and preserve visible transcript unless explicitly documented otherwise.
  - [x] 4-3. Emit a visible reset receipt that names archived transcript and state paths.
  - [x] 4-4. Ensure ordinary new turns never erase prior visible chat history.
- [x] 5. Validate session continuity
  - [x] 5-1. Add tests for two consecutive TUI turns where the second view retains the first user turn and assistant summary.
  - [x] 5-2. Add tests that per-run event logs remain distinct while the visible transcript is continuous.
  - [x] 5-3. Add tests for `/reset` clearing/archiving visible transcript and `/reset state` preserving it.
  - [x] 5-4. Add plain/Rich rendering checks for transcript replay plus active live progress.

## Decisions
- The TUI should feel continuous like Codex: new turns append to a session transcript until reset.
- The transcript is a display/session artifact; `.dan-super/runs/turn-XX/events.jsonl` remains the audit source for each run.
- `/reset` separates human-facing conversations; turn numbers separate execution traces.
- `/reset state` should not silently erase the visible conversation unless the implementation explicitly adds and documents that behavior.
- Super DAN core should remain focused on execution and event emission. Transcript storage, replay, summarization, and reset presentation belong to the TUI/session layer.
- Transcript continuity should support user orientation: after several turns, the visible history should still show the relevant objective, major actions, final outcomes, and trace refs without flooding the terminal.

## Notes
- Checked out from idea-cart items `IC-019` and `IC-020`.
- This plan is complementary to [57-14](57-14-super-dan-tui-conversational-timeline.md): `57-14` defines the live narration stream, while this plan defines how that stream persists across turns.
- 2026-05-11: Implemented visible transcript JSONL storage under `.dan-super/tui/transcript.jsonl`, startup history replay, assistant final summaries, and TUI reset receipts that preserve transcript on `/reset state`.
