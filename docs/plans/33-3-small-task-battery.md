# 33-3: Small Task Battery

**Parent:** [33-generation-quality-eval](33-generation-quality-eval.md)
**Status:** in-progress
**Goal:** Define and run 20+ small-to-medium workflow generation prompts that exercise each major pattern family, reuse/adaptation behavior, and composition quality, establishing the baseline success rate for everyday use cases.

**Pilot subset:** The 10-prompt manual pilot (33-1) ran successfully in build lane with 60% pass rate. See [33-generation-quality-eval](33-generation-quality-eval.md#pilot-findings-2026-03-11) for results.

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

For each prompt, the harness checks:

1. **Graph created?** — Did a new graph appear in the store?
2. **Validates?** — Does `validate_graph()` pass on the generated graph?
3. **Topology match?** — Does the graph contain the expected semantic must-haves (loops, fan-out, tool nodes, code nodes) per the fixture?
4. **Node count reasonable?** — Is the node count within a broad expected range?
5. **Node types correct?** — Are the expected node types present (llm, tool, code, for_each, gate, etc.)?
6. **Connected / coherent?** — Are there no obvious disconnected fragments or isolated nodes in the generated workflow?

For prompts marked `lane: both`, the harness compares:

7. **Routing delta** — Did `agent` lane fail while `build` lane succeed? That indicates a routing problem rather than a generation problem.

Edge cases (T5) have different criteria:
- t5-01: No graph created, response asks for clarification
- t5-02, t5-03: No graph created, response answers the question
- t5-04: Either a reasonable complex graph OR a clarification/simplification response

Reuse / adaptation prompts (T2R) have different criteria:
- Prefer observable reuse/adaptation behavior over exact topology matching
- Record whether the system appears to modify an existing workflow, references a similar prior workflow, or still builds from scratch

## Tasks

- [x] 1. Write all prompt fixtures into `tests/eval/prompts.json`
- [x] 2. Run the battery via the harness *(2026-03-12: full run on port 8000, 51 records, 54.9% pass)*
- [ ] 3. Review results and annotate false positives/negatives
- [x] 4. Compute per-tier and per-lane pass rates *(report.by_tier, report.by_lane)*
- [x] 5. Record generation path per prompt *(report.generation_path with by_tier breakdown)*
- [x] 6. Check whether smart defaults (32-3) were applied: do generated T2/T3 graphs include retry policies and validation gates? *(report.smart_defaults when graphs stored)*
- [x] 7. Note which domain profiles (32-5) activated, if any *(report.domain_profiles)*
- [x] 8. Summarize reuse / adaptation behavior from the T2R prompts *(report.reuse_adaptation: T2R entries with mutation/reuse signals)*
- [x] 9. Specifically review 32-6 coverage: did `t1-03` use flexible review criteria without falling back to hardcoded `quality_score`, and did `t1-09` produce a simple conditional branch rather than verbose low-level gate wiring? *(report.coverage_32_6 highlights t1-03, t1-09)*

## Files

| File | Action |
|------|--------|
| `tests/eval/prompts.json` | Create/extend — T1 through T5 plus T2R prompt fixtures |

## Decisions

- (filled in during execution)

## Notes

- T1 prompts should be near-100% pass rate in `build` lane. If they fail there, the generation path has a core problem. These should also use the intent compiler path (not codegen) after the Phase 22 expansion to 15+ patterns.
- T2-T3 prompt expected node counts are approximate — the LLM may produce slightly different but valid topologies.
- Edge cases (T5) are judged by routing correctness, not graph quality.
- Reuse / adaptation prompts are judged by behavior and telemetry signals (e.g. memory_retrieval events from 31-20), not by forcing a specific graph shape.
- `t1-03` is the narrow 32-6 check for configurable `review_loop()` criteria; the generated flow should not require a hardcoded `quality_score` contract.
- `t1-09` is the narrow 32-6 check for common if/else branching convenience; a simple gate-style branch is sufficient, but it should not require obviously over-complicated topology for the common case.
