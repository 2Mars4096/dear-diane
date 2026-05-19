# 58-6: TUI Background Board

**Parent:** [58-async-core-and-background-runtime](58-async-core-and-background-runtime.md)
**Status:** completed
**Goal:** Give `dan super-tui` / terminal Agent surfaces a clear, structured board for the async-core proof: users can see what is happening at a glance, submit task B while task A is running, immediately see whether it started in parallel, queued behind A, appended to A, or needs clarification, and interfere through explicit commands without opening raw logs or reading redundant status chatter.

## Tasks
- [x] 1. Define the TUI board layout for the MVP
  - [x] 1-1. Add a compact `Tasks` / `Board` section that lists active and queued task ids, short objective labels, phase/status, elapsed time, and the current meaningful action.
  - [x] 1-2. Show the latest admission decision inline and prominently: `started in parallel`, `queued behind <task>`, `appended to <run>`, or `needs clarification`.
  - [x] 1-3. Show one-line dependency/conflict reasons when queued, especially target-path overlap.
  - [x] 1-4. Keep raw event logs behind `--raw-events`; the default board should be human-readable.
  - [x] 1-5. Use stable sections in this order: `Answer/Admission`, `Board`, `Progress`, `Intervene`, then optional `Outcome`.
  - [x] 1-6. Avoid stacked large panels; the default terminal view should fit normal laptop terminals without hiding the prompt.
- [x] 2. Keep input live while background runs continue
  - [x] 2-1. The prompt remains available after task A is admitted to background execution.
  - [x] 2-2. Submitting task B calls the foreground admission controller instead of blocking on task A.
  - [x] 2-3. The TUI prints an immediate acknowledgement before any executor result for task A is required.
  - [x] 2-4. The visible transcript records both the user turn and the admission result.
- [x] 3. Add board commands
  - [x] 3-1. `/tasks` renders active, queued, and recent completed tasks.
  - [x] 3-2. `/status [task|run]` renders compact status for one item or the board.
  - [x] 3-3. `/new <objective>` forces a separate task when ordinary admission might append.
  - [x] 3-4. `/append <text>` targets the focused/running task when safe.
  - [x] 3-5. `/cancel [task|run]` records stop intent and shows checkpoint-pending state.
  - [x] 3-6. `/pause [task|run]` and `/resume [task|run]` are either implemented or honestly shown as unavailable for the current backend.
  - [x] 3-7. `/focus <task|run>` selects the default intervention target once multiple active rows exist.
  - [x] 3-8. Every intervention command returns a visible board update or a precise reason it cannot be applied.
- [x] 4. Integrate narrator output without mixing authorities
  - [x] 4-1. Narrator reports summarize board state by task/run lane, not as one blended stream.
  - [x] 4-2. Narrator may explain why a task queued or started in parallel, but cannot change the decision.
  - [x] 4-3. Quiet heartbeats mention the active background run and queued work only when the snapshot changes materially; unchanged snapshots should not produce filler.
  - [x] 4-4. Final answers attach to the correct task/run row.
  - [x] 4-5. Suppress duplicate model/tool telemetry when a concise board or narrator update already communicates the same fact.
- [x] 5. Handle small terminal constraints
  - [x] 5-1. Fit the board in narrow terminals by truncating objective labels and preserving ids/status.
  - [x] 5-2. Avoid stacked giant panels; use one compact board plus the existing answer/progress stream.
  - [x] 5-3. Use stable row ordering: active first, queued second, recent completed last.
  - [x] 5-4. Keep status colors/icons optional so plain mode remains readable.
- [x] 6. Define noise and importance rules
  - [x] 6-1. Show only meaningful state changes by default: admission, run start, first material action, mutation, validation, blocker, intervention, terminal result.
  - [x] 6-2. Coalesce repetitive `model requested/completed`, unchanged heartbeats, and repeated tool summaries into one progress line.
  - [x] 6-3. Keep elapsed time visible without printing a new status row every tick.
  - [x] 6-4. Make raw event replay opt-in through `--raw-events` or an explicit debug command.
  - [x] 6-5. Preserve enough detail for trust: task ids, run ids, changed paths, blockers, validation status, and trace refs.
- [x] 7. Add TUI regressions
  - [x] 7-1. While task A is marked running, submitting disjoint task B renders `started in parallel` before A completes.
  - [x] 7-2. While task A owns `script.py`, submitting another `script.py` task renders `queued behind` with the path conflict reason.
  - [x] 7-3. `/tasks` shows active and queued rows without raw event payloads.
  - [x] 7-4. Narrator board summaries include separate lanes for A and B.
  - [x] 7-5. Final answer text for A does not overwrite or masquerade as B's answer.
  - [x] 7-6. Repeated unchanged heartbeat/model-wait snapshots do not add redundant default-visible rows.
  - [x] 7-7. `/cancel` or `/append` changes the visible board state immediately or reports the exact safe-checkpoint limitation.
  - [x] 7-8. Narrow-terminal rendering keeps task ids/status/action visible and does not push the prompt off-screen.

