# 33-3: Small Task Battery

**Parent:** [33-generation-quality-eval](33-generation-quality-eval.md)
**Status:** in-progress
**Goal:** Define and run 20+ small-to-medium workflow generation prompts that exercise each major pattern family, reuse/adaptation behavior, and composition quality, establishing the baseline success rate for everyday use cases.

**Prior run (2026-03-12):** 51 records, 54.9% pass (build lane). T1 55%, T2 75%, T2R 0%, T3 59%, T4 50%, T5 50%. Intent compiler 29.4%. See [33-generation-quality-eval](33-generation-quality-eval.md#post-patch-full-battery-2026-03-12).

**Re-run context:** Pipeline has received significant patches since the prior run — 33-9 (build path trustworthiness) and 33-10 (semantic correctness). Key changes affecting this battery:

| Patch | What changed | Expected impact on re-run |
|---|---|---|
| 33-10 B | Fixture expectation enforcement: `_determine_status()` now checks `min_nodes`, `topology`, `node_types` against fixtures | Pass rate will drop for graphs that previously "passed" structurally but didn't match expectations (honest baseline ~30-35%) |
| 33-10 C | Tool keyword inference: `_TOOL_KEYWORD_MAP` (30+ mappings) auto-assigns `tool_id` based on prompt keywords | Tool nodes (t2-01, t2-03, t2-04, t3-*) should have correct `tool_id` instead of defaulting to `web_search` |
| 33-10 F | Intent extraction tool-awareness: extraction prompt lists 19 registered tool_ids | Better tool-node typing in intent extraction stage |
| 33-10 A | Review-loop condition polarity fix (PENDING) | t1-03 review loop may still have backwards continue-while semantics until this lands |
| 33-9 E | Agent-lane routing: CONFIRM bypass, reuse suppression, solver heuristic | Agent lane should work at ~20% (was 0%) |
| 33-9 A | Generation summary events: `ChatGenerationSummaryEvent` with path, retries, wall-clock | Better diagnostics in JSONL records |
| 33-9 C | Node count calibration from 42 eval graphs | Codegen should produce better-sized graphs |
| 33-6 | Intent compiler activation: T1/T2 use deterministic path (68% activation) | T1/T2 should pass reliably when intent compiler activates (100% pass rate historically) |

## Prompt Catalog

### T1: Trivial — Single Pattern Expansions (9 prompts)

These map directly to known patterns and should exercise the builder convenience layer (`chain()`, `review_loop()`, `map_reduce()`) and expanded intent compiler (32-1, 32-2). If these fail, the generation path has fundamental issues.

| ID | Prompt | Expected Pattern | Expected Nodes |
|----|--------|-----------------|---------------|
| t1-01 | "Build a 3-step chain: research, analyze, summarize" | chain | 3 LLM |
| t1-02 | "Create a 5-step pipeline: gather data, clean it, analyze, visualize, report" | chain | 5 LLM |
| t1-03 | "Build a review loop: writer drafts, reviewer verifies citations, loop until `citations_verified` is true" | review_loop (custom criteria) | 3 (writer + reviewer + gate) |
| t1-04 | "Create a fan-out workflow that processes 5 items in parallel then merges results" | fan_out | 3+ (source + for_each + body + merge) |
| t1-05 | "Build a RAG question-answering pipeline over a document collection" | rag_qa | 2+ (RAG + LLM answer) |
| t1-06 | "Create a data ingestion pipeline: read PDFs from a folder, index them for search" | data_ingest | 3+ (input + index + RAG) |
| t1-07 | "Build a workflow with a single LLM node that summarizes text" | chain (1 node) | 1 LLM |
| t1-08 | "Create a workflow that reads a file and writes a summary to another file" | chain + tools | 2-3 (read + LLM + write) |
| t1-09 | "Build a workflow that checks whether a document is relevant; if yes summarize it, otherwise write a brief rejection note" | conditional branch | 3-4 |

### T2: Simple — Standard Multi-Node Workflows (6 prompts)

These require composing 2+ patterns or integrating tools. Smart generation defaults (32-3) should auto-add retry policies and validation gates. Should work on first attempt most of the time.

| ID | Prompt | Key Features | Expected Nodes |
|----|--------|-------------|---------------|
| t2-01 | "Build a workflow that searches the web for recent news on a topic, reads the top 3 articles, and produces a briefing" | tools (web_search, web_fetch), chain | 4-6 |
| t2-02 | "Create a translation pipeline: take a document, split it into sections, translate each section in parallel, reassemble" | fan_out + chain | 4-5 |
| t2-03 | "Build a workflow that takes a code file, reviews it for bugs, suggests fixes, then applies the fixes" | chain + tools | 3-4 |
| t2-04 | "Create a meeting notes workflow: transcribe audio, extract action items, draft follow-up emails" | chain + tools | 3-4 |
| t2-05 | "Build a content pipeline: generate blog post outline, write each section, review and edit, produce final markdown" | chain + review_loop | 4-5 |
| t2-06 | "Create a comparison workflow: take two documents, summarize each, then produce a comparison analysis" | fan_out (2 parallel) + merge | 4-5 |

### T2R: Reuse / Adaptation Smoke Checks (3 prompts)

These are small prompts that should tell us whether DAN can reuse or adapt previously-built workflows rather than always building from scratch. They should run after at least a few related workflows already exist in the graph store.

| ID | Prompt | Key Features | Expected Outcome |
|----|--------|-------------|------------------|
| t2r-01 | "Build another workflow like the 3-step research/analyze/summarize one, but tailored to supply-chain disruption analysis" | reuse / adapt | Prefer reuse or adaptation of an existing chain |
| t2r-02 | "Take the earlier market briefing workflow and adapt it for a private-company due diligence memo" | adapt existing workflow | Existing workflow modified rather than unrelated rebuild |
| t2r-03 | "Create a workflow similar to the literature-review one, but focused on policy reports instead of papers" | reuse / adapt | Reuse or adaptation signal observable in telemetry / behavior |

### T3: Medium — Multi-Pattern Composition (5 prompts)

These combine multiple patterns, tools, and control flow. Domain generation profiles (32-5) should activate for prompts with domain cues (equity/research/data analysis). May need 1 retry.

| ID | Prompt | Key Features | Expected Nodes |
|----|--------|-------------|---------------|
| t3-01 | "Build a literature review workflow: search for papers on a topic, read each paper in parallel, organize findings by theme, write a structured review, run it through a reviewer loop" | fan_out + chain + review_loop + tools | 6-10 |
| t3-02 | "Create a data analysis pipeline: read a CSV, run Python code to compute statistics and generate charts, interpret the results, write a report, get human approval before finalizing" | chain + code + tools + human_review | 5-8 |
| t3-03 | "Build a competitive analysis workflow: take a company name, search for competitors, analyze each competitor in parallel (financials, products, market share), then produce a comparison matrix and strategic summary" | fan_out + chain + tools | 6-10 |
| t3-04 | "Create a hiring pipeline workflow: screen resumes in parallel, score each candidate, rank them, write interview prep notes for the top 5, send a summary to the hiring manager" | fan_out + chain + tools | 5-8 |
| t3-05 | "Build a weekly report generator: pull data from multiple sources, analyze trends, write executive summary, route to reviewer, email the final version" | fan_out + chain + review_loop + tools | 6-10 |

### T5: Edge Cases (4 prompts)

These test boundary conditions: ambiguous intent, non-workflow requests, over-specified prompts. The request guard pipeline (31-19) should help with correct routing here — classification coherence and understanding guards should prevent false-positive workflow builds.

| ID | Prompt | Expected Behavior |
|----|--------|------------------|
| t5-01 | "Make something cool" | Should ask for clarification, NOT build a random workflow |
| t5-02 | "What's the weather in New York?" | Should answer directly (tool call), NOT build a workflow |
| t5-03 | "Tell me about deep learning" | Should answer conversationally, NOT build a workflow |
| t5-04 | "Build a workflow that ingests data from PostgreSQL database at postgres://user:pass@host:5432/db, runs a complex multi-dimensional factor analysis using PCA and t-SNE with hyperparameter tuning across 15 different configurations, generates interactive Plotly dashboards, runs A/B tests on the visualization choices, emails results to 3 stakeholders with personalized summaries based on their role (executive, technical, operations), and schedules weekly re-runs with drift detection" | Should handle gracefully — either simplify, ask for priorities, or attempt a best-effort complex graph |

## Validation Criteria

The harness now enforces a **two-gate validation** (structural + expectation-fit) per 33-10 B:

### Gate 1: Structural validity
1. **Graph created?** — Did a new graph appear in the store?
2. **Validates?** — Does `validate_graph()` pass on the generated graph?

### Gate 2: Fixture expectation fit (33-10 B — NOW ENFORCED)
3. **Node count in range?** — Is `node_count` between fixture `expected.min_nodes` and `expected.max_nodes`? Graphs outside this range fail with `expectation_mismatch`.
4. **Node types present?** — Does `graph_summary.node_types` contain every type listed in `expected.node_types`? Missing types → `expectation_mismatch`.
5. **Topology features?** — Does the graph match `expected.topology` constraints:
   - `review_loop` → `has_loop` must be true
   - `fan_out` → `has_fan_out` must be true
   - `chain` → `node_count >= 2`
   - `tools` → at least one tool-type node present
   - Mismatches → `expectation_mismatch`

### Semantic checks (when applicable)
6. **Tool_id correctness?** — After 33-10 C, tool nodes should have correct `tool_id` (not default `web_search`). Check tool-heavy prompts (t2-01, t2-03, t2-04) for appropriate tool assignment.
7. **Review-loop condition?** — t1-03 should use flexible custom criteria (`citations_verified`), not hardcoded `quality_score`. After 33-10 A lands, condition polarity should be correct (continue-while, not stop-when).
8. **Code node quality?** — After 33-10 C, code nodes should have descriptive placeholder code, not `result = 'done'` stubs.

### Lane comparison
9. **Routing delta** — Did `agent` lane fail while `build` lane succeed? That indicates a routing problem rather than a generation problem.

### Edge cases (T5)
- t5-01: No graph created, response asks for clarification
- t5-02, t5-03: No graph created, response answers the question
- t5-04: Either a reasonable complex graph OR a clarification/simplification response

### Reuse / adaptation (T2R)
- Prefer observable reuse/adaptation behavior over exact topology matching
- Record whether the system appears to modify an existing workflow, references a similar prior workflow, or still builds from scratch
- T2R remains 0% in prior runs — experience-reuse path has upstream blockers

## Tasks

### Initial run (completed)
- [x] 1. Write all prompt fixtures into `tests/eval/prompts.json`
- [x] 2. Run the battery via the harness *(2026-03-12: full run on port 8000, 51 records, 54.9% pass)*
- [x] 4. Compute per-tier and per-lane pass rates *(report.by_tier, report.by_lane)*
- [x] 5. Record generation path per prompt *(report.generation_path with by_tier breakdown)*
- [x] 6. Check whether smart defaults (32-3) were applied *(report.smart_defaults)*
- [x] 7. Note which domain profiles (32-5) activated *(report.domain_profiles)*
- [x] 8. Summarize reuse / adaptation behavior from T2R *(report.reuse_adaptation)*
- [x] 9. Review 32-6 coverage for t1-03, t1-09 *(report.coverage_32_6)*

### Post-33-9/33-10 re-run
- [ ] 10. Re-run full battery (`python -m tests.eval --lane build`)
  - [ ] 10-1. Compare pass rates against prior run (54.9% overall, expect drop from fixture enforcement)
  - [ ] 10-2. Identify new `expectation_mismatch` failures — these are graphs that previously "passed" but don't match fixture expectations
  - [ ] 10-3. Check tool_id correctness on tool-heavy prompts (t2-01, t2-03, t2-04, t3-*)
  - [ ] 10-4. Check review-loop condition polarity on t1-03 (33-10 A status)
  - [ ] 10-5. Check code node placeholders are descriptive (not `result = 'done'`)
- [ ] 11. Review results and annotate false positives/negatives
  - [ ] 11-1. Are any `expectation_mismatch` failures actually reasonable alternative topologies? Widen fixture ranges if so.
  - [ ] 11-2. Are any passing graphs semantically wrong despite matching expectations? Note for 33-10 E (LLM-as-judge).
- [ ] 12. Record generation summary data (33-9 A): path, retries, wall-clock per prompt
- [ ] 13. Measure intent compiler activation rate per tier (target: T1 >80%, T2 >60%)
- [ ] 14. Run with `--runs 3` on T1 tier to measure flakiness baseline

## Files

| File | Action |
|------|--------|
| `tests/eval/prompts.json` | Create/extend — T1 through T5 plus T2R prompt fixtures |

## Decisions

- (filled in during execution)

## Notes

- ~~**2026-03-18 blocker:** A live re-run attempt on `http://localhost:8000` did not progress past `status="processing"` for a simple T1 build prompt. The eval stream produced no events for >100s, and direct graph polling still showed `0 nodes / 0 edges` after 5s, 15s, and 30s.~~ **Resolved 2026-03-18.** The live stall came from two real build-path bugs (tier-2 `workflow_build` decomposition into child chats, plus the non-concierge `surface_context` crash) and one eval-harness issue (graph-summary blind spots for nested control-flow graphs / top-level gate mode / the explicit single-node chain fixture). After patching those paths and restarting DAN, `python -m tests.eval --tier T1 --lane build --delay 0.5 --no-save` completed cleanly at `11/11 passed` for the T1 build slice.
- **2026-03-18 follow-up:** Task 10 is no longer blocked on the first live build prompt. The next step is rerunning the broader 33-3 battery slices (other small-task tiers/lanes) and then rolling the new data into 33-5 analysis.
- **Fixture enforcement impact:** With 33-10 B active, the honest pass rate is expected to drop from ~55% to ~30-35%. This is not a regression — it reveals graphs that previously passed structural validation but didn't actually match the prompt's intent. The delta between old and new pass rates shows how many false-positive "passes" existed.
- T1 prompts should be near-100% pass rate when the intent compiler activates (historically 100%). If T1 fails in `build` lane, check whether intent compiler activated or fell back to codegen.
- T2-T3 prompt expected node counts are approximate — the LLM may produce slightly different but valid topologies. If `expectation_mismatch` fires on a reasonable alternative topology, widen the fixture `min_nodes`/`max_nodes` range.
- Edge cases (T5) are judged by routing correctness, not graph quality.
- Reuse / adaptation prompts (T2R) are judged by behavior and telemetry signals, not by forcing a specific graph shape. T2R remains 0% pass — experience-reuse path has upstream blockers.
- `t1-03` is the narrow 32-6 check for configurable `review_loop()` criteria; the generated flow should not require a hardcoded `quality_score` contract. Also check condition polarity (33-10 A).
- `t1-09` is the narrow 32-6 check for common if/else branching convenience; a simple gate-style branch is sufficient.
- **Tool_id inference (33-10 C):** Prompts mentioning file operations, CSV, PDF, email, code execution should produce tool nodes with correct `tool_id` (e.g. `file_read`, `csv_read`, `pdf_read`, `send_email`, `code_execution`) instead of defaulting to `web_search`. The `_TOOL_KEYWORD_MAP` has 30+ keyword→tool_id mappings.
- **Intent extraction tool-awareness (33-10 F):** The extraction prompt now lists 19 registered tool_ids. This should improve tool-node typing at the intent stage, before codegen even runs.
- **Generation summary (33-9 A):** Each record should now include `ChatGenerationSummaryEvent` data with path, retries, and wall-clock. Use this to diagnose slow builds and path selection issues.
- **Main bottleneck from prior runs:** 72% of failures were `timeout_planning` (LLM API reliability), not pipeline logic issues. If this persists in the re-run, it's an infrastructure constraint, not a quality issue.
