# 5: Chat / Agent V2 Shell

**Status:** not-started
**Goal:** Evolve the lightweight V2 frontend into a two-mode shell that can run normal Chat turns and durable Agent tasks without wiring the old heavy editor modes into the V2 happy path.

## Tasks
- [ ] 1. Replace legacy mode selector with a two-choice control
  - [ ] 1-1. Show `Chat` and `Agent` as the only primary V2 modes
  - [ ] 1-2. Keep old backend mode values hidden behind compatibility mapping
  - [ ] 1-3. Preserve a clear return path to the classic editor
- [ ] 2. Add Agent profile controls
  - [ ] 2-1. Add `Fast`, `Balanced`, `Deep`, and `Max`
  - [ ] 2-2. Show resolved profile summary in compact language
  - [ ] 2-3. Avoid exposing raw agent count as the main control
- [ ] 3. Add Agent run display
  - [ ] 3-1. Show task status, current phase, worker/activity rows, validation, repair, and final report
  - [ ] 3-2. Support stop, retry, and open-log affordances
  - [ ] 3-3. Link Agent run summaries back into the chat thread
- [ ] 4. Add reconnect and persistence UX
  - [ ] 4-1. Restore active Agent run after reload
  - [ ] 4-2. Show historical Agent runs in thread history without requiring Development mode
  - [ ] 4-3. Handle backend unavailable states cleanly
- [ ] 5. Keep V2 visually light
  - [ ] 5-1. Keep a left history rail, one central conversation/task surface, and one composer
  - [ ] 5-2. Do not embed the graph editor, Monaco, PDF, or mode-specific panels in the V2 path
  - [ ] 5-3. Keep bundle impact measurable through existing build/bundle checks

## Decisions
- V2 remains a chat/task product shell, not a second full IDE.
- Classic modes remain accessible but are not part of the V2 primary workflow.
- Agent run internals should be inspectable but quiet by default.

## Notes
- This UI plan is a companion to [../plans/57-chat-agent-v2-control-plane.md](../plans/57-chat-agent-v2-control-plane.md). Backend contract work lives in the `57-*` plan stack.
