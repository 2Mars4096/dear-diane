# 20-1: Docs Sync & Quick Wins

**Parent:** [20-patch-polish](20-patch-polish.md)
**Status:** completed
**Goal:** Close documentation debt for three shipped-but-undocumented feature areas and land three trivial chat UI improvements that were deferred from Phase 7.2.

## Tasks

### Documentation Sync

- [x] 1. Runtime reliability docs (from 7-1 task 8)
  - [x] 1-1. `docs/architecture.md`: add `RetryPolicy` model description under Engine section — fields (`max_retries`, `backoff_factor`, `retry_on`), how it attaches to `NodeBase`, how `ToolExecutor` respects it
  - [x] 1-2. `docs/llm-api-guide.md`: add retry/fallback usage examples — builder DSL `wf.llm(..., retry=RetryPolicy(...))`, fallback model syntax, halt semantics
  - [x] 1-3. `README.md`: mention retry/fallback in feature list

- [x] 2. Multi-provider docs (from 7-2 task 10)
  - [x] 2-1. `docs/architecture.md`: add provider registry section — `ProviderRegistry`, supported providers (OpenAI, Anthropic, Google), per-node model dispatch, key management (`DAN_OPENAI_API_KEY`, `DAN_ANTHROPIC_API_KEY`, `DAN_GOOGLE_API_KEY`)
  - [x] 2-2. `docs/llm-api-guide.md`: add multi-provider examples — `wf.llm(..., model="anthropic/claude-3.5-sonnet")`, provider prefix convention, cost table reference
  - [x] 2-3. `README.md`: list supported providers in feature summary

- [x] 3. Built-in tools docs (from 7-3 task 11)
  - [x] 3-1. `docs/architecture.md`: add `dan.tools` package overview — 11 tools (`file_read`, `file_write`, `list_directory`, `web_search`, `web_fetch`, `http_request`, `shell_command`, `pdf_read`, `text_chunk`, `json_extract`, `regex_match`), auto-registration
  - [x] 3-2. `docs/llm-api-guide.md`: add tool usage examples — `wf.tool("web_search", tool_id="web_search")`, tool schema reference, how to discover available tools
  - [x] 3-3. `README.md`: list built-in tool categories in feature summary

### Chat UI Quick Wins

- [x] 4. Per-thread mode persistence (from 12-2 task 2-2)
  - [x] 4-1. `ChatStore` metadata (`*.meta.json`): persist `mode` (`ask|agent|plan|debug`) via `set_thread_meta` on create/update thread operations
  - [x] 4-2. Chat thread APIs (`/api/chats/{workflow_id}`, `/api/chats/{workflow_id}/{thread_id}`): include `mode` in list/get responses and accept `mode` in create/update payloads
  - [x] 4-3. `ChatPanel.tsx`: on mode switch, persist selected mode to active thread metadata; on thread switch, hydrate mode from thread metadata (instead of always using default)
  - [x] 4-4. Backward compat: threads without stored mode default to `"agent"`; legacy `"build"`/`"mutate"` metadata normalizes to `"agent"`

- [x] 5. Keyboard shortcut to cycle chat modes (from 12-2 task 2-4)
  - [x] 5-1. `ChatPanel.tsx`: register `Cmd+Shift+M` (Mac) / `Ctrl+Shift+M` (Windows) keyboard handler
  - [x] 5-2. Cycle order: Agent → Ask → Plan → Debug → Agent
  - [x] 5-3. Show brief toast or mode indicator flash on switch
  - [x] 5-4. Ensure shortcut doesn't fire when chat input has focus with modifier keys (prevent conflict with text editing)

- [x] 6. Debug diff tag (from 12-2 task 6-4)
  - [x] 6-1. `chat_manager.py`: when mode is `"debug"` and a mutation plan is generated, tag the mutation with `source: "debug-fix"` in metadata
  - [x] 6-2. `GraphDiffPreview.tsx` (or equivalent diff rendering component): detect `source: "debug-fix"` tag and render a `[debug-fix]` badge/label on the diff header
  - [x] 6-3. Style: amber/yellow badge to visually distinguish from normal agent mutations

### Validation

- [x] 7. Targeted tests for docs/quick-win regressions
  - [x] 7-1. Backend tests: chat thread list/get/create/update preserve `mode` metadata; fallback to `agent` when metadata missing
  - [ ] 7-2. Frontend tests: thread switch restores mode; `Cmd/Ctrl+Shift+M` cycles modes in the expected order *(deferred — requires vitest setup with DOM mocking)*
  - [ ] 7-3. Frontend tests: debug-mode proposed mutation renders `[debug-fix]` visual tag *(deferred — requires vitest setup with DOM mocking)*

## Decisions

- `RetryPolicy` is set on the compiled `Graph` (post-build), not via builder DSL keyword args — the builder `llm()` method doesn't expose `retry_policy` as a parameter. Documented accordingly.
- Mode normalization (`_normalize_mode`) lives on `ChatStore` as a classmethod for reuse across list/get/create/update endpoints.
- Debug diff tag uses `plan_dump.setdefault("metadata", {})["source"] = "debug-fix"` on the serialized plan dict rather than adding a `metadata` field to the `MutationPlan` Pydantic model (keeps the model clean).
- Frontend test tasks 7-2 and 7-3 deferred — they require vitest + React Testing Library with DOM mocking setup.

## Notes

- Tasks 1–6 are largely independent and can be done in parallel; Task 7 (validation) should follow the corresponding implementation tasks.
- Docs tasks (1–3) require reading source code to write accurate documentation — not just paraphrasing plan files.
- Chat UI tasks (4–6) touch `ChatPanel.tsx`, chat thread APIs (`/api/chats/*`), `ChatStore` metadata, and diff rendering — no engine/scheduler changes expected.
- Task 4 (mode persistence) is the most impactful of the quick wins: without it, switching threads resets mode, which is a frequent annoyance.
