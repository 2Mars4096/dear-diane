# 28-3: Response Actions

**Parent:** [28-llm-first-chat](28-llm-first-chat.md)
**Status:** in-progress
**Goal:** Add a post-LLM layer that handles file delivery, destructive-action confirmation, unsourced-claim validation, and context persistence.

## Problem

When the LLM is the sole decision-maker, safety and UX concerns that were previously handled by routing layers need a new home. The LLM can now call `shell_command`, `file_write`, `send_email` — these need confirmation on messaging surfaces. The LLM can resolve a file path — on WhatsApp the file should be physically sent. The LLM may still produce unsourced claims — these need flagging.

## Tasks

### File delivery action
- [x] 1. Detect file paths in tool results and offer delivery on messaging surfaces
  - [x] 1-1. After the tool loop completes, scan tool call results for `data.path` fields pointing to existing files
  - [x] 1-2. On messaging surfaces (WhatsApp, Telegram, email): automatically send the file via `adapter.send_file()`
  - [x] 1-3. On editor/CLI surfaces: include the path in the text response
  - [x] 1-4. New event type: `ChatFileAttachmentEvent(path, filename, size)` so adapters know to send a file

### Destructive-action confirmation
- [ ] 2. Require confirmation for destructive tools on messaging surfaces
  - [ ] 2-1. Tag tools as `destructive` in registration: `file_write`, `shell_command`, `send_email`, `http_request` (non-GET)
  - [ ] 2-2. Before executing a destructive tool in the multi-turn loop, yield a `ChatConfirmationEvent(tool_name, args_preview)` and pause
  - [ ] 2-3. On messaging surfaces: adapter sends "DAN wants to run `shell_command('rm ...')` — reply 'yes' to approve"
  - [ ] 2-4. On editor: show inline approval button (existing approval pattern from 12-4)
  - [ ] 2-5. If denied, feed "user denied this action" back to the LLM as a tool result so it can adjust

### Claim validation
- [x] 3. Keep and enhance the existing unsourced-claim check
  - [x] 3-1. Move `_check_unsourced_claims()` from `Concierge` to the Response Actions layer
  - [x] 3-2. Run after the final LLM text response, not inside the concierge
  - [x] 3-3. If numeric claims detected without a preceding `web_search` tool call, append disclaimer

### WhatsApp message splitting
- [x] 4. Split long responses for messaging surfaces
  - [x] 4-1. WhatsApp has a 4096-char limit per message. Detect when the final response exceeds this.
  - [x] 4-2. Split at paragraph boundaries (double newline), not mid-sentence
  - [x] 4-3. New event type: `ChatMultiPartEvent(parts: list[str])` — adapter sends each part sequentially with a small delay
  - [x] 4-4. For structured reports (headers + sections), split at section boundaries
  - [x] 4-5. CLI and editor surfaces don't need splitting — only messaging surfaces

### Context persistence
- [ ] 5. Update project/task state after each conversation turn
  - [ ] 5-1. Append user message + assistant response to task turns
  - [ ] 5-2. If the LLM called `start_run`, `publish_workflow`, etc., update project linked state
  - [ ] 5-3. Auto-summarize every N turns (existing behavior, just rewired)

## Files

| File | Action |
|---|---|
| `src/dan/server/concierge/actions.py` | Created — `extract_file_paths_from_tool_results`, `split_message_for_surface`, `check_unsourced_claims` |
| `src/dan/server/chat_manager.py` | Added `ChatFileAttachmentEvent`, `ChatMultiPartEvent`, wired response actions into `send_message_with_tools()` |
| `src/dan/server/concierge/runtime.py` | Delegated `_check_unsourced_claims` to actions module, removed duplicate regex |
| `src/dan/cli/adapter.py` | Handle file attachments, message splitting via `split_message_for_surface()` |
| `tests/test_server/test_response_actions.py` | 30 tests covering all three action types + event models + adapter integration |

## Decisions

- Message splitting happens at the adapter level (not in ChatManager) since the adapter knows its surface type
- Claim validation in `send_message_with_tools` uses precise tool-name checking (`web_search`); legacy handler-dispatch path preserves backward-compatible behavior (any tool call suppresses disclaimer)
- Hard character-level splitting added as final fallback for messages with no paragraph/line boundaries
- File attachment events emitted before `ChatCompleteEvent` so adapters can send files before the text reply

## Notes

- Confirmation (task 2) is the trickiest part: it interrupts the multi-turn tool loop. Deferred to a future session.
- Context persistence (task 5) already works via the existing concierge `_record_assistant_turn` / `_finalize_task` flow. Rewiring deferred.
- For MVP, destructive confirmation can be optional (`DAN_CONFIRM_DESTRUCTIVE=1`) — default off for personal use, on for shared deployments.
