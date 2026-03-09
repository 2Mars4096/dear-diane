# 29-7: Essential Tools Expansion

**Parent:** [29-concierge-memory-evolvement](29-concierge-memory-evolvement.md)
**Status:** not-started
**Goal:** Fill critical gaps in the built-in tool library so the concierge can handle common user requests without workarounds. Prioritize tools that are trivial to implement but create embarrassing failures when missing.

## Context

DAN ships 11 built-in tools (`dan.tools`): file_read, file_write, list_directory, web_search, web_fetch, http_request, shell_command, pdf_read, text_chunk, json_extract, regex_match.

These cover file I/O, web, shell, and text processing. But several extremely common assistant interactions fail or require awkward workarounds:

- "What time is it?" → LLM fabricates (no datetime tool)
- "Copy that to my clipboard" → impossible (no clipboard tool)
- "Calculate 15% compound growth on $10k" → requires shell_command (no code eval tool)
- "Email this summary to sarah@example.com" → impossible (no send_email tool, though EmailAdapter has aiosmtplib)
- "Notify me when it finishes" → NotificationManager exists but isn't a tool
- "Analyze my data.csv" → file_read returns raw text, not structured data
- "Move this file to archive/" → requires shell_command (no file ops tool)
- "What's in this photo?" → impossible (no image understanding)
- "What changed in the last commit?" → requires shell_command (no git tools)
- "Commit these changes" → requires shell_command with no safety guardrails

## Tasks

### 1. Immediate tools (trivial, high-demand)
- [ ] 1-1. `current_datetime` — return current date, time, timezone, day of week. No dependencies. Fixes the #1 factual error: the LLM doesn't know what time it is.
- [ ] 1-2. `clipboard` — copy text to system clipboard (`pbcopy` on macOS, `xclip`/`xsel` on Linux). Parameters: `text` (str). Returns: `{copied: true, length: N}`. Graceful failure if no clipboard available (SSH, headless).
- [ ] 1-3. `python_eval` — sandboxed Python execution for math, data manipulation, quick scripts. Wraps existing `SandboxRunner` with chat-tool interface. Parameters: `code` (str), `timeout` (int, default 30). Returns: `{result, stdout, stderr}`. Distinct from workflow CodeOperator — this is a chat-level tool.
- [ ] 1-4. `send_email` — send email via SMTP. Wraps existing `aiosmtplib` dependency from EmailAdapter. Parameters: `to` (str), `subject` (str), `body` (str), `attachments` (optional list of file paths). Requires `DAN_SMTP_*` env vars. Returns: `{sent: true, message_id}`.
- [ ] 1-5. `notify` — send a notification via NotificationManager. Wraps existing infrastructure. Parameters: `message` (str), `title` (optional), `channel` (optional: "macos", "webhook", "bell"). Returns: `{delivered: true, channel}`.

### 2. File operation tools
- [ ] 2-1. `file_move` — move/rename file or directory. Parameters: `source` (str), `destination` (str). Workspace-sandboxed. Returns: `{moved: true, destination}`.
- [ ] 2-2. `file_copy` — copy file or directory. Parameters: `source` (str), `destination` (str), `recursive` (bool, default false). Workspace-sandboxed. Returns: `{copied: true, destination}`.
- [ ] 2-3. `file_delete` — delete file or empty directory. Parameters: `path` (str), `recursive` (bool, default false). Workspace-sandboxed. Requires confirmation for recursive. Returns: `{deleted: true}`.
- [ ] 2-4. All three enforce `DAN_WORKSPACE_ROOT` boundary (same as file_read/file_write).

### 3. Data tools
- [ ] 3-1. `csv_read` — parse CSV/TSV file into structured data. Parameters: `path` (str), `delimiter` (optional, auto-detect), `max_rows` (int, default 1000), `columns` (optional list to select). Returns: `{headers, rows (list of dicts), row_count, column_count}`. Uses stdlib `csv` module. Handles common issues: BOM, mixed encodings, quoted fields.
- [ ] 3-2. `spreadsheet_read` — read Excel (.xlsx) file. Parameters: `path` (str), `sheet` (optional, default first), `max_rows` (int, default 1000). Returns same structure as csv_read. Optional dependency: `openpyxl`. Graceful skip if not installed.

### 4. Media tools
- [ ] 4-1. `image_describe` — describe image content using vision-capable LLM. Parameters: `path` (str), `question` (optional, default "Describe this image"). Returns: `{description}`. Requires a vision model (GPT-4o, Claude with vision). Supports JPEG, PNG, WebP, GIF.
- [ ] 4-2. `audio_transcribe` — transcribe audio/voice file. Parameters: `path` (str), `language` (optional). Returns: `{text, language_detected, duration_seconds}`. Uses OpenAI Whisper API (`DAN_OPENAI_API_KEY`). Supports MP3, WAV, M4A, OGG, WEBM.
- [ ] 4-3. Note: plan [26-7-whatsapp-inbound-media](26-7-whatsapp-inbound-media.md) covers the WhatsApp-specific media download infrastructure and adapter-level handling. These tools complement 26-7 by providing the processing capabilities that downloaded media flows into. 26-7 downloads the bytes; these tools interpret them.

