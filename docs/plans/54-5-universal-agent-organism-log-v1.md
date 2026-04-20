# 54-5: Universal Agent Organism Log V1

**Parent:** [54-dan-control-plane-rewrite-around-universal-agents](54-dan-control-plane-rewrite-around-universal-agents.md)
**Status:** completed
**Goal:** Replace DAN Code and DAN Research's product-specific bounded-run logs with one universal-agent-backed `organism_log_v1` contract that captures timed LLM/tool/contract/handoff spans, parallel branches, and blocker relationships in a form future DAN-v2 organisms and visualizers can share.

## Tasks
- [x] 1. Freeze the `organism_log_v1` contract and its boundaries
  - [x] 1-1. Define the root log envelope and file-layout rules for bounded-run logs versus durable control-plane logs, including `product`, `stream_kind`, `session_id`, `turn_id`, `task_id`, `trace_id`, `organism_id`, and `organ_id`
  - [x] 1-2. Define the append-only record types required for graphing and replay, including timed span rows, point event rows, and optional summary/artifact rows with stable ids plus wall-clock timestamps and monotonic ordering data
  - [x] 1-3. Freeze the correlation ids for universal-agent concepts such as `contract_id`, `worker_id`, `cell_id`, `packet_id`, `signal_id`, `tool_call_id`, `model_call_id`, `attempt`, `review_round`, and `fanout_group_id`
  - [x] 1-4. Define explicit dependency and blocking semantics via fields such as `parent_span_id`, `blocked_on`, `wait_reason`, `fanin_group_id`, and `parallel_lane`
  - [x] 1-5. Keep human-facing status rendering and analytics as projections over the shared log instead of competing source-of-truth schemas
- [x] 2. Build the shared organism-log substrate on the universal-agent runtime
  - [x] 2-1. Add one shared writer/collector module under the universal-agent / worker substrate rather than under `dan code` or `dan research`
  - [x] 2-2. Add lightweight span helpers for timed nested scopes and append-only JSONL emission
  - [x] 2-3. Add stable `contract_id` derivation for output contracts / membranes so repeated runs can correlate comparable stages
  - [x] 2-4. Make the writer safe for bounded runs, durable controller turns, and background review loops without buffering the whole trace in memory
- [x] 3. Instrument the universal-agent execution seam
  - [x] 3-1. Emit LLM-call spans from the shared provider-completion adapter, including hedge attempts and streamed completion lifecycle
  - [x] 3-2. Emit tool-call spans from the shared tool membrane/execution path with result status and duration
  - [x] 3-3. Emit contract-validation / normalization / repair spans where worker outputs are checked or repaired against output contracts
  - [x] 3-4. Emit handoff/signal spans from the cell/tissue/organ composition substrate instead of only post-hoc `trace_rows`
  - [x] 3-5. Emit durable-agent control-plane spans for mailbox turns, planning/review calls, continuation loops, and heartbeat/wait states
- [x] 4. Converge DAN Code and DAN Research onto the shared schema
  - [x] 4-1. Make `.dan-code/runs/turn-XX/events.jsonl` conform to `organism_log_v1` without changing the user-facing workspace layout
  - [x] 4-2. Make `.dan-research/runs/turn-XX/events.jsonl` conform to the same schema and move heartbeat/control-plane records onto the same row vocabulary
  - [x] 4-3. Decide whether Research keeps a separate durable `control-plane-events.jsonl` file or a second stream with the same schema, and document that rule clearly
  - [x] 4-4. Normalize report models so Code and Research expose comparable log references and derived run summaries instead of one returning raw `trace_rows` and the other only `stage_records`
- [x] 5. Add derived run-analysis helpers for blocker and critical-path views
  - [x] 5-1. Build a log-reader/projection helper that reconstructs spans, nested stages, parallel lanes, and fan-in/fan-out groups from `organism_log_v1`
  - [x] 5-2. Compute the first useful blocking answers from the log alone: per-span duration, exclusive versus waiting time where possible, direct blocker chain, and critical-path candidates
  - [x] 5-3. Keep the first implementation backend/CLI-first; richer editor visualization can layer on top later without changing the log contract
  - [x] 5-4. Add an adapter/import seam so non-DAN agent logs can normalize into `organism_log_v1` through shared library helpers and a small CLI instead of requiring runtime-specific instrumentation first
  - [x] 5-5. Add the first editor-side Development-mode timeline consumer that discovers logs, requests shared analysis payloads, and renders lanes/blockers/critical-path details without inventing a second UI-only trace schema
- [x] 6. Regressions, docs, and rollout
  - [x] 6-1. Add contract tests for schema stability, id linkage, monotonic ordering, and parallel-branch reconstruction
  - [x] 6-2. Add focused DAN Code and DAN Research regressions proving the shared log captures LLM calls, tool use, contract checkpoints, handoffs, reviews, and terminal states
  - [x] 6-3. Update `docs/architecture.md`, `docs/todo.md`, and product docs once the implementation lands
  - [x] 6-4. Exit only when a real run from both products can answer "what took time?" and "what blocked the next step?" from the shared log

