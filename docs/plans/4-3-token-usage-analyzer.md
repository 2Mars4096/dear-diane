# 4-3: Session token usage analyzer

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** in-progress
**Goal:** Show which Claude Code / Codex sessions and chat rounds consume the most tokens, attribute the cost to activities, and suggest how to reduce it — from the transcripts already on disk.

## Tasks
- [ ] 1. Transcript profiler (`src/dan/native_workers/token_usage.py`)
  - [ ] 1-1. Claude parser: dedupe usage by API message id, tool calls + results, rounds, compaction, `subagents/agent-*.jsonl` children
  - [ ] 1-2. Codex parser: `token_count.last_token_usage` calls, (custom_)tool calls + outputs, rounds, `compacted`, children via `parent_thread_id`
  - [ ] 1-3. Normalized model: session → rounds → steps (llm call / tool call / prompt); context burden per step
  - [ ] 1-4. Newest-first listing without parsing all history; summary cache keyed by path+mtime+size under `graphs/token_usage/`
  - [ ] 1-5. Join DAN runs via `native_session_id` in `graphs/native_leads|native_workers/*.json`
- [ ] 2. Activity taxonomy
  - [ ] 2-1. Actions + stages (see Decisions) with deterministic rules from tool name / command
  - [ ] 2-2. Waste flags: duplicate read, oversized output, failed call, repeated command
  - [ ] 2-3. Optional LLM stage labelling through the configured OpenAI-compatible endpoint (`DAN_LLM_*`); model overridable; labels cached
- [ ] 3. Recommendations derived from aggregates and flags
- [ ] 4. API: `GET /api/token-usage/sessions`, `GET /api/token-usage/session`, `POST /api/token-usage/classify`, `GET/PUT /api/token-usage/settings`
- [ ] 5. Settings → Token usage section (session list, rounds, activity breakdown, heaviest steps, flags, advice)
- [ ] 6. Tests (pytest + vitest), docs (changelog, architecture, README, todo)

## Decisions
- Parse native transcripts directly; no ccusage/Langfuse dependency. Works retroactively.
- Context burden of a step = tokens it added × number of later model calls before the next compaction (what re-reading it cost).
- Tool-result tokens are estimated as chars/4; model-call tokens are exact from the transcript.
- Heuristic labels always available offline; LLM labelling is an explicit button because it sends step summaries (tool, truncated arguments, sizes — never tool output) to the provider.
- "JEV" was not found in the repo or in OpenRouter's model list (2026-09-21); classifier model defaults to `DAN_LLM_MODEL` and is editable in Settings.
- Actions: read, search, execute, write, reason, delegate, web, manage, respond.
- Stages: orient, locate, understand, plan, implement, verify, debug, review, environment, vcs, research, delegate, document, data, communicate, overhead.

## Notes
- Claude writes one row per content block, each repeating the message's usage: 344 API messages appeared as 734 rows in a real session.
- 8.7k Codex rollouts locally; listing walks `sessions/YYYY/MM/DD` newest first and stops at the limit.
