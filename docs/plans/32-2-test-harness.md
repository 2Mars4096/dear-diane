# 32-2: Test Harness

**Parent:** [32-generation-quality-eval](32-generation-quality-eval.md)
**Status:** not-started
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

- [ ] 1. Prompt fixture format
  - [ ] 1-1. JSON schema: `id`, `tier` (T1-T5), `lane` (`agent|build|both`), `prompt`, `tags` (`pure_llm`, `tool_light`, `tool_heavy`, `code`, `human`, `durability`), `expected` (optional: semantic must-haves), `multi_turn_follow_ups` (optional: list of follow-up prompts)
  - [ ] 1-2. Load/validate fixtures from `tests/eval/prompts.json`

- [ ] 2. API client
  - [ ] 2-1. Async HTTP client wrapping `/api/chat/message`, `/api/graphs`, `/api/graphs/{id}`, `/api/graphs/{id}/validate`
  - [ ] 2-2. WebSocket stream consumer for `/api/chat/{channel_id}/events` with timeout
  - [ ] 2-3. Graph bootstrap helper: `POST /api/graphs` with empty graph body for a fresh workflow ID
  - [ ] 2-4. Fresh workflow ID per prompt (uuid-based) to avoid contamination
  - [ ] 2-5. Telemetry reader: query `~/.dan/telemetry.db` via `SQLiteTelemetryStore` or direct SQL for `chat_turn`, `classification`, `guard_check`, `tool_call`, `workflow_run`, `workflow_node` events matching the prompt's timeline / session

- [ ] 3. Metrics collection
  - [ ] 3-1. Timing: prompt_sent_at, first_token_at, complete_at, total_ms (stream events provide wall-clock; telemetry `duration_ms` is authoritative)
  - [ ] 3-2. Build-time tokens: primary source is telemetry `chat_turn` event (`prompt_tokens`, `completion_tokens`, `estimated_cost`). Fallback: `chat_complete.token_usage` from stream events
  - [ ] 3-3. Run-time tokens: primary source is telemetry `workflow_run` / `workflow_node` events. Fallback: `/api/runs/{run_id}/token-breakdown`
  - [ ] 3-4. Repair tracking: telemetry `chat_turn.retry_count` + stream events for intent-compiler vs codegen path signals
  - [ ] 3-5. Classification & guard data: telemetry `classification` (intent, confidence, duration) and `guard_check` (action, duration) events correlated via `parent_event_id`
  - [ ] 3-6. Graph quality: node_count, node_types, edge_count, has_loop, has_fan_out, sub_graph_depth
  - [ ] 3-7. Failure classification: `misrouted`, `no_graph_created`, `validation_error`, `wrong_topology`, `execution_error`, `timeout`

- [ ] 4. Execution testing (optional, flag-gated)
  - [ ] 4-1. For execution-friendly validated graphs, start a run with `POST /api/runs` using `{graph_id, inputs}`
  - [ ] 4-2. Stream run events via `/api/runs/{run_id}/events` or poll `/api/runs/{run_id}`
  - [ ] 4-3. Record execution result: success/error/timeout, duration, node-level outcomes
  - [ ] 4-4. Do not assume arbitrary live-tool mocking exists; either use execution-friendly prompts or add a small deterministic test-tool pack before enabling broad execution measurement

- [ ] 5. JSONL logging
  - [ ] 5-1. One record per prompt+lane: `{id, tier, lane, prompt, model, timestamp, timing, build_tokens, run_tokens, observed_events, telemetry, graph_created, graph_id, graph_summary, validation, execution, status, error, response_text}`
  - [ ] 5-2. Append to `tests/eval/results/{timestamp}.jsonl`
  - [ ] 5-3. Store generated graph JSON alongside (for debugging failed cases)

- [ ] 6. Report generator
  - [ ] 6-1. Read JSONL, compute per-tier and per-lane: total, passed, failed, pass_rate, avg_tokens, avg_time, failure_mode_distribution
  - [ ] 6-2. Compute overall: total, passed, failed, pass_rate, total_build_tokens, total_run_tokens, total_time
  - [ ] 6-3. Top-N token consumers, top-N slowest, top-N most repair-heavy cases
  - [ ] 6-4. Failure mode summary: count per failure type, example prompt for each
  - [ ] 6-5. Lane comparison: prompts that fail in `agent` but pass in `build` (routing issue) vs fail in both (generation issue)
  - [ ] 6-6. Print to stdout (Rich table) + save as JSON

- [ ] 7. CLI interface
  - [ ] 7-1. `python -m tests.eval` — run all prompts, produce report
  - [ ] 7-2. `python -m tests.eval --tier T1` — run only tier T1
  - [ ] 7-3. `python -m tests.eval --prompt "Build a chain"` — run a single ad-hoc prompt
  - [ ] 7-4. `python -m tests.eval --report results/2026-03-11.jsonl` — regenerate report from existing JSONL
  - [ ] 7-5. `python -m tests.eval --execute` — enable execution testing (off by default)
  - [ ] 7-6. `python -m tests.eval --model gpt-4o` — set model via `/model` command before prompts
  - [ ] 7-7. `python -m tests.eval --lane agent|build|both` — choose evaluation lane

## Files

| File | Action |
|------|--------|
| `tests/eval/__init__.py` | Create |
| `tests/eval/__main__.py` | Create — CLI entry point |
| `tests/eval/runner.py` | Create — core test runner |
| `tests/eval/client.py` | Create — API client (chat + graphs + runs + validate) |
| `tests/eval/metrics.py` | Create — metrics collection and JSONL logging |
| `tests/eval/telemetry_reader.py` | Create — reads `~/.dan/telemetry.db` (31-20) for tokens, cost, duration, model, retry, classification, guard data |
| `tests/eval/report.py` | Create — report generation |
| `tests/eval/prompts.json` | Create — prompt fixtures (from 32-3 and 32-4) |
| `tests/eval/results/` | Create (gitignored) — output directory |

## Decisions

- (filled in during execution)

## Notes

- The harness depends on a running server (`dan-up`). It does NOT start/stop the server.
- Token and cost data come from the unified telemetry store (31-20). Every `chat_turn` event records exact `prompt_tokens`, `completion_tokens`, `estimated_cost`, and `duration_ms`. Child events (`classification`, `guard_check`, `tool_call`) are linked via `parent_event_id`. Stream events remain useful for generation-path signals (intent vs codegen path), but are not needed for token/cost/latency metrics.
- Each prompt uses a unique, pre-created empty workflow ID to prevent cross-contamination. The harness cleans up created graphs after the run (optional `--keep-graphs` flag).
