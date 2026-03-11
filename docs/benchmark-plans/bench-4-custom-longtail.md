# Bench 4: Custom Long-Tail Suite

**Parent:** [benchmark-plan](benchmark-plan.md)
**Status:** not-started
**Goal:** Demonstrate DAN's advantage on realistic multi-step domain workflows that no published benchmark covers — specifically testing while-loops, context projection, hyperedges, composable sub-graphs, and learning.

## Why This Benchmark

Published benchmarks test what existing systems were designed for. The custom suite tests what only DAN can do:
- Review-revise loops (while-loop with quality gates)
- Fan-out/fan-in parallel processing
- Hyperedge behavior modification across node subsets
- Composable sub-graphs as reusable blocks
- Learning across repeated runs

These are the "impossible workflow" scenarios that form the basis of the marketing challenge: "Build this workflow in your tool. We'll wait."

## The Five Scenarios

### Scenario 1: Research Paper Literature Review

**Steps:** ~15 (search → read PDFs → extract claims → synthesize → write review → review-revise loop)
**Tools:** web_search, pdf_read, file_write, LLM (multiple models)
**Quality gate:** Human-rated review quality (1-5) OR LLM-as-judge
**DAN features tested:** ForEach (parallel paper processing), while-loop (review-revise), context projection (reviewer sees only current draft + comments), model heterogeneity (cheap extraction, expensive synthesis)

```
[Search Papers] → [ForEach: Read & Extract] → [Synthesize Claims]
        → [Write Review] → [Review-Revise Loop: Reviewer ↔ Reviser] → [Final Review]
```

### Scenario 2: Data Analysis Report

**Steps:** ~12 (load CSV → clean → statistical tests → charts → write methodology → validate statistics)
**Tools:** python_eval, file_read, file_write, LLM
**Quality gate:** Automated — statistical claims in text match computed results
**DAN features tested:** Code nodes (python_eval), while-loop (re-run analysis if validation fails), typed edges (numeric data on data edges, methodology text on context edges)

```
[Load Data] → [Clean] → [Statistical Tests] → [Generate Charts]
        → [Write Methodology] → [Validate: Claims ↔ Computations] → [Final Report]
```

### Scenario 3: Competitive Intelligence Brief

**Steps:** ~18 (crawl 5 sites → extract pricing/features → compare → gap analysis → strategy memo)
**Tools:** web_fetch, web_search, python_eval, file_write, LLM
**Quality gate:** Human-rated actionable insights (1-5) + factual accuracy check
**DAN features tested:** ForEach (parallel competitor analysis), context projection (gap analysis sees only structured extractions, not raw HTML), hyperedge (apply "executive brief style" skill across all writing nodes)

```
[Identify Competitors] → [ForEach: Crawl & Extract] → [Normalize to Schema]
        → [Comparative Analysis] → [Gap Identification] → [Strategy Memo]
        ← Hyperedge: "Executive Brief Style" applied to analysis + memo nodes →
```

### Scenario 4: Code Migration (Python 2 → 3)

**Steps:** ~20 (scan codebase → classify files → transform syntax → run tests → review failures → retry)
**Tools:** file_read, python_eval, shell (pytest), LLM
**Quality gate:** Automated — test pass rate
**DAN features tested:** ForEach (parallel file processing), while-loop (fix-and-retest cycle), checkpoint recovery (resume from partially migrated state), model heterogeneity (cheap for syntax transforms, expensive for complex refactors)

```
[Scan Codebase] → [Classify by Complexity] → [ForEach: Transform]
        → [Run Tests] → [While: Fix Failures & Retest] → [Final Report]
```

### Scenario 5: Multi-Source Fact-Checking

**Steps:** ~15 (extract claims → search evidence per claim → cross-reference → assess reliability → verdict)
**Tools:** web_search, web_fetch, pdf_read, LLM
**Quality gate:** Automated — agreement with ground-truth verdicts (need to prepare ground-truth)
**DAN features tested:** ForEach (parallel claim verification), voting/ensemble (multiple LLM judges per claim), context projection (each claim verification is independent), while-loop (insufficient evidence → search more)

```
[Extract Claims] → [ForEach: Search Evidence] → [Cross-Reference Sources]
        → [Vote: Assess Reliability per Claim] → [Generate Verdict Report]
```

## Evaluation Framework

### Quality Metrics per Scenario

| Scenario | Primary Metric | Secondary Metric | Automated? |
|----------|---------------|-----------------|------------|
| Lit Review | Human quality (1-5) | Citation accuracy | Semi-auto (LLM-as-judge + spot check) |
| Data Analysis | Claims-match-computations rate | Report completeness | Fully automated |
| Competitive Intel | Actionable insights (1-5) | Factual accuracy | Semi-auto (LLM-as-judge + spot check) |
| Code Migration | Test pass rate | Lines correctly transformed | Fully automated |
| Fact-Checking | Verdict accuracy vs. ground-truth | Source coverage | Fully automated |

### Cross-Scenario Metrics

Every scenario also reports: total tokens, total cost, wall-clock time, failure point, recovery success.

## Tasks

