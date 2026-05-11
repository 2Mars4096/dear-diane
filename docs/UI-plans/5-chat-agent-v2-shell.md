# 5: Chat / Agent V2 Shell

**Status:** completed
**Goal:** Evolve the lightweight V2 frontend into a two-mode shell that can run normal Chat turns and durable Agent tasks without wiring the old heavy editor modes into the V2 happy path.

## Tasks
- [x] 1. Replace legacy mode selector with a two-choice control
  - [x] 1-1. Show `Chat` and `Agent` as the only primary V2 modes
  - [x] 1-2. Keep old backend mode values hidden behind compatibility mapping
  - [x] 1-3. Preserve a clear return path to the classic editor
- [x] 2. Add Agent profile controls
  - [x] 2-1. Add `Fast`, `Balanced`, `Deep`, and `Max`
  - [x] 2-2. Show resolved profile summary in compact language
  - [x] 2-3. Avoid exposing raw agent count as the main control
- [x] 3. Add Agent run display
  - [x] 3-1. Show task status, current phase, worker/activity rows, validation, repair, and final report
  - [x] 3-2. Support stop, retry, and open-log affordances
  - [x] 3-3. Link Agent run summaries back into the chat thread
  - [x] 3-4. Render queue state and follow-up binding when a turn is attached to an active task
  - [x] 3-5. While an Agent run is active, expose two separate composer actions and keyboard shortcuts: `Enter` appends to the current run at the next checkpoint, and `Cmd/Ctrl+Enter` queues the message to run after the current run completes; `Shift+Enter` remains newline
  - [x] 3-6. Do not expose an `Auto` queue mode; the default visible state should make the selected queue lane explicit before send
- [x] 4. Add reconnect and persistence UX
  - [x] 4-1. Restore active Agent run after reload
  - [x] 4-2. Show historical Agent runs in thread history without requiring Development mode
  - [x] 4-3. Handle backend unavailable states cleanly
- [x] 5. Keep V2 visually light
  - [x] 5-1. Keep a left history rail, one central conversation/task surface, and one composer
  - [x] 5-2. Do not embed the graph editor, Monaco, PDF, or mode-specific panels in the V2 path
  - [x] 5-3. Keep bundle impact measurable through existing build/bundle checks
- [x] 6. Add structured input and surface parity affordances
  - [x] 6-1. Show attachment chips for files, PDFs, images, and figures in the composer and thread
  - [x] 6-2. Show task refs and artifact refs without requiring Development mode
  - [x] 6-3. Render the same normalized Agent events that Telegram receives as progress updates
  - [x] 6-4. Keep Telegram/remote-surface-origin turns visually identifiable without exposing raw adapter internals
- [x] 7. Make branching and thread lineage visually unambiguous
  - [x] 7-1. Show a branch tree or branch rail when a user forks from a prior turn, retry, or "try separately" action
  - [x] 7-2. Label each branch with parent thread, branch point, active task/run state, and latest artifact/result summary
  - [x] 7-3. Keep the active branch visually distinct from sibling branches and from queued continuation items
  - [x] 7-4. Preserve clear navigation back to the parent thread and branch point

## Decisions
- V2 remains a chat/task product shell, not a second full IDE.
- Classic modes remain accessible but are not part of the V2 primary workflow.
- Agent run internals should be inspectable but quiet by default.
- Attachments and Agent events should render from the same V2 contracts used by messaging surfaces.
- Active-run follow-ups use explicit user intent. The composer must offer two separate send gestures for checkpoint append versus continue-after-current, with no auto-selection mode; the initial V2 keymap is `Enter` for append, `Cmd/Ctrl+Enter` for run-after-current, and `Shift+Enter` for newline.
- Branching must be obvious in the thread UI; users should never confuse a sibling branch with the main active thread or with queued follow-up work.

## Notes
- 2026-04-30 follow-up: Closed the remaining frontend-side V2 shell gaps. The shell now has `Fast` / `Balanced` / `Deep` / `Max` Agent profiles wired into execution `profile_policy`, task history and per-thread task/artifact status in the rail, reload reconnect for active Agent runs, retry and open-log controls, backend-offline disablement, composer/thread attachment chips, message-level task/artifact refs, remote-surface origin labels, and branch creation from any message with parent/branch-point lineage shown in the rail.
- 2026-04-30: The V2 frontend now exposes only `Chat` and `Agent`, maps those labels to backend-compatible request modes internally, creates and executes Agent runs through `/api/v2/agent-runs`, streams normalized run events over `WS /api/v2/agent-runs/{run_id}/events`, persists `task_run_ref` on chat messages, and renders explicit active-run queue lanes. During an active Agent run, `Enter` appends at the next checkpoint, `Cmd/Ctrl+Enter` queues after the current run, and the footer buttons mirror those two separate lanes.
- 2026-04-29: The V2 frontend API adapter now posts V2 shell messages to `/api/v2/chat/message` and accepts the compact `v2_control_plane` task/run metadata while the classic editor chat remains on `/api/chat/message`.
- This UI plan is a companion to [../plans/57-chat-agent-v2-control-plane.md](../plans/57-chat-agent-v2-control-plane.md). Backend contract work lives in the `57-*` plan stack.
