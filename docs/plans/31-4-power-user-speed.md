# 31-4: Power-User Speed

**Parent:** [31-daily-use-qol](31-daily-use-qol.md)
**Status:** completed
**Goal:** Add CLI pipe/one-shot mode, memory management commands, file handling UX improvements, and prep timeout controls so power users can script, manage memory, and avoid slow I/O stalls.

## Problem

Power users hit friction daily: no way to run a single question and exit (`dan ask "..."`), no pipe mode for scripting, no memory delete/confirm/reject from chat, prep phase can block indefinitely on slow I/O, and file attachments require explicit "read this file" instead of auto-context. These gaps make scripting, memory hygiene, and file-heavy workflows tedious.

## Tasks

- [x] 1. CLI pipe mode and one-shot ask
  - [x] 1-1. Add `--ask "question"` to `dan-chat` in `src/dan/cli/chat.py`: send one message, stream response, exit. No REPL. Reuse `ChatClient.send_chat_message` + `stream_chat_events`, print response text, then `sys.exit(0)`
  - [x] 1-2. Add `--pipe` flag: read stdin as the message (block until EOF or newline), write response text to stdout. No Rich formatting, no prompts, no readline. For scripting: `echo "summarize this" | dan-chat --pipe` or `dan-chat --pipe < file.txt`
  - [x] 1-3. Add `--output <path>` flag: write the response text to a file in addition to stdout. Works with both `--ask` and `--pipe`
  - [x] 1-4. Add `--model <name>` flag to `dan-chat`: on startup, send `/model <name>` as the first message (reuses the `/model` fast command from 31-1). No API changes needed — the fast command handles model switching. Override applies for this session only
  - [x] 1-5. Add `dan-ask` entry point in `pyproject.toml` `[project.scripts]`: point to `dan.cli.ask:main` (or a thin wrapper in `chat.py`). `dan-ask "question"` is equivalent to `dan-chat --ask "question"`
  - [x] 1-6. Extend `ChatMessageRequest` in `src/dan/server/app.py`: add optional `attachment_path: str | None = None`. Pass `attachment_path` into `SurfaceMessage.metadata["selected_path"]` when building the surface msg. Model override is handled by 31-1's `/model` command (no need to add `model` to `ChatMessageRequest`)

- [x] 2. Memory management commands
  - [x] 2-1. Add `/memory-delete <id>` to `_handle_memory_command` in `src/dan/server/concierge/runtime.py`. Call `self.memory_kernel.delete(item_id, hard=True)` (MemoryKernel already has `delete` at line 470). Guard: only allow deleting items with `lifecycle != DURABLE` unless user passes `--force` (e.g. `/memory-delete <id> --force`). On success: "Deleted item <id>." On failure (not found, DURABLE without --force): appropriate error message
  - [x] 2-2. Add `/memory-forget <query>`: call `memory_kernel.retrieve(query, limit=10)`, display matches with IDs, then immediately delete all matches. No confirmation flow — keeps it a true fast command. If zero matches, respond "No matching memories found." For safety, `/memory-forget` only deletes items with lifecycle `ACTIVE` or `INFERRED` (not `DURABLE`). To include durable items, user must use `/memory-delete <id> --force` per item
  - [x] 2-3. Add `/memory-confirm` as fast command: call `handle_preference_confirmation(surface_id, "confirm")` (or equivalent). If `_pending_preference_surface` has items, confirm all and respond. If empty: "No pending preferences to confirm."
  - [x] 2-4. Add `/memory-reject` as fast command: same pattern, call `handle_preference_confirmation` with "reject" semantics. Support `/memory-reject 1 2 3` to reject by index from last surfaced list
  - [x] 2-5. Add `/memory-delete` and `/memory-forget` to `_FAST_COMMAND_PREFIXES` in `runtime.py` (line 83) and `_BYPASS_PREFIXES` in `dispatcher.py` so they skip queueing. `/memory-confirm` and `/memory-reject` already match `/memory-` prefix