- [ ] 1. **Scenario design**
  - [ ] 1-1. Define concrete inputs for each scenario (specific topic, dataset, codebase, article)
  - [ ] 1-2. Prepare ground-truth or evaluation rubrics per scenario
  - [ ] 1-3. Identify minimum viable tool set per scenario
- [ ] 2. **DAN workflow construction per scenario**
  - [ ] 2-1. Scenario 1: Lit review workflow (builder DSL or markdown agents)
  - [ ] 2-2. Scenario 2: Data analysis workflow
  - [ ] 2-3. Scenario 3: Competitive intel workflow
  - [ ] 2-4. Scenario 4: Code migration workflow
  - [ ] 2-5. Scenario 5: Fact-checking workflow
- [ ] 3. **Meta-orchestrator path per scenario**
  - [ ] 3-1. Feed each scenario description to MetaController as NL
  - [ ] 3-2. Compare auto-generated graph vs. hand-built graph
  - [ ] 3-3. Report: can the meta-orchestrator produce a working graph without manual intervention?
- [ ] 4. **Baseline runs**
  - [ ] 4-1. Monolithic agent (same LLM + tools, single conversation) per scenario
  - [ ] 4-2. Simple chain (sequential script, no branching/loops) per scenario
  - [ ] 4-3. Record failure point, quality, tokens, cost for each
- [ ] 5. **DAN runs**
  - [ ] 5-1. Hand-built DAN workflow per scenario
  - [ ] 5-2. Auto-generated DAN workflow per scenario (meta-orchestrator path)
  - [ ] 5-3. Record quality, tokens, cost, wall-clock time for each
- [ ] 6. **Feature isolation**
  - [ ] 6-1. While-loop contribution: single-pass vs. review-revise on Scenarios 1, 4
  - [ ] 6-2. Context projection contribution: pass-everything vs. projection on Scenarios 1, 3
  - [ ] 6-3. Fan-out contribution: sequential vs. parallel on Scenarios 1, 3, 5
  - [ ] 6-4. Model heterogeneity: all-same-model vs. tier policy on all scenarios
  - [ ] 6-5. Hyperedge contribution: manual prompt editing vs. skill attachment on Scenario 3
  - [ ] 6-6. Checkpoint recovery: inject failure at step N, compare resume vs. restart on Scenario 4
- [ ] 7. **Learning curve** (Scenarios 1 and 2, 5 runs each)
  - [ ] 7-1. Run each scenario 5 times
  - [ ] 7-2. Plot cost and quality per run
  - [ ] 7-3. Identify which learning features activated (model tiering, prompt optimization)
- [ ] 8. **Feed all results into bench-5 analysis framework**

## The "Impossible Workflow Challenge"

Package the 5 scenarios as a public benchmark challenge:
- Publish task descriptions and evaluation criteria
- Provide a harness that accepts workflow outputs and scores them
- Challenge: "Build these 5 workflows in your tool and submit results"
- DAN's results serve as the reference implementation

Key impossible features for competitors:
- **Scenario 1:** While-loop review-revise with typed exit conditions — Langflow/Flowise can't
- **Scenario 3:** Hyperedge style skill across multiple nodes — nobody has this concept
- **Scenario 4:** Checkpoint recovery from mid-migration — competitors restart from scratch
- **Scenario 5:** Voting ensemble across multiple LLM judges — requires VoteNode

## Expected Results

| Scenario | Monolithic (predicted) | Simple Chain (predicted) | DAN (predicted) | Primary Advantage |
|----------|----------------------|------------------------|-----------------|--------------------|
| Lit Review | Low quality, context confusion by step 10 | Completes but no quality gate | High quality after 2-3 review iterations | While-loop + projection |
| Data Analysis | Moderate — may miss validation | Completes but no validation | Validated, correct claims | While-loop + code node |
| Competitive Intel | Partial — misses some competitors | Completes but generic | Structured, actionable | Fan-out + hyperedge |
| Code Migration | Fails at ~60% of files | Transforms but no test loop | ~90%+ test pass rate | While-loop + checkpoint |
| Fact-Checking | Moderate accuracy | Sequential, slow | Higher accuracy, faster | Fan-out + voting |

## Estimated Effort

- Scenario design + inputs: 2 days
- Workflow construction (5 workflows): 4 days
- Baseline runs: 2 days (API costs ~$100)
- DAN runs + meta-orchestrator comparison: 2 days (API costs ~$100)
- Feature isolation + learning curves: 3 days (API costs ~$150)
- Analysis: 1 day
- Challenge packaging: 1 day
- **Total: ~15 days, ~$350 API costs**

## Decisions

- (to be filled during execution)

## Notes

- Start with Scenario 1 (lit review) as the simplest proof — DAN already has the paper_writing workflow built
- Scenario 4 needs a prepared Python 2 codebase with tests — consider using an open-source project
- Scenario 5 needs ground-truth verdicts — prepare from a known fact-checking dataset (e.g., FEVER)
- LLM-as-judge evaluation should use a different model than the one being tested to avoid bias
- The "Impossible Workflow Challenge" is a marketing deliverable — plan the packaging early
