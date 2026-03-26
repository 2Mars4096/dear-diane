# 33-4: Complex Workflow Battery

**Parent:** [33-generation-quality-eval](33-generation-quality-eval.md)
**Status:** in-progress
**Goal:** Test workflow generation on complex, multi-pattern, real-world-scale prompts, multi-turn progressive refinement sequences, durability smoke checks, and a first-wave long-running benchmark-prep slice on frozen inputs. These stress the limits of generation and reveal composition, wiring, execution, and workflow-stability failure modes.

**Prior run (2026-03-12):** T4: 3/6 (50%) pass, multi-turn: 8/10 turns (80%). Durability D1-D4 run on first valid graph.

**Re-run context:** Same pipeline patches as 33-3 apply here. Additional T4-specific impacts:

| Patch | Expected T4 impact |
|---|---|
| 33-10 B (fixture enforcement) | T4 has strict `min_nodes: 10-12` — graphs that pass structurally but have too few nodes will fail with `expectation_mismatch` |
| 33-10 C (tool keyword map) | T4 prompts are tool-heavy (web_search, code_execution, csv_read, send_email) — tool_ids should be correct |
| 33-10 F (tool-aware extraction) | Better tool-node typing for complex multi-tool workflows |
| 33-9 C (node count calibration) | Codegen node-count guidance calibrated from 42 graphs — T4 should produce appropriately-sized graphs |
| 33-9 E (agent lane routing) | Agent lane should work at ~20% (was 0%) — run T4 in both lanes if feasible |
| 33-10 A (review-loop polarity) | PENDING — t4-01, t4-02, t4-03 all expect review loops; condition may be backwards |

## T4: Complex Workflows (4 prompts)

These require nested sub-graphs, multi-department coordination, or deep pipelines (10+ nodes). They push the boundaries of what the builder codegen can produce in one shot. Domain generation profiles (32-5) should activate for equity/research/data analysis prompts, injecting domain-appropriate tools and model tiers.

| ID | Prompt | Key Features | Expected Nodes |
|----|--------|-------------|---------------|
| t4-01 | "Build a multi-department research system: create 3 parallel research teams — one for market analysis, one for technical assessment, one for competitive intelligence. Each team should have its own research-review loop. An orchestrator collects all findings and produces a unified strategic report." | parallel_subagents, nested review_loops, orchestrator | 12-20 |
| t4-02 | "Create an end-to-end academic paper writing workflow: start with a literature search using web search, organize papers by theme, generate an outline, write each section in parallel (intro, methods, results, discussion), assemble into a draft, run through a review panel with up to 3 revision cycles, compile to LaTeX PDF" | fan_out, review_loop, tools, code, chain | 10-18 |
| t4-03 | "Build an equity research workflow: gather company financials from web searches (revenue, margins, growth), pull recent news and analyst coverage, run Python code to compute financial ratios and trend analysis, generate comparison charts, assess market sentiment, write an investment thesis with bull/bear cases, route through a senior analyst review loop, produce a final PDF memo" | chain, tools, code, review_loop, fan_out | 10-15 |
| t4-04 | "Create a Kaggle competition pipeline: read the dataset from a CSV file, perform exploratory data analysis with Python code, engineer features, train and evaluate 3 models in parallel (random forest, XGBoost, neural net), ensemble the best performers, generate a submission file, write a summary report of methodology and results" | fan_out, code, chain, tools | 10-15 |

## Practical Real-World Tasks (3 prompts)

These simulate what a real user would actually ask DAN to build — natural phrasing, mixed tool usage, practical domain context. Unlike the synthetic prompts above, these are written in first person with real-world motivation. prac-01 also has multi-turn follow-ups to test iterative refinement on a practical workflow.

| ID | Prompt | Key Features | Expected Nodes |
|----|--------|-------------|---------------|
| prac-01 | Weekly supply-chain risk monitor: search news, score disruptions with code, categorize by sector, write exec brief, email ops team | chain + tools + code + email | 5-12 |
| prac-02 | Competitive intelligence pipeline: fan-out across 3 competitors, code-based feature comparison, sentiment analysis, strategic memo with review cycle | fan_out + tools + code + review_loop | 8-18 |
| prac-03 | Weekly meeting prep: read notes file, search for project updates, draft agenda, parse action items with code, write markdown | chain + tools + code | 4-8 |

prac-01 has 2 follow-ups: add review loop, then fan out search across 3 regions. This tests progressive refinement on a practical workflow.