## Decisions
- The TUI board is a projection of the durable task board, not a second state store.
- The first TUI version should privilege clarity over dense dashboard behavior.
- Board rows should use stable task/run ids because short objective labels can be ambiguous.
- Direct local TUI mode uses the same V2 admission/store/backend contracts in-process; server transport is optional for shared multi-surface runs, not required for the default terminal workflow.
- User intervention is a first-class part of the TUI, not a debug escape hatch.
- Default rendering should be signal-first. Raw telemetry is still available, but the normal user should see decisions, progress, blockers, and outcomes rather than every internal event.
- The initial board-rendering slice lived in `src/dan/cli/super_tui.py` as a projection over observed events plus future `tui.board.admission` / `super.task.*` admission events; the final slice now routes interactive executor-write turns through V2 async admission.
- Direct-local `/focus`, `/append`, and `/cancel` now persist display-side board intervention events and keep their checkpoint limitation explicit; actual live injection/cancellation belongs to active async runs at safe checkpoints.
- Model-routed executor-write turns in interactive TUI sessions use local in-process V2 async admission by default. They print the admission board immediately, persist the user/admission transcript pair, and return to `super-tui>` while the local V2 backend continues in the background. Passing `--server` switches only the transport to `/api/v2/agent-runs/admit`.

## Notes
- This sub-plan is split out from `58-5` because the terminal is the fastest place to prove the async-core UX before GUI/Telegram polish.
- The desired demo flow is:
  - user: `build script 1 as script_a.py`
  - TUI: `Task A accepted; running in background`
  - user: `also build script_b.py`
  - TUI: `Task B accepted; started in parallel with Task A`
  - `/tasks` shows both active rows with separate phases and trace refs.
- The board should answer three operator questions without further commands: what is running, what is queued, and what can I do now?
- Implemented first slice: `TuiBoardRow`, board projection/update helpers, compact plain/Rich rendering, `/tasks`, `/status [task]`, `/new <objective>`, honest `/append`/`/continue`/`/pause`/`/resume`/`/cancel`/`/focus` limitations, and focused TUI regressions.
- Implemented second slice: stable `Admission` / `Board` / `Progress` / `Intervene` sections, persisted TUI board interventions, focused default targets, checkpoint-pending append/cancel rows, lane-separated narrator snapshot context, target-row final-answer attachment, and regressions for duplicate model-wait suppression.
- Implemented final slice: interactive TUI can submit foreground turns to local V2 async admission by default, show immediate background/queue/append/status acknowledgement, keep the prompt live, and route `/append` / `/pause` / `/resume` / `/cancel` through the same async control path when enabled. `--server` remains available for shared HTTP-backed admission.
- 2026-05-19 follow-up: the task board remains available through `/tasks`, `/status`, and explicit board formatters, but default live output no longer auto-prints `Session` / `Board` panels or inline board snapshots. Narrator/progress text now gets generic semantic de-duplication before rendering so adjacent updates do not restate the same work.
- 2026-05-19 chatbox follow-up: prompt-toolkit terminals now render the active input as a bottom bordered `DAN · Chat` composer and skip the large static startup `Message` panel. Readline/non-TTY fallback keeps the static hint.
- 2026-05-19 chatbox overlap follow-up: the permanent bottom menu was removed after it collided with the bordered composer; keybindings/completions remain, and accepted background turns now emit a compact `Thinking 0s` acknowledgement.
- 2026-05-19 chatbox frame follow-up: the prompt-toolkit composer now uses the native prompt frame for a complete `DAN · Chat` box with a bottom edge. Accepted and queued turns render as `Chat -> Thinking` / `Chat -> Queue` acknowledgement blocks so the sent chat and progress state read as one chain without repeating the objective.
- 2026-05-19 chatbox polish follow-up: prompt-toolkit frame corners are rounded again, the `DAN · Chat` title is patched into the frame's top border, chatbox prompt fragments include a small left margin, and accepted-turn acknowledgement restores elapsed `Thinking 0s...` text in the boxed progress lane.
- 2026-05-19 panel ornament follow-up: Rich panels now keep section labels in the top border and render a narrow vertical side-rail ornament inside the body; plain output keeps the direct labels.
- 2026-05-19 single-lane thinking follow-up: background chatbox turns no longer print a separate loose prompt-adjacent thinking clock, and renderer/narrator progress for those turns is boxed under `DAN · Chat -> Thinking:` with elapsed `Thinking Ns...` text.
