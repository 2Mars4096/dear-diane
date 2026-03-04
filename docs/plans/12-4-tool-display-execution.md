# 12-4: Tool Display & Execution

**Parent:** [12-cursor-parity-chat](12-cursor-parity-chat.md)
**Status:** in-progress
**Goal:** Make tool calls first-class visible events in the chat stream — show what the LLM is doing, let users approve/reject individual actions, and display results inline with expandable detail.

## Current State

- LLM tool calls are opaque: the user sees only the final `MutationPlan` via `GraphDiffPreview`
- No visibility into intermediate reasoning, tool inputs, or partial results
- `/run` commands show a `RunRefBlock` status badge but "View logs" is a dead button
- No approval model for individual operations (all-or-nothing accept/reject on the full plan)

## Target State

- Every tool call renders as an expandable card in the chat stream: tool name, inputs (collapsible JSON), status spinner, output (collapsible)
- Mutation plans still use `GraphDiffPreview` but each operation shows as a step within the tool card
- Run output streams inline with collapsible log sections per node
- Users can approve/reject individual tool calls before execution (opt-in, not default)

## Tasks

- [x] 1. Tool call event protocol
  - [x] 1-1. Define new WebSocket events: `chat_tool_call_start {id, name, args_preview}`, `chat_tool_call_result {id, status, output, duration_ms}`
  - [x] 1-2. Add events to `ChatStreamEvent` TypeScript type
  - [x] 1-3. `chat_manager.py`: emit tool events at each stage of `send_message_with_tools`
  - [x] 1-4. Mutation plan events: args_preview summarises operations, dry-run result in output_preview
- [x] 2. Inline tool call rendering
  - [x] 2-1. New component `ToolCallCard.tsx`: icon, tool name, status (running/success/error), expandable args/output
  - [x] 2-2. `ChatMessage.tsx`: interleave tool call cards with text content in message rendering
  - [x] 2-3. Collapse tool args by default; expand on click
  - [x] 2-4. Error state: red border, error message styling on the tool card
  - [x] 2-5. Duration badge on completed tool calls
- [x] 3. Mutation plan as tool trace
  - [x] 3-1. When `plan_graph_mutations` tool is called, render it as a `ToolCallCard` with the plan summary
  - [x] 3-2. Each operation (add_node, add_edge, etc.) renders as a sub-step within the card
  - [x] 3-3. `GraphDiffPreview` opens from the tool card's "Preview Changes" button
  - [x] 3-4. After apply, tool card updates to show "Applied" / "Rejected" status
- [x] 4. Run output streaming in chat
  - [x] 4-1. `map_run_event_to_chat_block` already handles all event types (done in 12-1)
  - [x] 4-2. New component `RunOutputBlock.tsx`: per-node collapsible sections with status icon, output preview, timing
  - [x] 4-3. Stream run events accumulated as structured `runEvents` array on messages
  - [x] 4-4. Wire "View logs" button in `RunOutputBlock` to jump to LogPanel
  - [x] 4-5. Live progress: running nodes show spinner icon with node ID
- [ ] 5. Per-operation approval gates (opt-in) — **deferred** (requires bidirectional WebSocket handshake)
  - [ ] 5-1. Add `approval_required: bool` field to chat mode config (default: `false` for Agent, `true` for Plan) *(deferred — requires bidirectional WebSocket handshake)*
  - [ ] 5-2. When enabled: each tool call pauses and shows "Approve" / "Skip" / "Edit" buttons *(deferred — requires bidirectional WebSocket handshake)*
  - [ ] 5-3. Backend: tool execution waits for WebSocket approval message before proceeding *(deferred — requires bidirectional WebSocket handshake)*
  - [ ] 5-4. Timeout: auto-reject after configurable period (default: 5 min) with notification *(deferred — requires bidirectional WebSocket handshake)*
  - [ ] 5-5. Bulk approve: "Approve All Remaining" button for the current plan *(deferred — requires bidirectional WebSocket handshake)*
- [ ] 6. Sandbox execution display — **deferred** (depends on Phase 6 sandbox runner)
  - [ ] 6-1. Code tool execution: show sandbox indicator (lock icon) and execution environment info *(deferred — depends on Phase 6 sandbox runner)*
  - [ ] 6-2. Terminal-like output rendering: monospace, ANSI color support, scrollable *(deferred — depends on Phase 6 sandbox runner)*
  - [ ] 6-3. File output artifacts: show generated files with download/preview links *(deferred — depends on Phase 6 sandbox runner)*
  - [ ] 6-4. Resource usage: show execution time, memory (if available from sandbox) *(deferred — depends on Phase 6 sandbox runner)*

## Decisions

- `chat_tool_call_progress` dropped — not needed for the current tool (plan_graph_mutations is non-streaming). Can add later for streaming tools.
- Tool call events are emitted around the mutation plan processing in `send_message_with_tools`, wrapping the dry-run step. This gives the frontend a clear start/result lifecycle.
- `ToolCallCard` is rendered inline in `ChatMessage.tsx` via the `toolCalls` array on ChatMessage. Mutation badge is hidden when a tool call card is present.
- `RunOutputBlock` replaces `RunRefBlock` when structured `runEvents` are available; falls back to the simple badge otherwise.
- Tasks 5 and 6 are deferred as they require substantial bidirectional WebSocket work and sandbox integration respectively.

## Notes

- Task 1 (event protocol) is the backend foundation. Tasks 2-4 are frontend rendering. Task 5 is the approval model.
- Per-operation approval (task 5) is the most architecturally significant — it requires bidirectional WebSocket communication for the approval handshake.
- The sandbox display (task 6) leverages the existing `SandboxRunner` from Phase 6 (plan 9-2). This sub-plan adds the chat rendering, not the execution.
- Mutation plans remain the primary tool for graph editing. Other tools (inspect, search, run) are additive.
- Tool call serialization (`toolCalls`, `runEvents`) is added to `toBackendMessage`/`fromBackendMessage` for thread persistence.
