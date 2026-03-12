# DAN Pilot Evaluation Findings — 2026-03-11

**Source:** `tests/eval/results/2026-03-11_223338_run.jsonl`  
**Lane:** build only  
**Prompts:** p01–p10 (pilot battery)

---

## 1. Per-Record Analysis

### p01 — T1 (Trivial)
| Field | Value |
|-------|-------|
| **Prompt** | Build a simple 3-step chain: research a topic, analyze findings, write a summary |
| **Tier** | T1 |
| **Status** | passed |
| **Failure mode** | — |
| **Graph created** | true |
| **Graph summary** | node_count: 3, edge_count: 2 |
| **Generation path** | codegen |
| **Response text** | Workflow created with 3 nodes and 2 edges. |
| **Timing** | 73,508 ms (~73.5 s) |
| **Error** | — |
| **Observed events** | 12 total: chat_complete (10), chat_intent_extracted (1), chat_code_generated (1), chat_validation_result (1), chat_graph_created (1) |
| **Validation** | passed |

---

### p02 — T1 (Trivial)
| Field | Value |
|-------|-------|
| **Prompt** | Create a review loop workflow where a writer drafts content and a reviewer gives feedback until approved |
| **Tier** | T1 |
| **Status** | passed |
| **Failure mode** | — |
| **Graph created** | true |
| **Graph summary** | node_count: 2, edge_count: 1 |
| **Generation path** | codegen |
| **Response text** | Workflow created with 2 nodes and 1 edges. |
| **Timing** | 21,886 ms (~22 s) |
| **Error** | — |
| **Observed events** | 6 total: chat_complete (2), chat_intent_extracted (1), chat_code_generated (1), chat_validation_result (1), chat_graph_created (1) |
| **Validation** | passed |

**Note:** Expected review_loop topology (3+ nodes with gate); produced 2 nodes, 1 edge — likely simplified or incomplete.

---

### p03 — T2 (Simple)
| Field | Value |
|-------|-------|
| **Prompt** | Build a workflow that searches the web for recent news on a topic, reads the top 3 results, and produces a briefing |
| **Tier** | T2 |
| **Status** | failed |
| **Failure mode** | no_graph_created |
| **Graph created** | false |
| **Graph summary** | — |
| **Generation path** | — |
| **Response text** | (empty) |
| **Timing** | 83,349 ms (~83 s) |
| **Error** | wall_timeout |
| **Observed events** | 9 total: all chat_complete (progress_ack). No intent_extracted, code_generated, or graph_created |
| **Validation** | — |

**Note:** Stuck in “Planning the workflow” for ~70 s, then harness timed out at 90 s. Never reached workflow_build.

---

### p04 — T2 (Simple)
| Field | Value |
|-------|-------|
| **Prompt** | Create a RAG pipeline: ingest PDF documents from a folder, index them, then answer questions using the indexed knowledge |
| **Tier** | T2 |
| **Status** | failed |
| **Failure mode** | no_graph_created |
| **Graph created** | false |
| **Graph summary** | — |
| **Generation path** | — |
| **Response text** | [DAN - create-a-rag-pipeline-ingest-pdf] Meta session started: 0cb794813ae1445f |
| **Timing** | 62,741 ms (~63 s) |
| **Error** | — |
| **Observed events** | 10 total: chat_complete (10). Last two: “Please confirm before I do that.” and “Meta session started” |
| **Validation** | — |

**Note:** Classification-related: system asked for confirmation and started a meta session instead of building a graph. Harness clarification auto-reply may not have matched this pattern.

---

### p05 — T3 (Medium)
| Field | Value |
|-------|-------|
| **Prompt** | Build a workflow that takes a research question, searches for papers, reads each paper in parallel, then synthesizes findings into a literature review with a review loop |
| **Tier** | T3 |
| **Status** | failed |
| **Failure mode** | no_graph_created |
| **Graph created** | false |
| **Graph summary** | — |
| **Generation path** | — |
| **Response text** | (empty) |
| **Timing** | 145,046 ms (~145 s) |
| **Error** | stream: sent 1011 (internal error) keepalive ping timeout; no close frame received |
| **Observed events** | 0 |
| **Validation** | — |

**Note:** WebSocket/stream failure before any events. Likely LLM API or proxy keepalive timeout.

---

### p06 — T3 (Medium)
| Field | Value |
|-------|-------|
| **Prompt** | Create a data analysis workflow: read a CSV file, run Python code to compute statistics, generate a chart, and write a report summarizing the findings |
| **Tier** | T3 |
| **Status** | passed |
| **Failure mode** | — |
| **Graph created** | true |
| **Graph summary** | node_count: 5, edge_count: 4 |
| **Generation path** | codegen |
| **Response text** | Workflow created with 5 nodes and 4 edges. |
| **Timing** | 27,135 ms (~27 s) |
| **Error** | — |
| **Observed events** | 6 total: chat_complete (2), chat_intent_extracted (1), chat_code_generated (1), chat_validation_result (1), chat_graph_created (1) |
| **Validation** | passed |

