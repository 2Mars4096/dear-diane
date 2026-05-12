# 57-17: Super DAN TUI Neutral Progress And Result Surface

**Parent:** [57-chat-agent-v2-control-plane](57-chat-agent-v2-control-plane.md)
**Status:** completed
**Goal:** Make TUI progress read as neutral step awareness plus useful answer/result text, rather than first-person narration or raw model/tool telemetry.

## Tasks
- [x] 1. Define neutral progress language
  - [x] 1-1. Prefer state/update phrasing such as `Reading workspace context.`, `Workspace context checked: docs/todo.md (42 lines).`, `Patch prepared: src/...`, and `Validation running.`
  - [x] 1-2. Avoid making every line start with `I'm ...`; reserve first-person wording only where it is genuinely conversational or final.
  - [x] 1-3. Keep model rounds, worker ids, tool counts, raw tool ids, and provider chatter out of the default panel.
  - [x] 1-4. Preserve raw detail in `--raw-events`.
  - [x] 1-5. Include the selected four-lane mode near the top, with a short plain-language rationale.
- [x] 2. Add a real answer/result surface
  - [x] 2-1. Add TUI state fields for `mode_line`, `activity_lines`, result/answer lines, blocker state, and `trace_lines`.
  - [x] 2-2. Render useful read-only answers directly under `Result:` instead of showing only progress.
  - [x] 2-3. For write lanes, render changed files, validation outcome, tests/checks run, remaining blockers, and trace refs under `Result:`.
  - [x] 2-4. Keep result text bounded and scannable; long answers can point to artifacts or files.
  - [x] 2-5. Persist compact final result text into the visible TUI transcript.
- [x] 3. Render progress as start/end/intermittent step updates
  - [x] 3-1. At run start, show `You asked:` and `Mode:` before activity begins.
  - [x] 3-2. For each meaningful stage start, add one neutral activity line.
  - [x] 3-3. For each meaningful stage completion, update or append a concise completion line with concrete context.
  - [x] 3-4. For long quiet periods, show a neutral heartbeat such as `Still working on validation.` plus the bottom elapsed timer.
  - [x] 3-5. Keep repeated file reads/listing/tool calls coalesced by user-level activity, not by raw tool type.
- [x] 4. Make the four lanes shape the feedback
  - [x] 4-1. `simple read-only`: show the read source(s) and answer; no planner/build progress.
  - [x] 4-2. `complex read-only`: show sources inspected, synthesis progress, and answer; no write/change language.
  - [x] 4-3. `simple write`: show target, patch/change, validation if any, and result.
  - [x] 4-4. `complex write`: show planning, build, validation, repair, changed files, and next queued work.
  - [x] 4-5. Clarification cases should render the question as the result, not as an error.
- [x] 5. Tighten rendering and highlighting
  - [x] 5-1. Default panel order: `You asked`, `Mode`, `Activity`, `Result`, `Trace`, `Working/Elapsed`.
  - [x] 5-2. Highlight paths, statuses, mode labels, validation outcomes, and trace refs.
  - [x] 5-3. Avoid over-coloring ordinary text.
  - [x] 5-4. Keep plain fallback readable and close to the Rich layout.
- [x] 6. Validate behavior
  - [x] 6-1. Add unit tests that default progress contains no raw `Round N`, `tool_count`, worker id, or low-level tool ids.
  - [x] 6-2. Add tests that read-only requests show answer/result lines, not only activity lines.
  - [x] 6-3. Add tests that write requests show changed files and validation/result lines.
  - [x] 6-4. Add Rich/plain render checks for the four lanes.
  - [x] 6-5. Add transcript tests proving final result text persists across turns.

## Decisions
- The TUI is not a telemetry dashboard by default. It is a compact operator-facing conversation/status surface.
- Progress should make the user aware of what is happening, not perform a personality-heavy narration.
- The answer/result surface is first-class. A request that asks to show, summarize, or explain something must display the answer in the TUI.
- Raw event rows remain the source of truth and stay available through debug mode.
- Implementation should remain output/projection focused. Prefer TUI-side mapping and optional public summary fields over changing Super DAN scheduling or execution.

## Notes
- This plan builds on [57-14](57-14-super-dan-tui-conversational-timeline.md) and [57-16](57-16-super-dan-tui-four-lane-intent-gate.md).
- The desired tone is neutral and useful: `Reading workspace context.`, not `I'm asking kimi for round 4`.
- The elapsed footer from `57-14` remains the bottom time cue for long-running work.
- 2026-05-12: First implementation landed in the TUI projection: neutral `Activity:` / `Result:` sections, mode labels, bounded read-only answers, clarification rendering, and default hiding of raw tool ids/model rounds. Remaining work is full four-lane Rich/plain visual coverage and a dedicated simple-write result path.
- 2026-05-12: Second implementation slice added complex-read-only search result lines and simple-write copy/move/touch result rendering. Remaining work is fuller Rich/plain visual coverage across all lanes and any future model-backed read-only loop.
- 2026-05-12: Final implementation slice added Rich/plain coverage for all four lane labels and wired live complex read-only model-loop progress/result lines into the same `Activity` / `Result` surface.