## Decisions
- `organism_log_v1` is a per-run trace contract for universal-agent organisms; it is not a replacement for historical telemetry aggregation.
- The source of truth should live in the shared universal-agent substrate, not in product-specific loggers.
- Existing product file locations can stay stable even if the row schema changes.
- Human-readable status updates remain useful, but they should be derivable from the same shared trace.
- The first slice targets DAN Code and DAN Research because they already prove the durable-controller plus bounded-organism architecture; future DAN-v2 lanes should plug into the same contract.
- UI graph rendering is downstream of the trace contract. The first success criterion is a trustworthy log, not a finished visualizer.

## Notes
- `CrossCellTraceLog` and `OrganismObservability` already provide a partial seam, but they currently capture handoff/signal and stage summaries rather than timed spans for LLM/tool/contract work.
- Research currently has both per-turn run logs and a workspace-level control-plane log; the shared contract must make that split explicit rather than leaving it as an ad hoc second dialect.
- The older unified telemetry work is adjacent but not sufficient here: telemetry answers aggregate analytics questions, while `organism_log_v1` must support per-run dependency and blocker reconstruction.
- Landed first implementation slice: `src/dan/worker/organism_log.py` now owns the shared writer/classification/projection seam, `.dan-code/runs/turn-XX/events.jsonl` and `.dan-research/runs/turn-XX/events.jsonl` now write through that shared contract, and DAN Research keeps its separate workspace-level `.dan-research/control-plane-events.jsonl` file but on the same `organism_log_v1` row vocabulary.
- The current slice also threads stable `trace_id`, `model_call_id`, `tool_call_id`, and `contract_id` through the shared provider/tool/runtime seams, and both product report models now expose `event_log_schema` plus comparable trace references.
- `src/dan/worker/organism_log_adapters.py` now adds the first external-ingest seam: foreign JSON/JSONL logs can either pass through unchanged when they already use `organism_log_v1`, or normalize through a heuristic `generic_json` adapter into the same append-only row vocabulary.
- `src/dan/worker/organism_log_analysis.py` now adds the first explicit analysis contract over the shared schema: it derives timeline lanes, visualization-ready spans, dependency edges, blocker chains, max parallelism, and critical-path candidates from normalized `organism_log_v1` rows without introducing a second trace format.
- `src/dan/cli/organism_log.py` now exposes the shared seam as `dan organism-log` / `dan-organism-log` with `import`, `summarize`, and `analyze` subcommands so arbitrary agent logs can be normalized, summarized, and projected into a future visualizer contract without being emitted by DAN itself.
- `src/dan/server/routers/misc.py` now exposes `/api/organism-logs` plus `/api/organism-logs/analyze`, and the editor Development-mode timeline slot now consumes those endpoints through `editor/src/components/code/DevelopmentTimelinePanel.tsx` / `OrganismLogTimelinePanel.tsx` so the first UI renderer reuses the exact backend analysis payload.
- The reader/discovery seam now also tolerates mixed legacy+`organism_log_v1` files by analyzing the contiguous v1 suffix. This matters for real reused `.dan-research/control-plane-events.jsonl` and `.dan-research/runs/turn-XX/events.jsonl` files that still contain pre-schema rows at the front from older sessions.
- The structured-output seam now emits timed `contract.validation.*` and `contract.repair.*` spans from `src/dan/worker/core/executor.py`, with `validation_phase=initial|repair`, explicit `repair_round`, and `normalization_mode=jsonish_payload` because parse/normalization currently lives inside validation rather than as a separate runtime stage.
- The direct DAN Code path now also forwards those shared executor events into `.dan-code/runs/turn-XX/events.jsonl` through `CallbackEventSink`, so real product runs and not only durable-runner sessions capture the same contract span family.
- `src/dan/worker/composition.py::CrossCellTraceLog` now emits live `trace.row` callbacks on each handoff/signal, the Code and Research CLI run loggers stream those rows straight into `organism_log_v1`, and `src/dan/worker/organism_log.py` projects them back out as zero-duration `cell_handoff` / `supervisory_signal` spans instead of relying only on end-of-run `trace_rows` export.
- `src/dan/worker/runner.py` now forwards durable mailbox/session/background lifecycle events through the shared callback seam, `organism_event_context(...)` now carries `agent_session_id` / `agent_message_id` / `worker_session_id` into executor/model/tool events, and the organism-log reader derives `durable_agent_session`, `durable_mailbox_wait`, `durable_mailbox_turn`, and `durable_background_task` spans with mailbox-turn parentage for worker/model/contract spans.
- DAN Research now routes durable controller callback events onto `.dan-research/control-plane-events.jsonl`, so planning/review mailbox turns, wait states, and their nested model/worker spans sit on the same shared control-plane trace as the explicit CLI `orchestrator.*` stage spans.
- Acceptance is now locked by focused worker/CLI/server regressions that prove the shared log can answer both questions from product-shaped traces: Code logs expose measured LLM/tool durations for "what took time?", and Research control-plane logs expose explicit mailbox-wait blockers for "what blocked the next step?".