## Long-Running Benchmark-Prep Workloads (first wave)

These are not generic T4 prompts. They are the **bridge from Phase 33 into the benchmark suite**: longer-running, execution-first workloads with fixed inputs and clearer scoring. The objective is to prove DAN can build and run complex workflows honestly before external benchmarks or public claims.

### LR1: Literature review on a frozen corpus

- Input: a fixed local corpus of 20-30 PDFs or markdown papers
- Flow: ingest/search -> parallel read/extract -> theme synthesis -> draft -> reviewer loop -> final report
- Features stressed: fan-out, review loop, context projection, long wall-clock execution
- Scoring: citation coverage + LLM judge + artifact completeness

### LR2: Data analysis report on a fixed package

- Input: one versioned CSV bundle plus expected summary statistics
- Flow: load -> clean -> analyze -> chart -> write report -> validate claims against computed outputs
- Features stressed: code execution, tool wiring, validation, honest failure on missing code
- Scoring: fully automated; textual claims must match computed outputs

### LR3: Code migration with tests and resume

- Input: a prepared repo fixture with failing Python 2-era code and a deterministic test suite
- Flow: scan -> classify -> parallel transform -> run tests -> fix/retest loop -> final migration report
- Features stressed: long-running execution, code generation, checkpoint/resume, iterative repair
- Scoring: test pass rate, recovery behavior, and whether resume works after interruption

Wave 1 execution order:

1. LR2 first — most automated and easiest to score honestly
2. LR1 second — strongest product/story demo
3. LR3 third — strongest systems/architecture demo

LR4 competitive intelligence and LR5 fact-checking remain useful, but they are second-wave once the three workloads above are stable.

## Multi-Turn Progressive Refinement (3 sequences)

These test whether users can build workflows incrementally through conversation. Each sequence is 2-4 turns. Progressive NL refinement (32-4) added structural mutation macros — turns like "add a review loop" and "make it fan out" should use targeted mutations rather than full rebuilds. After 32-6, at least one follow-up should also be a compound structural request in a single message.

### Sequence M1: Start simple, add complexity

| Turn | Prompt |
|------|--------|
| 1 | "Build a simple 3-step chain: research, analyze, summarize" |
| 2 | "Now add a review loop after the summarize step — a reviewer checks quality and sends it back for revision if needed" |
| 3 | "Make the research step fan out to search 3 different sources in parallel" |

**Expected outcome:** The final graph has fan-out → analyze → summarize → review loop. Each turn should modify the existing graph, not rebuild from scratch.

### Sequence M2: Refine by adding tools

| Turn | Prompt |
|------|--------|
| 1 | "Create a workflow that produces a market briefing on a given company" |
| 2 | "Add web search to gather real-time stock price and recent news" |
| 3 | "Include a code node that calculates financial ratios from the gathered data" |
| 4 | "Add a final step that emails the briefing to the user" |

**Expected outcome:** Progressive enhancement of a single workflow. Tool nodes added incrementally.

### Sequence M3: Restructure existing workflow

| Turn | Prompt |
|------|--------|
| 1 | "Build a document processing pipeline: read document, extract key points, write summary" |
| 2 | "Actually, I need to handle multiple documents — make it process a list of documents in parallel" |
| 3 | "Add a comparison step after all documents are processed that identifies common themes across them" |

**Expected outcome:** Turn 2 restructures from chain to fan-out. Turn 3 adds post-merge analysis.

## Execution Testing

For T4 prompts that generate valid graphs, attempt execution:

1. Start a run via `POST /api/runs` with `{graph_id, inputs}`
2. Monitor run events for 60 seconds (or until completion) via `/api/runs/{run_id}/events`
3. Record: did it start? Which nodes executed? Where did it fail? How long did it take?

Execution testing is best-effort — many T4 workflows need real tools (web_search, file_read) that may not work in test context. The value is seeing *how far* execution gets, not whether it produces perfect output.

## Durability Smoke Checks (4 checks)

Complex generation is only part of the story. We also need to know whether generated workflows remain usable after they are created.

### D1: Repeat-run stability

Take one execution-friendly generated workflow and run it 3 times with the same inputs.

**Checks:**
- Does each run start successfully?
- Does the workflow complete all 3 times, or fail intermittently?
- Are run-time token counts and durations in the same rough range?

### D2: Reload + validate stability

After a workflow is generated:

1. Fetch it from `/api/graphs/{id}`
2. Re-fetch it in a fresh harness process
3. Re-run `/api/graphs/{id}/validate`

