# 57-12: Super DAN Terminal TUI

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** in-progress
**Goal:** Add a Codex-like terminal UI for Agent/Super DAN work that uses colored panels, live progress, command lanes, and event-log-backed state while remaining a sibling surface to the GUI.

## Tasks
- [x] 1. Define TUI scope and entry points
  - [x] 1-1. Add a separate `dan-super-tui` script and `dan super-tui` subcommand.
  - [x] 1-2. Keep `dan super-organism` stable as the direct CLI runner.
  - [x] 1-3. Prefer Rich for panels, tables, colors, and live layout; provide a plain fallback when Rich is unavailable.
  - [x] 1-4. Make the TUI usable in ordinary terminals without requiring a browser or Electron.
- [ ] 2. Build the terminal shell
  - [x] 2-1. Show workspace, model/profile, active task/run id, queue state, and trace refs in a header/status band.
  - [x] 2-2. Provide an objective composer with command help for `/append`, `/continue`, `/pause`, `/cancel`, `/status`, `/skills`, `/plan`, `/reset`, `/help`, and `/exit`.
    - [x] The idle composer now renders as a compact boxed `Message` area with a dim helper strip under it, while keeping the actual `super-tui>` prompt available for prompt-toolkit completions.
    - [x] Prompt-toolkit sessions now open a dropdown immediately when the operator types `/` or a bare `$`; readline fallback still provides Tab completion.
  - [x] 2-3. Render a live event timeline with color-coded phases for planning, model, tool, write, validation, repair, and completion.
    - [x] The single `Recent Events` panel now shows a conversational timeline with model, tool, file, validation, blocker, final result, and trace updates rather than fixed `Current / Context / Progress / Results` sections.
    - [x] Rich TUI rows now semantically highlight step labels, file/trace paths, tool names, model names, `$skill-name` mentions, and success/failure/waiting terms without changing event emission.
    - [x] `--raw-events` keeps raw event names/tool metadata available for debug replay.
  - [ ] 2-4. Render changed files, artifacts, validation gaps, queued messages, and blockers in stable panels.
    - [x] Local events now render changed files, artifacts, validation state, queue state, and blockers.
    - [ ] Active-run human queued-message rendering remains blocked on [57-10](57-10-active-run-operator-steering.md).
- [ ] 3. Reuse shared Agent contracts
  - [ ] 3-1. When a V2 backend/server run is available, send commands through `AgentRunCommand` and render normalized `AgentRunEvent` rows.
  - [x] 3-2. For local direct Super DAN runs, render the same event-log rows and avoid inventing a separate human queue model.
  - [x] 3-3. Defer true active-run steering until [57-10](57-10-active-run-operator-steering.md) lands.
  - [x] 3-4. Keep TUI state recoverable from V2 store records or `.dan-super/runs/**/events.jsonl`.
- [x] 4. Add terminal UX details
  - [x] 4-1. Use restrained colors: green for completed, amber for waiting/degraded, red for failed, cyan/blue for active work, muted gray for debug refs.
  - [x] 4-2. Keep a compact mode for narrow terminals and logs.
  - [x] 4-3. Make open-log/open-artifact refs copyable as text.
  - [x] 4-4. Avoid hiding critical failures behind animations or transient status lines.
- [x] 5. Validate TUI behavior
  - [x] 5-1. Unit-test event-to-render-state projection without requiring a real terminal.
  - [x] 5-2. Snapshot-test Rich render output for representative progress states when Rich is installed.
  - [x] 5-3. Test plain fallback output when Rich is unavailable.
  - [x] 5-4. Test `dan super-tui --status` or equivalent non-interactive status output.

## Decisions
- The TUI and GUI do not conflict; they are sibling surfaces over shared contracts.
- The TUI should not be a direct patch to the existing `dan super-organism` UX.
- The first TUI slice can render local Super DAN events, but durable active-run steering belongs to V2 command semantics from [57-10](57-10-active-run-operator-steering.md).
- Terminal affordances should feel rich, but the source of truth remains event logs and V2 task/run state.
- The first implementation wraps the existing Super DAN runner through opt-in progress-renderer and live-report hooks instead of changing the default `dan super-organism` output.
- Until the full checkpoint/progress vocabulary lands, the Rich TUI intentionally renders only a compact header and one `Recent Events` panel.
- Skill semantics belong below the TUI. The TUI can autocomplete, disambiguate, and pass selected skill tokens, but actual skill packet injection and preflight execution live in the shared invocation layer plus Super DAN runner.
- The next TUI UX direction is split into follow-up plans: [57-14](57-14-super-dan-tui-conversational-timeline.md) for flowing conversational progress and [57-15](57-15-super-dan-tui-session-transcript.md) for Codex-like visible transcript continuity across turns.

## Notes
- This plan captures the user request for a Codex-like CLI experience with terminal-native color and panel affordances.
- "HTML features on top of the terminal" maps here to Rich-style layout, color, tables, panels, and live updates rather than browser-rendered HTML.
- 2026-05-11: First TUI slice landed with `src/dan/cli/super_tui.py`, `dan super-tui`, `dan-super-tui`, Rich/plain live rendering, static `--event-log` replay, interactive objective entry, and focused CLI/projection/fallback tests.
- 2026-05-11: Adjusted interactive rendering so the objective prompt appears below the idle panel, and direct one-shot `dan super-tui "<objective>"` mirrors `dan super-organism` execution-mode selection unless the operator passes `--live`.
- 2026-05-11: Added prompt-toolkit command and skill suggestion menus for `/` and `$`, plus `/skills [filter]` as a terminal picker fallback.
- 2026-05-11: Collapsed the Rich layout to `Recent Events` only for now, added safe TUI handling for bare `$skill` mentions, and made direct local `/append` / `/continue` / `/pause` / `/cancel` messages explicit about the missing V2 steering backend.
- 2026-05-11: Reworked the idle input area into a boxed message composer with a shaded helper line, and let selected `$skill-name` mentions appear in Recent Events when a run starts.
- 2026-05-11: Kept the one-panel live layout but expanded the panel content from terse event labels to step descriptions, progress details, concrete tool results, validation/blocker summaries, and final trace refs.
- 2026-05-11: Adjusted skill handling so `dan super-tui` is a selection/autocomplete surface only; it delegates `$skill-name` parsing to `src/dan/skills/invocation.py` and passes selected-skill metadata to the runner.
- 2026-05-11: Fixed prompt completions so typing `/` opens command suggestions and typing bare `$` opens the skill dropdown with a visible terminal color highlight.
- 2026-05-11: Fixed a prompt-toolkit import compatibility issue that made the dropdown path fall back to readline in environments where `CompleteStyle` lives under `prompt_toolkit.shortcuts`.
- 2026-05-11: Added output-only Rich semantic highlighting for paths, tools, skills, model names, and status terms in the existing `Recent Events` panel; plain output remains unchanged.
- 2026-05-11: Follow-up plans split the next desired TUI behavior out of this base shell plan: conversational event narration in `57-14` and persistent visible transcript/reset semantics in `57-15`.
- 2026-05-11: `57-14` and `57-15` landed in the TUI layer: progress now renders as a conversational timeline with raw debug replay, and interactive TUI sessions replay visible transcript history from `.dan-super/tui/transcript.jsonl` before the composer.
- 2026-05-12: `io-similarity` trace review showed that free-text interactive TUI turns can over-promote tiny/read-only requests into live planner/build runs; the earlier todo-specific fast path was reverted in favor of designing a generic intent gate.