- [x] 3. Prep timeout controls
  - [x] 3-1. Add `DAN_CONCIERGE_PREP_TIMEOUT` env var (default 5.0 seconds). Parse as float in `src/dan/server/concierge/runtime.py` where `fan_out_dict` is used for parallel prep (around line 325)
  - [x] 3-2. Wrap the `fan_out_dict(prep_tasks)` call in `asyncio.wait_for(..., timeout=prep_timeout)`. On `asyncio.TimeoutError`, log warning ("Prep phase timed out after Ns; proceeding with partial context"), use whatever completed (memory/context/reuse), set failed keys to empty/None
  - [x] 3-3. Extend `fan_out_dict` in `src/dan/server/concierge/fan_out.py` to accept optional `timeout_per` or add a top-level `timeout` that applies to the entire gather. Alternatively, use `asyncio.wait(..., timeout=prep_timeout, return_when=asyncio.FIRST_EXCEPTION)` to implement partial completion. Prefer `asyncio.wait_for` around the whole `fan_out_dict` for simplicity
  - [x] 3-4. Apply same timeout to other `fan_out_dict` usages in runtime (e.g. lines 1723, 2474) if they are part of the "prep" phase that blocks the first response. Document in plan: only the initial parallel prep (memory + context + reuse) needs the timeout; later fan-outs (e.g. preference surfacing) can remain untimed or use a longer default

- [x] 4. File handling UX improvements
  - [x] 4-1. Ensure `ChatMessageRequest` accepts `attachment_path` and the app passes it to `SurfaceMessage.metadata["selected_path"]` (see task 1-6). Verify adapter already sends `attachment_path` in body (cli/adapter.py line 254)
  - [x] 4-2. In `FileHandler.handle` (`src/dan/server/concierge/handlers.py`), when `selected_path` is set and file exists: for non-PDF files (CSV, JSON, TXT, etc.) under 100KB, auto-read content and pass as context. Add `_auto_read_small_file(path: Path) -> str | None`: check `path.stat().st_size < 102400`, detect type by extension, read as text (UTF-8, fallback errors=replace). Return content or None if too large / binary
  - [x] 4-3. When auto-read succeeds, include content in `HandlerResult.content` or as an attachment that the LLM receives (e.g. prepend to message or pass via `attachments` + system hint). Match existing pattern for PDF review: `_review_document` reads and passes content. For small TXT/CSV/JSON, do similar: "File content:\n{content}\n\nUser message: {msg.text}"
  - [x] 4-4. Add `file_delivery` action / capability: when a tool like `file_write` or `python_eval` produces an output file path, the chat response can include a `chat_file_attachment` event. In `capability_handlers.py`, after `handle_file_write` (or similar) returns a path, the chat manager should emit `ChatFileAttachmentEvent(path=..., filename=..., size=...)` so adapters receive it and call `adapter.send_file()`. Wire `file_delivery` into the tool-result handling path in `chat_manager.py` (where tool results are processed and events emitted)
  - [x] 4-5. WhatsApp adapter already has `_send_file` / `send_file`; `cli/adapter.py` already consumes `chat_file_attachment` events and calls `adapter.send_file` (lines 306–337). Ensure capability handlers can trigger this by emitting the event

- [ ] 5. Tests
  - [ ] 5-1. Tests for `dan-ask` one-shot mode: mock `ChatClient`, verify single `send_chat_message` + `stream_chat_events`, process events, assert exit without REPL. In `tests/test_cli/` or `tests/test_chat_cli.py`
  - [ ] 5-2. Tests for `--pipe` flag: mock stdin with "hello", mock server response, assert stdout receives response text only, no prompts
  - [ ] 5-3. Tests for `--output` flag: verify response written to specified file
  - [ ] 5-4. Tests for `/memory-delete`, `/memory-forget`, `/memory-confirm`, `/memory-reject` in `tests/test_concierge/test_memory_commands.py` or `test_fast_commands.py`: mock MemoryKernel, verify correct methods called, response content
  - [ ] 5-5. Tests for prep timeout: mock slow `_retrieve_memory_context` or `context_resolver.resolve`, set `DAN_CONCIERGE_PREP_TIMEOUT=0.1`, verify timeout triggers, warning logged, response still produced with partial/empty context

## Decisions

- (filled in during execution)

## Notes

- `MemoryKernel.delete(item_id, hard=True)` removes from index; `hard=False` soft-deletes (ARCHIVE). For `/memory-delete` we want hard delete. The plan's "lifecycle != DURABLE unless --force" means: if item has `lifecycle == MemoryLifecycle.DURABLE`, require `--force` to delete
- `handle_preference_confirmation` already exists and handles "confirm"/"reject" via natural language. `/memory-confirm` and `/memory-reject` are explicit fast commands that bypass the natural-language check
- `fan_out_dict` already supports `timeout_per` for per-task timeout. For whole-prep timeout, `asyncio.wait_for` around the entire `fan_out_dict` call is simpler than modifying each task
- File auto-read: 100KB limit avoids loading huge CSVs. For larger files, keep current behavior (user must say "read this file" or use explicit file_read tool)

## Estimate

~1 day