### 5. Git tools
- [ ] 5-1. `git_status` — current branch, modified/staged/untracked files. Parameters: `path` (optional, default workspace root). Returns: `{branch, modified, staged, untracked, ahead, behind}`. Read-only, always safe.
- [ ] 5-2. `git_diff` — show diff of changes. Parameters: `path` (optional), `staged` (bool, default false), `commit` (optional, compare against specific commit). Returns: `{diff_text, files_changed, additions, deletions}`. Read-only.
- [ ] 5-3. `git_log` — recent commit history. Parameters: `path` (optional), `limit` (int, default 10), `since` (optional date). Returns: `{commits: [{hash, author, date, message}]}`. Read-only.
- [ ] 5-4. `git_commit` — stage files and commit. Parameters: `message` (str), `files` (optional list, default all modified), `amend` (bool, default false). Returns: `{commit_hash, files_committed}`. Write operation — requires confirmation on messaging surfaces per ActionPolicy.
- [ ] 5-5. `git_branch` — list, create, or switch branches. Parameters: `action` ("list" | "create" | "switch"), `name` (optional). Returns: `{branches, current}` or `{created}` or `{switched}`. Create/switch are write operations.
- [ ] 5-6. `git_worktree` — manage git worktrees for parallel branch work. Parameters: `action` ("list" | "add" | "remove"), `path` (optional, for add/remove), `branch` (optional, for add). Returns: `{worktrees: [{path, branch, head}]}` or `{created: path}` or `{removed: path}`. Enables working on feature branches in isolation without stashing. Useful for iterative build sessions (29-3) that need to test on a clean branch.
- [ ] 5-7. Safety: no force push, no hard reset, no rebase. Destructive git operations require `shell_command`. These tools cover the safe 90% of git usage.
- [ ] 5-8. All git tools check for `.git` directory; return clear error if not in a git repo.

### 6. Utility tools
- [ ] 6-1. `compress` — create archive from files/directory. Parameters: `paths` (list of str), `output` (str), `format` ("zip" | "tar.gz", default "zip"). Returns: `{archive_path, size_bytes, file_count}`.
- [ ] 6-2. `text_translate` — translate text between languages using LLM. Parameters: `text` (str), `target_language` (str), `source_language` (optional, auto-detect). Returns: `{translated, source_language, target_language}`. Thin wrapper around an LLM call.
- [ ] 6-3. `diff` — compare two files or two text strings. Parameters: `a` (str, path or text), `b` (str, path or text), `context_lines` (int, default 3). Returns: `{unified_diff, additions, deletions, is_identical}`. Uses stdlib `difflib`.

### 7. Integration
- [ ] 7-1. Add all new tools to `_TOOL_MODULES` in `dan/tools/__init__.py`
- [ ] 7-2. Each tool follows existing pattern: `TOOL_METADATA` dict + async callable
- [ ] 7-3. Auto-registered via `get_all_tools()` — no manual registration needed
- [ ] 7-4. All tools available in both workflow (ToolOperator) and chat (capability tools) contexts
- [ ] 7-5. Optional-dependency tools (`spreadsheet_read`, `audio_transcribe`) skip gracefully if deps missing
- [ ] 7-6. Update `docs/llm-api-guide.md` tool table and `docs/architecture.md` tool list

### 8. Tests
- [ ] 8-1. Unit tests for each tool (mock filesystem for file ops, mock subprocess for clipboard)
- [ ] 8-2. `current_datetime` returns valid ISO format with timezone
- [ ] 8-3. `csv_read` handles: UTF-8 BOM, tab-delimited, quoted commas, empty cells, max_rows truncation
- [ ] 8-4. `python_eval` respects timeout and sandbox boundaries
- [ ] 8-5. `image_describe` and `audio_transcribe` skip gracefully when API keys missing
- [ ] 8-6. All file-touching tools enforce workspace sandbox
- [ ] 8-7. Git tools: status/diff/log work in repo, clear error outside repo, commit creates valid commit

## Decisions

- (to be filled during execution)

## Notes

- `current_datetime`, `clipboard`, `notify`, and `diff` need zero external dependencies. They can ship immediately.
- `python_eval` reuses the existing `SandboxRunner` — no new sandbox implementation needed.
- `send_email` reuses `aiosmtplib` already in the dependency tree from EmailAdapter.
- `image_describe` and `audio_transcribe` are the bridge between 26-7 (media download) and actual media understanding. 26-7 gets bytes into DAN; these tools make sense of them.
- `text_translate` is a thin LLM wrapper. It exists as an explicit tool so the model can chain it (search → translate) rather than doing translation inline in one large prompt.
- Git tools use `asyncio.create_subprocess_exec("git", ...)` — no new dependency. Read-only tools (status/diff/log) are always safe. Write tools (commit/branch) follow the existing `ActionPolicy` confirmation rules for destructive operations.
- The tool count goes from 11 to ~30. Still batteries-included, still auto-discovered, still workspace-sandboxed where relevant.
