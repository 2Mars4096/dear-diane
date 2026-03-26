# Benchmark Plan

**Status:** planned
**Goal:** Empirically prove that DAN's typed-graph architecture outperforms monolithic agents on complex, multi-step tasks using public, refreshable benchmarks first, then DAN-specific long-tail suites — and quantify the contribution of each architectural feature.

## Thesis

The distribution of AI tasks follows a power law. Simple tasks (summarize, translate) are commoditized. Moderate tasks (3-5 tool calls) are handled by ReAct loops. The long tail — multi-step, domain-specific, quality-gated workflows — is where monolithic agents break down and where DAN's architecture creates measurable advantage.

The benchmark suite proves this by measuring success rate, quality, token efficiency, cost, and learning across public leaderboards, public reproducible benchmark repos/datasets, and custom long-tail scenarios.

## Public-First Strategy

The benchmark program has two different output modes and they should not be conflated:

1. **Leaderboard-native benchmarks** — public scoreboards where DAN can refresh an externally visible record after major releases. Current priority: GAIA and AppWorld.
2. **Repo-native public benchmarks** — public datasets/eval code with reproducible runs but no single canonical live leaderboard. These still count as public evidence if DAN publishes the run artifacts, commit SHA, model config, and scoring procedure. Current priority: agent-memory benchmarks and WorFBench.

Internal Phase 33 evals remain the gate. They are not the headline evidence, but they determine whether external numbers are believable enough to publish.

## Current Readiness

The benchmark program is no longer blocked on basic execution honesty work:

- Phase 29 (`42-*`) is complete, so benchmark-grade server/local/provider execution is now explicit and reproducible enough to trust.
- Phase 30 (`43-*`) is complete, so the benchmark smoke-path workflow now fails honestly and emits structured degraded artifacts instead of brittle late crashes.

That means the next benchmark work should shift from "patch runtime trust gaps" to "produce public evidence." The immediate execution focus should therefore be:

1. **bench-5 minimal slice** — only the shared reporting/profile features needed for real public runs
2. **bench-2 GAIA** — first leaderboard-native public pilot and validation artifact
3. **bench-6 agent memory** — first repo-native public memory scorecard and contamination-safe memory proof

`bench-3` (AppWorld) remains strategically important, but it should follow after the publication path has already been exercised on GAIA and the memory track.

## Benchmark Inventory

### Tier 1 — Public Score Tracks (first external evidence)

| # | Benchmark | Source | What It Tests | Subplan |
|---|-----------|--------|---------------|---------|
| 1 | **GAIA Level 3** | ICLR 2024 | 6+ step real-world tasks with tools | [bench-2-gaia](bench-2-gaia.md) |
| 2 | **AppWorld** | ACL 2024 Best Resource | Complex multi-API control flow | [bench-3-appworld](bench-3-appworld.md) |
| 3 | **Agent Memory Track** | Public 2024-2026 memory benchmarks | Long-term memory, updates, cross-session carry, memory-guided action | [bench-6-agent-memory](bench-6-agent-memory.md) |

### Tier 1.5 — Public Reproducible Technical Proofs

| # | Benchmark | Source | What It Tests | Subplan |
|---|-----------|--------|---------------|---------|
| 4 | **WorFBench** | ICLR 2025 | Workflow DAG generation from NL | [bench-1-worfbench](bench-1-worfbench.md) |

### Custom Suite — Long-Tail Demonstration

| # | Benchmark | What It Tests | Subplan |
|---|-----------|---------------|---------|
| 5 | **Custom Long-Tail Suite** | 5 domain-specific 10-30 step workflows | [bench-4-custom-longtail](bench-4-custom-longtail.md) |

### Cross-Cutting

| # | Component | What It Covers | Subplan |
|---|-----------|----------------|---------|
| 6 | **Analysis Framework** | Metrics, ablation, learning curves, reporting | [bench-5-analysis-framework](bench-5-analysis-framework.md) |

### Tier 2 — Strong Fit (future, subplans created JIT)

| Benchmark | Focus | Why It Fits DAN |
|-----------|-------|----------------|
| **Tau-bench** (Sierra) | Tool-agent-user with domain rules | Hyperedge rules vs. in-context policy |
| **Jenova.ai** (Feb 2026) | 100k+ token orchestration | Context projection under token pressure |
| **AgentBench** (ICLR 2024) | 8 diverse environments | Generality across workflow patterns |

### Tier 3 — Domain-Specific (future, subplans created JIT)

| Benchmark | Focus | Marketing Angle |
|-----------|-------|----------------|
| **SWE-bench Pro** | Long-horizon coding | "DAN for code workflows" |
| **MLAgentBench** | ML experimentation | "DAN for data science" |
| **OdysseyBench** | Office applications | "DAN for office automation" |
| **AssistantBench** | Web navigation | "DAN for web research" |

## Unified Adapter Pattern

Every benchmark follows the same integration pattern:

```
Benchmark task description
        ↓
DAN MetaController.plan()     → produces a workflow graph
        ↓
DAN Engine.run(graph)          → executes with tools
        ↓
Extract answer from RunResult
        ↓
Submit to benchmark evaluator
```

Adapter engineering per benchmark:
1. **Task parser** — benchmark input format → DAN NL dispatch
2. **Tool mapper** — benchmark tools/APIs → DAN `ToolRegistry`
3. **Answer extractor** — DAN `RunResult` → benchmark expected format
4. **Eval runner** — loop over test set, collect results, compute metrics

## Baselines

Every benchmark should run against the same underlying model family and the same available tools unless the benchmark itself forbids tool parity.

