# 33-4: Complex Workflow Battery

**Parent:** [33-generation-quality-eval](33-generation-quality-eval.md)
**Status:** in-progress
**Goal:** Test workflow generation on complex, multi-pattern, real-world-scale prompts, multi-turn progressive refinement sequences, and durability smoke checks. These stress the limits of generation and reveal composition, wiring, and workflow-stability failure modes.

## T4: Complex Workflows (4 prompts)

These require nested sub-graphs, multi-department coordination, or deep pipelines (10+ nodes). They push the boundaries of what the builder codegen can produce in one shot. Domain generation profiles (32-5) should activate for equity/research/data analysis prompts, injecting domain-appropriate tools and model tiers.

| ID | Prompt | Key Features | Expected Nodes |
|----|--------|-------------|---------------|
| t4-01 | "Build a multi-department research system: create 3 parallel research teams — one for market analysis, one for technical assessment, one for competitive intelligence. Each team should have its own research-review loop. An orchestrator collects all findings and produces a unified strategic report." | parallel_subagents, nested review_loops, orchestrator | 12-20 |
| t4-02 | "Create an end-to-end academic paper writing workflow: start with a literature search using web search, organize papers by theme, generate an outline, write each section in parallel (intro, methods, results, discussion), assemble into a draft, run through a review panel with up to 3 revision cycles, compile to LaTeX PDF" | fan_out, review_loop, tools, code, chain | 10-18 |
| t4-03 | "Build an equity research workflow: gather company financials from web searches (revenue, margins, growth), pull recent news and analyst coverage, run Python code to compute financial ratios and trend analysis, generate comparison charts, assess market sentiment, write an investment thesis with bull/bear cases, route through a senior analyst review loop, produce a final PDF memo" | chain, tools, code, review_loop, fan_out | 10-15 |
| t4-04 | "Create a Kaggle competition pipeline: read the dataset from a CSV file, perform exploratory data analysis with Python code, engineer features, train and evaluate 3 models in parallel (random forest, XGBoost, neural net), ensemble the best performers, generate a submission file, write a summary report of methodology and results" | fan_out, code, chain, tools | 10-15 |

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
3. **Scale correct?** — Is node count in a plausible complex-workflow range?
4. **Key patterns present?** — Does the graph contain the expected structural features (fan-out, review loops, sub-graphs, etc.)?
5. **Execution attempt** — Did the run start successfully? How many nodes completed?

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

- [x] 1. Write T4 prompt fixtures
- [x] 2. Write multi-turn sequence fixtures (with follow_ups field), including at least one compound structural follow-up in a single turn
- [x] 3. Add durability smoke fixtures / checks (`tests/eval/durability_checks.py`)
- [ ] 4. Run complex battery via harness
- [ ] 5. Attempt execution on execution-friendly valid graphs
- [x] 6. Run durability smoke checks *(CLI --durability flag runs D1-D4 on first valid graph)*
- [ ] 7. Review results, annotate failure modes
- [x] 8. Check whether domain profiles (32-5) activated for domain-specific T4 prompts *(report.t4_domain)*
- [x] 9. For multi-turn sequences, verify that progressive refinement (32-4) used structural mutations rather than full rebuilds *(report.multi_turn_summary: mutation_rate, structural_mutation per turn)*

## Files

| File | Action |
|------|--------|
| `tests/eval/prompts.json` | Extend — T4 prompts and multi-turn sequences |
| `tests/eval/durability_checks.py` | Create — repeat-run / reload / export-import helpers |

## Decisions

- (filled in during execution)

## Notes

- T4 workflows may take 30-60 seconds to generate (multiple LLM calls, retries). The harness should have generous timeouts.
- Multi-turn testing requires maintaining conversation history across turns. The harness needs to pass both `history` and the latest `client_graph_revision` back to each subsequent API call.
- Execution testing is informational, not pass/fail. A workflow that validates but fails to execute is still a generation success — it is a separate category of issue.
- Durability matters most on a smaller execution-friendly subset. Do not block the entire phase on making every complex workflow fully runnable.
- **Smart defaults (32-3):** T4 graphs should have auto-wired retry policies and validation gates. If they don't, that's a signal that smart defaults aren't activating at scale.
- **Domain profiles (32-5):** t4-02 (paper), t4-03 (equity), t4-04 (data/ML) should each activate their respective domain profile. Check whether domain-specific tools and model tiers appear in the generated graph.
- **Compound follow-ups (32-6):** At least one multi-turn or mutation-after-build case should combine two edits in one message. If that falls back to rebuild/codegen instead of atomic macro application, file it against 32-6 rather than 32-4.