---

### p07 — T4 (Complex)
| Field | Value |
|-------|-------|
| **Prompt** | Build a multi-department research system: 3 parallel research teams each investigate a sub-topic with their own review loops, then an orchestrator merges all findings into a final report |
| **Tier** | T4 |
| **Status** | failed |
| **Failure mode** | no_graph_created |
| **Graph created** | false |
| **Graph summary** | — |
| **Generation path** | — |
| **Response text** | [DAN - build-a-multi-department-research-system] Meta session started: 2739dd508e1a413d |
| **Timing** | 8,970 ms (~9 s) |
| **Error** | — |
| **Observed events** | 2 total: chat_complete (2). “Please confirm before I do that.” and “Meta session started” |
| **Validation** | — |

**Note:** Same pattern as p04: immediate confirmation prompt and meta session, no build. Very fast failure (~9 s).

---

### p08 — T4 (Complex)
| Field | Value |
|-------|-------|
| **Prompt** | Create an equity research workflow: gather company financials from web search, analyze revenue trends with code, assess market sentiment, write an investment memo, and run it through a reviewer panel |
| **Tier** | T4 |
| **Status** | passed |
| **Failure mode** | — |
| **Graph created** | true |
| **Graph summary** | node_count: 1, edge_count: 0 |
| **Generation path** | codegen |
| **Response text** | Workflow created with 1 nodes and 0 edges. |
| **Timing** | 156,423 ms (~156 s) |
| **Error** | — |
| **Observed events** | 27 total: chat_tool_call_start (8), chat_tool_call_result (8), chat_complete (11), chat_token (1), chat_code_generated (1), chat_validation_result (1), chat_graph_created (1) |
| **Validation** | passed |

**Note:** Graph is valid but minimal: 1 node, 0 edges. Expected 10–15 nodes. LLM produced a detailed plan in text but the generated graph is effectively empty.

---

### p09 — T5 (Edge)
| Field | Value |
|-------|-------|
| **Prompt** | Make something cool |
| **Tier** | T5 |
| **Status** | passed |
| **Failure mode** | — |
| **Graph created** | false |
| **Graph summary** | — |
| **Generation path** | codegen |
| **Response text** | (empty) |
| **Timing** | 74,463 ms (~74 s) |
| **Error** | wall_timeout |
| **Observed events** | 13 total: chat_complete (11), chat_code_generated (1), chat_validation_result (1), chat_tool_call_start (1), chat_tool_call_result (1). No chat_graph_created |
| **Validation** | — |

**Note:** T5 edge case — expected: ask for clarification, NOT build a workflow. Outcome: no graph created ✓. Harness passed because graph_created=false is correct. System reached codegen and validation but timed out before graph_created; unclear if graph would have been created. Timeout is harness-side; outcome matches expected behavior.

---

### p10 — T5 (Edge)
| Field | Value |
|-------|-------|
| **Prompt** | What's the weather today? |
| **Tier** | T5 |
| **Status** | passed |
| **Failure mode** | — |
| **Graph created** | false |
| **Graph summary** | — |
| **Generation path** | — |
| **Response text** | (empty) |
| **Timing** | 80,041 ms (~80 s) |
| **Error** | wall_timeout |
| **Observed events** | 8 total: all chat_complete (progress_ack). Stuck in “Gathering relevant context” |
| **Validation** | — |

**Note:** T5 edge case — expected: answer directly, NOT build a workflow. Outcome: no graph created ✓. System never left context-gathering; no workflow build attempted. Harness passed because graph_created=false is correct for edge cases.

---

## 2. Summary Patterns

### 2.1 Tier Success vs Failure

| Tier | Passed | Failed | Pass Rate |
|------|--------|-------|-----------|
| T1   | 2 (p01, p02) | 0 | 100% |
| T2   | 0 | 2 (p03, p04) | 0% |
| T3   | 1 (p06) | 1 (p05) | 50% |
| T4   | 1 (p08) | 1 (p07) | 50% |
| T5   | 2 (p09, p10) | 0 | 100%* |

\* T5 “passed” = no graph created (correct for edge cases). Both hit wall_timeout; p10 never attempted workflow build.

### 2.2 What Distinguishes Passing vs Failing Prompts