| Baseline | Description | Simulates |
|----------|-------------|-----------|
| **A: Monolithic agent** | Same LLM + same tools, single conversation, self-directed tool use | Cursor agent / Claude agent mode |
| **B: DAN ablation** | Same DAN stack with one capability disabled (`memory=off`, `projection=off`, `parallel=off`, etc.) | Isolates DAN feature contribution |
| **C: Simple chain** | Hardcoded sequential script, no branching/loops/context management | n8n / Zapier-style automation |

`Simple chain` is a useful internal control, but it should not be the main public comparison target on flagship benchmarks. Public claims should primarily compare DAN against a fair monolithic agent baseline and benchmark-native baselines.

## Core Metrics

| Metric | What It Measures | How |
|--------|-----------------|-----|
| **Success rate** | Task completed with acceptable output | Per benchmark evaluator |
| **Quality score** | Output quality (1-5 human or automated) | Human rating or exact match |
| **Total tokens** | Sum of input + output tokens across all LLM calls | Engine token tracking |
| **Total cost** | USD spent on API calls | Engine cost tracking |
| **Wall-clock time** | End-to-end duration | Parallel fan-out advantage |
| **Failure point** | Which logical step failed | Monolithic breakdown analysis |
| **Recovery** | Could it recover from mid-task failure | Checkpoint vs. restart |
| **Learning curve** | Cost/quality on run 1 vs. run 5 | DAN-unique metric |

## Reproducibility And Refresh Policy

Every published benchmark refresh should log:

- git commit SHA
- benchmark version / dataset split
- model name(s) and tier map
- memory mode (`off`, `working-only`, `full`, or benchmark-specific variants)
- learning mode (`on` or `off`)
- behavior / prompt snapshot version where relevant
- exact run date and artifact bundle location

Refresh rules:

1. **Leaderboard-native** benchmarks (GAIA, AppWorld): refresh after a major DAN release or any material routing/memory/execution change that should move the score.
2. **Repo-native public** benchmarks (agent memory, WorFBench): refresh after major memory/planning/runtime changes and publish the result bundle in-repo or on a public artifact host.
3. **Internal gate first**: if the Phase 33 honest rerun or benchmark-prep long-running slice is red, do not refresh public benchmark numbers.

## Feature Ablation

For each benchmark, isolate individual feature contributions by toggling them off:

| Feature | Ablation Method | Expected Impact |
|---------|----------------|-----------------|
| Context projection | `pass_everything=True` mode | 40-60% token reduction on 20+ node pipelines |
| While-loop quality gate | Single pass vs. review-revise (max 5 iter) | 1-2 point quality improvement (5-point scale) |
| Model heterogeneity | All-GPT-4 vs. tier-policy | 3-5x cost reduction, <5% quality loss on cheap nodes |
| Parallel fan-out | Sequential vs. parallel execution | Near-linear wall-clock speedup |
| Checkpoint recovery | Inject failure at step N, resume vs. restart | Proportional time savings |
| Learning (run N vs 1) | Plot cost/quality across 5 runs | 30-50% cost reduction by run 5 |

## Expected Results

```
                  Success Rate by Task Complexity
  100% ┤
       │ ●●●
       │    ●●●  ○○○
       │       ●●   ○○○
       │         ●●     ○○○
       │           ●●      ○○
       │             ●●       ○○
       │               ●●       ○
       │                 ●●       ○
       │                   ●●
   0%  └─────────────────────────────
       5     10    15    20    25   30  (logical steps)

       ● DAN    ○ Monolithic Agent
```

Crossover expected at ~10-15 logical steps where context confusion, lack of quality convergence, and wrong-model-everywhere start degrading monolithic agents.

## Execution Order

1. **Phase 33 gate** — honest rerun + long-running benchmark-prep slice must be stable before external claims
2. **bench-5 minimal slice** — extend `tests/eval` just enough for reproducible benchmark profiles, artifact bundles, compare mode, and memory trace fields
3. **bench-2** (GAIA) — first public leaderboard-native pilot, then first public validation artifact, then first official leaderboard refresh
4. **bench-6** (agent memory) — LongMemEval + LoCoMo + MemoryArena pilot/public scorecard with contamination-safe memory controls
5. **bench-3** (AppWorld) — second leaderboard-native public score track after GAIA and the publication path are already proven
6. **bench-4** (custom long-tail) — DAN-specific demonstrations once public baselines exist
7. **bench-1** (WorFBench) — technical workflow-generation proof, strongest for papers and architecture analysis

## Marketing Deliverables from Benchmark Results

- **Leaderboard placement** on GAIA (public HuggingFace leaderboard)
- **Leaderboard / public scoreboard placement** on AppWorld where feasible
- **Public memory scorecard** — LongMemEval / MemoryArena / LoCoMo results with commit-linked refresh history
- **"Impossible Workflow Challenge"** — the custom suite as a public challenge to other tools
- **Peer-reviewable paper** — WorFBench + GAIA + ablation analysis = workshop/conference submission
- **Cost comparison calculator** — interactive demo showing token/cost savings per pipeline depth
- **Landing page data** — success rate × complexity chart, cost-over-time learning curve

## Dependencies

- Meta-orchestrator (`MetaController`, `WorkflowPlanner`, `IntentCompiler`) must handle benchmark task descriptions
- `ToolRegistry` must support benchmark-required tools (web_search, pdf_read, python_eval, shell, file I/O)
- Engine token/cost tracking must be per-node granular for ablation
- Learning system (`DAN_LEARNING_MODE=1`) must be stable for multi-run experiments
- Memory systems must be switchable and snapshot-able so DAN can run fair `memory_on` vs `memory_off` comparisons without cross-run contamination