**Checks:**
- Does the graph persist exactly enough to remain valid?
- Does the graph revision remain stable across reloads when untouched?

### D3: Export / import stability

For one complex generated workflow:

1. Export to markdown and/or python if those APIs support the graph
2. Re-import or rebuild from the exported representation
3. Validate the rebuilt graph

**Checks:**
- Does the exported form preserve enough structure to recreate a valid workflow?
- Are key topology features preserved?

### D4: Mutation-after-build stability

Take one valid generated workflow and apply 1-2 natural-language follow-ups that modify it.

At least one follow-up should be compound, e.g. `"Add a review loop after the summarize step and fan out the research step to search 3 sources in parallel"` so the battery exercises 32-6's compound structural mutation dispatch.

**Checks:**
- Does the workflow remain valid after mutation?
- Can it still execute after mutation?
- Does graph growth look coherent rather than fragmenting into disconnected pieces?

## Validation Criteria

### T4 prompts
1. **Graph created?** — Did a graph appear?
2. **Validates?** — Does `validate_graph()` pass?
3. **Fixture expectations met?** (33-10 B — NOW ENFORCED) — Node count within `min_nodes..max_nodes`, required `node_types` present, `topology` features match. Violations → `expectation_mismatch`.
4. **Tool_ids correct?** (33-10 C) — Tool nodes have appropriate `tool_id` from keyword inference, not default `web_search`.
5. **Code nodes descriptive?** (33-10 C) — Code placeholders are descriptive, not `result = 'done'` stubs.
6. **Review-loop condition?** (33-10 A) — If review loops present, condition polarity should be continue-while (not stop-when-satisfied).
7. **Execution attempt** — Did the run start successfully? How many nodes completed?

### Multi-turn sequences
1. **Incremental modification?** — Did each turn modify the existing graph rather than rebuilding?
2. **Additive?** — Does the graph grow with each turn (more nodes, more edges)?
3. **Coherent?** — Is the final graph a valid, connected workflow (not disconnected fragments)?
4. **Final validation?** — Does the final graph pass `validate_graph()`?
5. **Revision continuity?** — Do graph revisions change incrementally across turns rather than resetting in suspicious ways?
6. **Compound mutation support?** — When one turn asks for two structural changes, are both applied atomically without falling back to full rebuild?

### Durability checks
1. **Repeat-run stability** — Does the same workflow remain runnable across repeated executions?
2. **Reload stability** — Does the stored graph remain valid after reload?
3. **Export/import stability** — Does a round-trip through export/import preserve validity?
4. **Mutation stability** — Does a valid graph remain valid after follow-up edits?

## Tasks

### Initial run (completed)
- [x] 1. Write T4 prompt fixtures
- [x] 2. Write multi-turn sequence fixtures (with follow_ups field), including compound structural follow-ups
- [x] 3. Add durability smoke fixtures / checks (`tests/eval/durability_checks.py`)
- [x] 4. Run complex battery via harness *(2026-03-12: T4 50% pass, multi-turn 80%)*
- [x] 5. Attempt execution on valid graphs *(all failed at runtime — expected for tool-heavy workflows)*
- [x] 6. Run durability smoke checks *(D1-D4 on first valid graph)*
- [x] 8. Check domain profile activation *(report.t4_domain)*
- [x] 9. Verify progressive refinement used structural mutations *(report.multi_turn_summary)*

### Post-33-9/33-10 re-run
- [ ] 10. Re-run T4 battery (`python -m tests.eval --tier T4 --lane build`)
  - [ ] 10-1. Compare against prior T4 pass rate (50%). With fixture enforcement, expect drop for under-noded graphs.
  - [ ] 10-2. Check tool_id correctness across t4-01 through t4-04 (all tool-heavy)
  - [ ] 10-3. Check review-loop condition polarity on t4-01, t4-02, t4-03 (33-10 A status)
  - [ ] 10-4. Record generation summary data: path, retries, wall-clock
- [ ] 11. Re-run multi-turn sequences (`python -m tests.eval --complex --lane build`)
  - [ ] 11-1. Compare against prior 80% turn pass rate
  - [ ] 11-2. Check mutation-vs-rebuild rate per turn
  - [ ] 11-3. Check compound follow-up handling (32-6)
- [ ] 12. Re-run durability checks (`python -m tests.eval --durability`)
- [ ] 13. Review results, annotate failure modes
  - [ ] 13-1. Distinguish `expectation_mismatch` (fixture too strict) from genuine quality failures
  - [ ] 13-2. Note any new `timeout_planning` vs `codegen_failed` vs `stream_error` patterns
