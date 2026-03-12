# 33-1: Manual Pilot

**Parent:** [33-generation-quality-eval](33-generation-quality-eval.md)
**Status:** completed
**Goal:** Send 5-10 workflow-building prompts through the live DAN system, observe what happens in both `agent` and `build` lanes, and establish a rough baseline before building automation.

**Superseded by:** Post-33-9/33-10 pipeline improvements and full battery runs. The pilot baseline (60% pass, 0% intent compiler) is historical — the pipeline now has intent compiler activation (68%), tool-aware extraction, fixture expectation enforcement, and granular failure categories. Re-run via 33-3/33-4/33-5.

## Why manual first

Building a test harness without knowing the rough baseline is premature optimization. If generation works 90% of the time, the harness needs different instrumentation than if it works 20% of the time. 30 minutes of manual testing tells us which world we're in.

## Method

Use a Python script that hits the live server API (`POST /api/chat/message`) with workflow-building prompts and collects results. For each prompt:

1. Create a fresh empty graph via `POST /api/graphs` with a unique workflow ID
2. Record the initial graph state (`GET /api/graphs/{id}`)
3. Send the prompt in the **routing lane** with `mode: "agent"`
4. Stream events via WebSocket (`/api/chat/{channel_id}/events`)
5. Fetch the graph and validate it via `POST /api/graphs/{id}/validate`
6. For prompts that fail in `agent` lane for unclear reasons, re-run them in the **generation lane** with `mode: "build"` on a fresh empty graph
7. Query the **unified telemetry store** (`~/.dan/telemetry.db`, 31-20) for the matching `chat_turn` event to get exact tokens, cost, duration, model, retry count, and any child events (classification, guard, tool_call)
8. Log: prompt, lane, response text, timing, observable events, telemetry data, graph (if any), validation result

## Pilot Prompts (10 prompts, escalating difficulty)

### T1: Trivial (pattern expansions)
1. `"Build a simple 3-step chain: research a topic, analyze findings, write a summary"`
2. `"Create a review loop workflow where a writer drafts content and a reviewer gives feedback until approved"`

### T2: Simple (standard structures)
3. `"Build a workflow that searches the web for recent news on a topic, reads the top 3 results, and produces a briefing"`
4. `"Create a RAG pipeline: ingest PDF documents from a folder, index them, then answer questions using the indexed knowledge"`

### T3: Medium (composition)
5. `"Build a workflow that takes a research question, searches for papers, reads each paper in parallel, then synthesizes findings into a literature review with a review loop"`
6. `"Create a data analysis workflow: read a CSV file, run Python code to compute statistics, generate a chart, and write a report summarizing the findings"`

### T4: Complex (multi-pattern, nested)
7. `"Build a multi-department research system: 3 parallel research teams each investigate a sub-topic with their own review loops, then an orchestrator merges all findings into a final report"`
8. `"Create an equity research workflow: gather company financials from web search, analyze revenue trends with code, assess market sentiment, write an investment memo, and run it through a reviewer panel"`

### T5: Edge cases
9. `"Make something cool"` (ambiguous — should the system ask for clarification?)
10. `"What's the weather today?"` (not a workflow request — should NOT trigger workflow build)

## Tasks

- [x] 1. Build pilot infrastructure (subsumed by `tests/eval/` harness from 33-2)
  - [x] 1-1. HTTP client (`tests/eval/client.py`)
  - [x] 1-2. WebSocket client (`tests/eval/client.py`)
  - [x] 1-3. Graph bootstrap via `POST /api/graphs`
  - [x] 1-4. Timing capture (start → first token → complete)
  - [x] 1-5. Telemetry reader (`tests/eval/telemetry_reader.py`)
  - [x] 1-6. JSONL output with lane + observable event data (`tests/eval/metrics.py`)
- [x] 2. Run pilot against live server
  - [x] 2-1. Execute all 10 prompts sequentially in `build` lane (pilot ran build lane)
  - [x] 2-2. Re-run ambiguous failures in `build` lane on fresh graphs
  - [x] 2-3. Manual review of each result (was it right? what went wrong?)
  - [x] 2-4. Note any crashes, hangs, missing telemetry, or unexpected routing
  - [x] 2-5. For each prompt, record which generation path was used: intent compiler (fast) vs codegen (slow). Result: 50% codegen, 0% intent_compiler, 50% unknown.
  - [x] 2-6. In `agent` lane, check telemetry for `guard_check` events — did the request guard pipeline (31-19) reclassify or short-circuit any prompts? *(harness now fetches guard_check child events from telemetry, populates EvalRecord.guard_events, report shows guard_intervention_rate for agent lane)*
- [x] 3. Produce pilot summary
  - [x] 3-1. Table: prompt | lane | success/fail | time | graph_created | node_count | notes
  - [x] 3-2. Rough baseline: 6/10 succeed in `build` (60% pass rate)
  - [x] 3-3. Generation path breakdown: 50% codegen, 0% intent_compiler, 50% unknown
  - [x] 3-4. List of failure modes observed: all 4 failures = no_graph_created
  - [x] 3-5. Note whether domain detection (31-21) and guard pipeline (31-19) visibly affected any prompts
  - [x] 3-6. Decision: pipeline functional enough for automated testing; T2 web/tool tasks need attention

## Files

| File | Action |
|------|--------|
| `tests/pilot/run_pilot.py` | Create / harden — pilot runner script |
| `tests/pilot/prompts.json` | Create — prompt fixtures |
| `tests/pilot/results/` | Create (gitignored) — JSONL output directory |

## Decisions

- (filled in during execution)

## Notes

- The server must be running (`dan-up`) before the pilot. Current config: deepseek-v3.2, tier: on, learning: on.
- Do **not** rely on `_scratch` for isolated measurements. `_scratch` is special-cased by the server; the pilot should pre-create fresh empty graphs for each case.
- The pilot script is disposable — it's not the final harness. The harness (33-2) will be designed based on pilot findings.
- A prototype runner may exist during this phase, but the task is not complete until the script is verified against the live server.
- **Test isolation:** Use unique workflow IDs (uuid-based) per prompt. Do not set a project context — project-scoped memory (31-18) could leak between prompts if the same project is reused.
- **Pipeline context:** This pilot runs against the Phase 22 optimized pipeline (convenience layer, 15+ intent patterns, smart defaults, domain profiles). The prompts cover its claimed capability range.
