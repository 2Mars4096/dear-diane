# 52-5: DAN Code Orchestrator Feedback

**Parent:** [52-multicellular-composition](52-multicellular-composition.md)
**Status:** completed
**Goal:** Make the DAN Code orchestrator narrate public run progress clearly while keeping the implementation on the universal-agent substrate.

## Tasks
- [x] 1. Enrich coding-organism public lifecycle events with the payloads the CLI needs to explain the run
  - [x] 1-1. Include orchestrator plan details such as worker briefs in public stage-completed events
  - [x] 1-2. Include aggregated candidate file/test details and validator gap details in public stage-completed events
  - [x] 1-3. Emit structured `status.update` events so orchestrator/worker/aggregator/validator/delivery narration can come from runtime payloads instead of fixed CLI copy
- [x] 2. Improve the DAN Code CLI progress renderer so the orchestrator speaks in assistant-style updates instead of low-level stage labels
  - [x] 2-1. Keep hidden chain-of-thought private while narrating planning, worker execution, repair, and completion publicly
  - [x] 2-2. Compress noisy tool summaries so directory and file reads stay readable in the console
  - [x] 2-3. Keep lifecycle output deterministic (`[status]`) while rendering dynamic assistant updates and worker-scoped trace/tool prefixes from structured event fields
- [x] 3. Tighten the end-of-run CLI report so selected files and validation commands are easier to scan
- [x] 4. Add focused regressions for the new public narration and event payloads

## Decisions
- Keep `dan code` on the dedicated coding organism built from universal-worker primitives instead of adding a separate chat runtime.
- Treat the orchestrator as the public bridge between the user and the worker pool; the CLI should render that bridge explicitly.
- Keep typo greetings like `hii` off the local fast-path; only explicit local/meta prompts stay local.

## Notes
- `CodeProgressRenderer` now renders `[assistant]` narration for orchestration, workers, aggregation, validation, repair, and completion.
- `execute_coding_organism(...)` now emits richer public stage payloads so the CLI can mention worker briefs, candidate files, validation commands, and validator gaps without reading private scratchpad text.
- `status.update` is now the main public assistant contract for `dan code`; fixed lifecycle/status text stays in deterministic `[status]` lines, and parallel model/tool traces carry `worker_id` labels so interleaved workers stay readable.
- The orchestrator plan now also carries a formal `public_response`, so the first public assistant line can state the interpreted user intent and next action before the worker plan details.