- [ ] 14. Run T4 in agent lane if agent routing has improved (`--lane both`)
  - [ ] 14-1. Compare agent vs build lane pass rates for T4
- [ ] 15. Create a dedicated long-running prompt pack at `tests/eval/benchmark_long_running_prompts.json`
  - [ ] 15-1. Add LR1, LR2, LR3 fixtures with frozen local input paths rather than open-ended live-world requests
  - [ ] 15-2. Mark execution assertions, artifact assertions, and time budgets explicitly in the fixture metadata
  - [ ] 15-3. Tag these fixtures separately from smoke/T1-T5 so they can be run as a benchmark-prep slice
- [ ] 16. Run the long-running pack in `build` lane with execution enabled
  - [ ] 16-1. Record build success, validation, execution success, wall-clock, token/cost, and final artifact checks
  - [ ] 16-2. Capture where the run stopped when it fails: planning, generation, validation, execution, or resume
  - [ ] 16-3. Keep generated graphs and artifacts for manual inspection and benchmark handoff
- [ ] 17. Add interruption/resume checks to LR3 (and LR1 where practical)
  - [ ] 17-1. Inject one interruption after the first meaningful execution phase
  - [ ] 17-2. Resume and verify the run continues rather than restarting from zero
- [ ] 18. Treat this long-running slice as the gate into Bench 4 / Bench 1
  - [ ] 18-1. Do not promote any long-running benchmark results publicly until 33-10 Section G is closed
  - [ ] 18-2. Once LR1/LR2/LR3 each have at least one honest end-to-end run, use them as the reference workload set for monolithic baseline comparisons

## Files

| File | Action |
|------|--------|
| `tests/eval/prompts.json` | Extend — T4 prompts and multi-turn sequences |
| `tests/eval/benchmark_long_running_prompts.json` | Create — frozen-input long-running benchmark-prep fixtures |
| `tests/eval/durability_checks.py` | Create — repeat-run / reload / export-import helpers |

## Decisions

- (filled in during execution)

## Notes

- T4 workflows may take 30-60 seconds to generate (multiple LLM calls, retries). The harness should have generous timeouts.
- Multi-turn testing requires maintaining conversation history across turns. The harness needs to pass both `history` and the latest `client_graph_revision` back to each subsequent API call.
- Execution testing is informational, not pass/fail. A workflow that validates but fails to execute is still a generation success — it is a separate category of issue.
- The new long-running benchmark-prep slice is different: for LR1/LR2/LR3, execution truth matters. Build-only success is useful for diagnosis, but it is not benchmark evidence.
- Durability matters most on a smaller execution-friendly subset. Do not block the entire phase on making every complex workflow fully runnable.
- **Smart defaults (32-3):** T4 graphs should have auto-wired retry policies and validation gates.
- **Domain profiles (32-5):** t4-02 (paper), t4-03 (equity), t4-04 (data/ML) should each activate their respective domain profile.
- **Compound follow-ups (32-6):** At least one multi-turn or mutation-after-build case should combine two edits in one message.
- **Fixture enforcement (33-10 B):** T4 fixtures have strict `min_nodes` (10-15). Prior run showed some T4 graphs pass with too few nodes (e.g. the equity workflow pilot produced 1 node). Fixture enforcement will catch these. If legitimate complex workflows consistently produce fewer nodes than expected, revisit whether `min_nodes` is too aggressive.
- **Tool_id inference (33-10 C):** All four T4 prompts mention tools (web_search, code_execution, csv_read, pdf_read). After the `_TOOL_KEYWORD_MAP` patch, tool nodes should have correct `tool_id` assignments. Check the stored graph JSON for tool node configs.
- **Node count calibration (33-9 C):** Codegen prompt now includes node-count guidance calibrated from 42 eval graphs. T4 prompts should produce graphs in the 10-20 node range, not the 1-5 node range seen in some prior runs.
- **Main bottleneck from prior runs:** 72% of failures were `timeout_planning` — LLM API reliability, not pipeline logic. If T4 still shows high timeout rates, the issue is infrastructure, not generation quality.
- **Benchmark-prep handoff:** This file should produce the first serious long-running workload pack before Bench 4 custom scenarios are positioned as proof. Think of LR1/LR2/LR3 as the execution-focused dress rehearsal for the later benchmark suite.
