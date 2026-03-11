# 33-2: Test Harness

**Parent:** [33-generation-quality-eval](33-generation-quality-eval.md)
**Status:** completed
**Goal:** Build the automated test runner that sends prompt batteries through the live concierge API, compares `agent` vs `build` lanes, collects metrics, and produces a summary report.

## Design

The harness is a standalone Python script (not pytest) that:
1. Loads prompt fixtures from a JSON file
2. For each prompt, pre-creates a fresh empty graph and sends the prompt through `POST /api/chat/message`
3. Streams chat events and captures generation-path signals (`chat_intent_extracted`, `chat_code_generated`, `chat_validation_result`, `chat_graph_created`, `chat_complete`)
4. After each prompt, queries the **unified telemetry store** (`~/.dan/telemetry.db`) for the matching `chat_turn` event and any child events (classification, guard, tool_call) to get exact tokens, cost, duration, model, and retry count
5. Validates the resulting graph via `/api/graphs/{id}/validate`
6. Optionally attempts execution for an execution-friendly subset, then queries telemetry for `workflow_run` / `workflow_node` events
7. Logs every step as a JSONL record (merging stream signals + telemetry data)
8. Produces a summary report (stdout + JSON file)

### Why standalone, not pytest

Pytest is for deterministic pass/fail assertions. This harness measures *quality* against non-deterministic LLM outputs. A 70% pass rate isn't a test failure — it's a data point. The harness produces a report, not a test verdict.

## Tasks

- [x] 1. Prompt fixture format
  - [x] 1-1. JSON schema with all fields
  - [x] 1-2. Load/validate fixtures from `tests/eval/prompts.json`

- [x] 2. API client (`tests/eval/client.py`)
  - [x] 2-1. Async HTTP client via httpx
  - [x] 2-2. WebSocket stream consumer via websockets
  - [x] 2-3. Graph bootstrap helper
  - [x] 2-4. Fresh workflow ID per prompt (uuid-based)
  - [x] 2-5. Telemetry reader (`tests/eval/telemetry_reader.py`) — direct SQLite reader

- [x] 3. Metrics collection (`tests/eval/runner.py`, `tests/eval/metrics.py`)
  - [x] 3-1. Timing: prompt_sent_at, first_token_at, complete_at, total_ms
  - [x] 3-2. Build-time tokens from telemetry `chat_turn` event
  - [x] 3-3. Run-time tokens from telemetry `workflow_run` / `workflow_node` events
  - [x] 3-4. Generation path tracking (intent_compiler vs codegen)
  - [x] 3-5. Guard event collection from stream events
  - [x] 3-6. Graph quality: node_count, node_types, edge_count, has_loop, has_fan_out
  - [x] 3-7. Generation path per-tier breakdown
  - [x] 3-8. Domain detection from stream event metadata
  - [x] 3-9. Failure classification: misrouted, no_graph_created, validation_error, timeout, guard_short_circuit, error

- [x] 4. Execution testing (optional, `--execute` flag)
  - [x] 4-1. Start run via `POST /api/runs`
  - [x] 4-2. Poll `GET /api/runs/{run_id}` with 2s intervals, 120s timeout
  - [x] 4-3. Record execution result: status/error/timeout, duration

- [x] 5. JSONL logging (`tests/eval/metrics.py`)
  - [x] 5-1. One EvalRecord per prompt+lane via Pydantic model_dump_json
  - [x] 5-2. Append to `tests/eval/results/{timestamp}_{tag}.jsonl`
  - [x] 5-3. Store generated graph JSON alongside (`log_graph` → `{stem}_graphs/{id}_{lane}.json`; `--no-store-graphs` to disable)

- [x] 6. Report generator (`tests/eval/report.py`)
  - [x] 6-1. Per-tier and per-lane breakdowns
  - [x] 6-2. Overall stats
  - [x] 6-3. Top-N token consumers, top-N slowest
  - [x] 6-4. Failure mode summary with examples
  - [x] 6-5. Lane comparison (agent-only / build-only / both-fail)
  - [x] 6-6. Generation path breakdown per tier
  - [x] 6-7. Rich table output with plain-text fallback + JSON save

- [x] 7. CLI interface (`tests/eval/__main__.py`)
  - [x] 7-1. `python -m tests.eval` — run all prompts
  - [x] 7-2. `python -m tests.eval --tier T1` — repeatable tier filter
  - [x] 7-3. `python -m tests.eval --prompt "..."` — ad-hoc prompt
  - [x] 7-4. `python -m tests.eval --report results/X.jsonl` — report-only mode
  - [x] 7-5. `python -m tests.eval --execute` — execution testing
  - [x] 7-6. `python -m tests.eval --model gpt-4o` — model stored in records
  - [x] 7-7. `python -m tests.eval --lane agent|build|both`

## Files

| File | Action |
|------|--------|
| `tests/eval/__init__.py` | Create |
| `tests/eval/__main__.py` | Create — CLI entry point |
| `tests/eval/runner.py` | Create — core test runner |
| `tests/eval/client.py` | Create — API client (chat + graphs + runs + validate) ✓ |
| `tests/eval/metrics.py` | Create — metrics collection and JSONL logging |
| `tests/eval/telemetry_reader.py` | Create — reads `~/.dan/telemetry.db` (31-20) for tokens, cost, duration, model, retry, classification, guard data ✓ |
| `tests/eval/report.py` | Create — report generation |
| `tests/eval/prompts.json` | Create — prompt fixtures (from 33-3 and 33-4) |
| `tests/eval/results/` | Create (gitignored) — output directory |

## Decisions

- (filled in during execution)

## Notes

- The harness depends on a running server (`dan-up`). It does NOT start/stop the server.
- Token and cost data come from the unified telemetry store (31-20). Every `chat_turn` event records exact `prompt_tokens`, `completion_tokens`, `estimated_cost`, and `duration_ms`. Child events (`classification`, `guard_check`, `tool_call`) are linked via `parent_event_id`. Stream events remain useful for generation-path signals (intent vs codegen path), but are not needed for token/cost/latency metrics.
- Each prompt uses a unique, pre-created empty workflow ID to prevent cross-contamination. The harness cleans up created graphs after the run (optional `--keep-graphs` flag).
- **Test isolation:** Do not set project context in requests. Project-scoped memory (31-18) and domain learning (31-21) accumulate state that can bleed between prompts if the same project is reused. Unique workflow IDs per prompt are sufficient for isolation.
- **Reproducibility:** Self-adaptive behavior (31-22) means prompts and thresholds may drift. If `DAN_BEHAVIOR_TIER >= 2`, log the active behavior snapshot (`BehaviorChangeLog` state) at the start of the run for reproducibility.
- **Pilot improvements (2026-03-11):** Five enhancements added during pilot execution: (1) 90s wall-clock timeout in `_consume_stream()` to prevent progress-ack pings from keeping the stream alive indefinitely. (2) Clarification auto-reply loop in `run_single()` — detects server prompts like "reuse/adapt/start fresh?" and "please confirm" and responds automatically. (3) Progress-ack event filtering — `_consume_stream()` skips `chat_complete` events with `detected_mode="progress_ack"` and only breaks on final terminal events. (4) Richer event data capture — events stored with key fields (message_id, content, detected_mode) instead of just type. (5) Proper validation response handling — `_validate()` infers `passed=True` when `errors=[]` since the validate endpoint doesn't return a `valid` field.
