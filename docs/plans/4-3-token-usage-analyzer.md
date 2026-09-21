# 4-3: Session token usage analyzer

**Parent:** [4-work-notes-gui](4-work-notes-gui.md)
**Status:** completed
**Goal:** Show which Claude Code / Codex sessions and chat rounds consume the most tokens, attribute the cost to activities, and suggest how to reduce it — from the transcripts already on disk.

## Tasks
- [x] 1. Transcript profiler (`src/dan/native_workers/token_usage.py`)
  - [x] 1-1. Claude parser: dedupe usage by API message id, tool calls + results, rounds, compaction, `subagents/agent-*.jsonl` children
  - [x] 1-2. Codex parser: `token_count.last_token_usage` calls, (custom_)tool calls + outputs, rounds, `compacted`, children via `parent_thread_id`
  - [x] 1-3. Normalized model: session → rounds → steps (llm call / tool call / prompt); context burden per step
  - [x] 1-4. Newest-first listing without parsing all history; summary cache keyed by path+mtime+size under `graphs/token_usage/`
  - [x] 1-5. Join DAN runs via `native_session_id` in `graphs/native_leads|native_workers/*.json`
- [x] 2. Activity taxonomy
  - [x] 2-1. Actions + stages (see Decisions) with deterministic rules from tool name / command
  - [x] 2-2. Waste flags: duplicate read, oversized output, failed call, repeated command
  - [x] 2-3. Optional stage labelling with Jev through OpenRouter's Decisions endpoint; model overridable; labels cached
- [x] 3. Recommendations derived from aggregates and flags
- [x] 4. API: `GET /api/token-usage/sessions`, `GET /api/token-usage/session`, `POST /api/token-usage/classify`, `GET/PUT /api/token-usage/settings`
- [x] 5. Settings → Token usage section (session list, rounds, activity breakdown, heaviest steps, flags, advice)
- [x] 6. Combined all-sessions overview (`GET /api/token-usage/overview`, *All sessions* tab)
- [x] 8. One-line takeaway: costliest stage + the single remedy, on session and overview reports
- [x] 7. Tests (pytest + vitest), docs (changelog, architecture, README, todo)

## Decisions
- Parse native transcripts directly; no ccusage/Langfuse dependency. Works retroactively.
- Context burden of a step = tokens it added × number of later model calls before the next compaction (what re-reading it cost).
- Tool-result tokens are estimated as chars/4; model-call tokens are exact from the transcript.
- Heuristic labels always available offline; LLM labelling is an explicit button because it sends step summaries (tool, truncated arguments, sizes — never tool output) to the provider.
- Jev (TypeSafe) is a *decisions* model: id `jev-latest` / `typesafe/jev-1.13`, served only by `POST /api/alpha/decisions` (`{model, state, questions}` → `answers[q] = {choice, probabilities, confidence}`), absent from `/api/v1/models`, and rejected by chat completions. One choice question per step with `STAGES` as criteria; 24 steps per request to stay inside its 32k context. It is used only by the analyzer.
- Actions: read, search, execute, write, reason, delegate, web, manage, respond.
- Stages: orient, locate, understand, plan, implement, verify, debug, review, environment, vcs, research, delegate, document, data, communicate, overhead.

## Notes
- Follow-ups not done: per-round drill-down to every step, cost in dollars, Cursor/Antigravity transcripts, a root run id spanning one DAN run's Claude and Codex workers (today each native session is analysed separately and marked with its DAN run).
- Overview buckets a session's tokens on its last-activity day.
- Claude writes one row per content block, each repeating the message's usage: 344 API messages appeared as 734 rows in a real session.
- 8.7k Codex rollouts locally; listing walks `sessions/YYYY/MM/DD` newest first and stops at the limit.
