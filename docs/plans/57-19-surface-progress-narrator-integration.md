# 57-19: Surface Progress Narrator Integration

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** in-progress
**Goal:** Wire the core narrator lane into TUI, GUI, and CLI surfaces so progress questions get immediate visible feedback plus a later model-written answer without starting executor work.

## Tasks
- [x] 1. Route progress questions to the narrator lane
  - [x] 1-1. Route `current progress`, `what is happening`, `status`, `where are we`, `/progress`, `/last`, and similar status questions to `narrator read-only` when they refer to the current or recent run.
  - [x] 1-2. Keep workspace inspection requests such as `summarize docs/todo.md` in `executor read-only`.
  - [x] 1-3. Keep mutation requests such as `update the todo list` and `continue building this` in `executor write`.
  - [x] 1-4. Ask one short clarification when a message could reasonably mean either status narration or new executor work.
- [ ] 2. Make the TUI feel responsive before the model answer arrives
  - [x] 2-1. Immediately append a visible transcript line for the user question.
  - [x] 2-2. Immediately render a deterministic status line such as `Progress summary requested from the current run snapshot.`
  - [x] 2-3. Show a `Narrator: running` state and the existing elapsed-time footer while the narrator call is in flight.
  - [ ] 2-4. Keep executor progress events updating in the same panel while narration is pending.
  - [x] 2-5. Replace or append the deterministic fallback with the narrator answer once it completes.
- [x] 3. Render narrator answers as conversation, not telemetry
  - [x] 3-1. Put the narrator answer near the top under `Answer:` or `Narration:` so it is not buried below activity rows.
  - [x] 3-2. Keep raw event/tool/provider rows hidden unless `--raw-events` is enabled.
  - [x] 3-3. Highlight statuses, elapsed time, active phase, changed paths, artifacts, validation outcomes, blockers, and trace refs.
  - [x] 3-4. Bound the answer to a compact operator summary with optional bullets for current state, latest completed step, active work, blockers, and next expected event.
  - [x] 3-5. Persist narrator questions and answers into the flat same-session transcript until `/reset`.
- [ ] 4. Share the behavior across surfaces
  - [ ] 4-1. Expose the same narrator request/result events to the GUI as a chat bubble or progress card.
  - [x] 4-2. Expose a plain CLI path for progress/status narration without requiring the full TUI renderer.
  - [ ] 4-3. Keep Telegram or other messaging surfaces eligible to consume the same core narrator events later without a separate prompt contract.
  - [x] 4-4. Use surface-specific rendering only for layout, color, and interaction, not for narrator safety or prompt semantics.
- [ ] 5. Handle edge cases
  - [x] 5-1. If no run exists, answer from transcript/session state and say there is no active run.
  - [x] 5-2. If the last run is completed, explain final state and elapsed time rather than showing `running`.
  - [x] 5-3. If a run is active but no model provider is available, show the deterministic snapshot summary immediately.
  - [x] 5-4. If the narrator answer becomes stale, label it as based on an older snapshot or discard it in favor of the newer one.
  - [x] 5-5. Preserve `/reset` as the transcript and narrator-history boundary.
- [ ] 6. Add regression coverage
  - [x] 6-1. `tell me current progress` shows the user question immediately.
  - [x] 6-2. `tell me current progress` does not start executor read-only or write work.
  - [x] 6-3. The TUI emits immediate deterministic feedback before the narrator model response.
  - [x] 6-4. The narrator response updates the visible answer area when it completes.
  - [ ] 6-5. Existing executor progress remains visible while the narrator is pending.
  - [x] 6-6. Flat transcript replay includes narrator turns across same-session prompts.
- [x] 7. Update docs
  - [x] 7-1. Update TUI help/README language with the narrator lane and progress commands.
  - [x] 7-2. Add changelog and todo updates when implementation lands.

## Decisions
- Surface layers own responsiveness and presentation: transcript placement, loading states, color, highlighting, and layout.
- Core owns narrator safety, prompt contract, snapshot semantics, event names, and stale/fallback behavior.
- Progress questions should not wait silently for a model call. A deterministic snapshot summary appears first, then the model-written answer can refine it.
- The default TUI should keep the current single-panel structure, but change what it shows: the answer first, compact status only, and trace/log detail only under `--raw-events`.

## Notes
- This plan depends on [57-18](57-18-core-progress-narrator-layer.md) for the reusable narrator contract.
- The immediate problem to fix is the observed `tell me current progress?` turn: no visible feedback for several seconds and no prominent answer text.
- TUI and GUI should stay sibling surfaces over the same narrator API. The narrator must not become TUI-only.
- 2026-05-12: First TUI/plain integration landed. `tell me current progress?`, `/progress`, and `/last` route to `narrator read-only`, render `Answer:` before activity, read the latest `.dan-super` event log or transcript snapshot, optionally ask a model with no tools when a model is explicitly available, and persist `assistant_narrator` transcript entries. GUI/Telegram consumption and true concurrent active-run narration remain open.
- 2026-05-12: TUI model narration now uses the core async narrator job path and renders stale narrator answers as explicit result lines. The remaining TUI gap is accepting fresh operator input while a direct local live executor run is still occupying the prompt.
- 2026-05-12: Default narrator presentation was tightened after live feedback: raw workspace-check payloads and narrator lifecycle plumbing are hidden from normal `Activity`, fallback answers use human progress labels, and Rich narrator mode titles the panel `Progress` instead of `Recent Events`.
- 2026-05-12: Default narrator presentation was tightened again after terminal smoke feedback. The normal TUI narrator view now suppresses context/trace/result telemetry, answers next-step questions with `Next:` first, strips markdown/log formatting from model answers, and leaves detailed logs behind `--raw-events`.