**Passing (workflow-build prompts):**
- Reach `workflow_build` phase quickly (often within 20–30 s)
- Emit full pipeline: `chat_intent_extracted` → `chat_code_generated` → `chat_validation_result` → `chat_graph_created`
- No “Please confirm before I do that” or meta-session handoff
- Simpler prompts (T1 chain, T1 review_loop, T3 data analysis) succeed more often

**Failing (workflow-build prompts):**
- **Timeout:** Stuck in “Planning the workflow” (p03) or “Gathering relevant context” (p10) until 90 s
- **Classification / confirmation:** System asks for confirmation and starts meta session instead of building (p04, p07)
- **Infrastructure:** WebSocket/stream error before any events (p05)

### 2.3 Failure Modes: Timeout vs Classification

| Failure Mode | Count | Examples |
|--------------|-------|----------|
| **Timeout (wall_timeout)** | 3 | p03, p09, p10 |
| **Classification / confirmation** | 2 | p04, p07 (“Please confirm”, meta session) |
| **Stream / infrastructure** | 1 | p05 (keepalive ping timeout) |

**Timeout-related:** p03, p05, p09, p10 (4 of 6 failures involve timeout or stream error)  
**Classification-related:** p04, p07 (2 of 6 — confirmation prompt, no build)

### 2.4 Graph Validity (validation.passed)

All graphs that were created passed validation:

| ID | graph_created | validation.passed |
|----|---------------|------------------|
| p01 | true | true |
| p02 | true | true |
| p06 | true | true |
| p08 | true | true |

**Graph quality caveats:**
- **p02:** Expected review_loop (3+ nodes, gate); got 2 nodes, 1 edge — likely underspecified
- **p08:** Expected 10–15 nodes; got 1 node, 0 edges — structurally valid but semantically wrong

### 2.5 Generation Path

- **intent_compiler:** 0 prompts
- **codegen:** 6 prompts (p01, p02, p06, p08, p09; p09 timed out before graph_created)
- **unknown / null:** 4 prompts (p03, p04, p05, p07, p10 — all failed before codegen)

No prompt used the intent compiler path; all successful builds used codegen.

---

## 3. Recommendations

1. **Increase or tune timeout:** p03 was close to completion (planning for ~70 s); 90 s may be too short for complex prompts.
2. **Clarification auto-reply:** p04 and p07 hit “Please confirm before I do that” and meta session. Extend harness to detect and auto-reply to this pattern.
3. **LLM API stability:** p05 failed with keepalive timeout; investigate proxy/API configuration.
4. **Intent compiler usage:** 0% intent_compiler; expand pattern coverage so T1/T2 prompts can use deterministic path.
5. **Graph quality checks:** Add semantic checks (e.g. node count vs expected range) — p08’s 1-node graph is valid but not useful.
6. **T5 behavior:** p09 and p10 “passed” by outcome (no graph) but p10 never attempted workflow build. Consider separate metrics for “correct refusal” vs “timeout before decision”.

---

## 4. Addendum — 2026-03-12 Runs

### 4.1 Full Battery (build lane)
- **Source:** `tests/eval/results/2026-03-12_001813_run.jsonl`
- **51 records, 54.9% pass rate**
- **Tier breakdown:** T1 72.7%, T2 50%, T2R 0%, T3 52.9%, T4 33.3%, T5 83.3%
- **Durability:** D1–D4 run on first valid graph

### 4.2 Pilot Both Lanes (partial — 012224)
- **Source:** `tests/eval/results/2026-03-12_012224_run.jsonl` (12 records, p01–p06)
- **Agent lane:** 0% pass (6/6 failed)
- **Build lane:** 50% pass (3/6)
- **Lane comparison:** 3 agent-only failures (p01: reuse prompt, p02/p04: routing_blocked); 3 both-fail (p03, p05, p06)
- **Findings:** Agent lane blocked by clarification prompts — auto-reply may need tuning for multi-turn agent flow.

### 4.3 Root Cause — chat_queued ~5ms Failures (fixed 2026-03-12)
- **Symptom:** Runs where most prompts finished in ~5ms with only `chat_queued` observed and `no_graph_created`.
- **Cause:** When the dispatcher queues same-project requests, the server emits `chat_queued` with a replacement `stream_channel_id` and closes the original WebSocket. The eval client was connecting only to the initial channel; when it closed after `chat_queued`, the client stopped instead of reconnecting to the queued channel.
- **Fix:** `tests/eval/client.py` `stream_events()` now follows redirects in a loop (mirrors `telegram_fleet._iter_chat_stream_events()`). Loop detection via `seen_channels`; reconnect-on-close retained for transient failures.
- **Also fixed:** Exception fallback `str(exc) or repr(exc) or type(exc).__name__` applied consistently in `run_single`, `run_multi_turn`, and top-level handler (setup-failure and unhandled errors).
