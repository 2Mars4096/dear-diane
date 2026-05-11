# 57-12: Super DAN Terminal TUI

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** not-started
**Goal:** Add a Codex-like terminal UI for Agent/Super DAN work that uses colored panels, live progress, command lanes, and event-log-backed state while remaining a sibling surface to the GUI.

## Tasks
- [ ] 1. Define TUI scope and entry points
  - [ ] 1-1. Add a separate `dan-super-tui` script and `dan super-tui` subcommand.
  - [ ] 1-2. Keep `dan super-organism` stable as the direct CLI runner.
  - [ ] 1-3. Prefer Rich for panels, tables, colors, and live layout; provide a plain fallback when Rich is unavailable.
  - [ ] 1-4. Make the TUI usable in ordinary terminals without requiring a browser or Electron.
- [ ] 2. Build the terminal shell
  - [ ] 2-1. Show workspace, model/profile, active task/run id, queue state, and trace refs in a header/status band.
  - [ ] 2-2. Provide an objective composer with command help for `/append`, `/continue`, `/pause`, `/cancel`, `/status`, `/plan`, `/reset`, `/help`, and `/exit`.
  - [ ] 2-3. Render a live event timeline with color-coded phases for planning, model, tool, write, validation, repair, and completion.
  - [ ] 2-4. Render changed files, artifacts, validation gaps, queued messages, and blockers in stable panels.
- [ ] 3. Reuse shared Agent contracts
  - [ ] 3-1. When a V2 backend/server run is available, send commands through `AgentRunCommand` and render normalized `AgentRunEvent` rows.
  - [ ] 3-2. For local direct Super DAN runs, render the same event-log rows and avoid inventing a separate human queue model.
  - [ ] 3-3. Defer true active-run steering until [57-10](57-10-active-run-operator-steering.md) lands.
  - [ ] 3-4. Keep TUI state recoverable from V2 store records or `.dan-super/runs/**/events.jsonl`.
- [ ] 4. Add terminal UX details
  - [ ] 4-1. Use restrained colors: green for completed, amber for waiting/degraded, red for failed, cyan/blue for active work, muted gray for debug refs.
  - [ ] 4-2. Keep a compact mode for narrow terminals and logs.
  - [ ] 4-3. Make open-log/open-artifact refs copyable as text.
  - [ ] 4-4. Avoid hiding critical failures behind animations or transient status lines.
- [ ] 5. Validate TUI behavior
  - [ ] 5-1. Unit-test event-to-render-state projection without requiring a real terminal.
  - [ ] 5-2. Snapshot-test Rich render output for representative progress states when Rich is installed.
  - [ ] 5-3. Test plain fallback output when Rich is unavailable.
  - [ ] 5-4. Test `dan super-tui --status` or equivalent non-interactive status output.

## Decisions
- The TUI and GUI do not conflict; they are sibling surfaces over shared contracts.
- The TUI should not be a direct patch to the existing `dan super-organism` UX.
- The first TUI slice can render local Super DAN events, but durable active-run steering belongs to V2 command semantics from [57-10](57-10-active-run-operator-steering.md).
- Terminal affordances should feel rich, but the source of truth remains event logs and V2 task/run state.

## Notes
- This plan captures the user request for a Codex-like CLI experience with terminal-native color and panel affordances.
- "HTML features on top of the terminal" maps here to Rich-style layout, color, tables, panels, and live updates rather than browser-rendered HTML.
