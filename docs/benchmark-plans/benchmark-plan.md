# Benchmark Plan

**Status:** not-started
**Goal:** Empirically prove that DAN's typed-graph architecture outperforms monolithic agents on complex, multi-step "long tail" tasks — and quantify the contribution of each architectural feature.

## Thesis

The distribution of AI tasks follows a power law. Simple tasks (summarize, translate) are commoditized. Moderate tasks (3-5 tool calls) are handled by ReAct loops. The long tail — multi-step, domain-specific, quality-gated workflows — is where monolithic agents break down and where DAN's architecture creates measurable advantage.

The benchmark suite proves this by measuring success rate, quality, token efficiency, cost, and learning across published academic benchmarks and custom long-tail scenarios.

## Benchmark Inventory

### Tier 1 — Priority (prove core thesis)

| # | Benchmark | Source | What It Tests | Subplan |
|---|-----------|--------|---------------|---------|
| 1 | **WorFBench** | ICLR 2025 | Workflow DAG generation from NL | [bench-1-worfbench](bench-1-worfbench.md) |
| 2 | **GAIA Level 3** | ICLR 2024 | 6+ step real-world tasks with tools | [bench-2-gaia](bench-2-gaia.md) |
| 3 | **AppWorld** | ACL 2024 Best Resource | Complex multi-API control flow | [bench-3-appworld](bench-3-appworld.md) |

### Custom Suite — Long-Tail Demonstration

| # | Benchmark | What It Tests | Subplan |
|---|-----------|---------------|---------|
| 4 | **Custom Long-Tail Suite** | 5 domain-specific 10-30 step workflows | [bench-4-custom-longtail](bench-4-custom-longtail.md) |

### Cross-Cutting

| # | Component | What It Covers | Subplan |
|---|-----------|----------------|---------|
| 5 | **Analysis Framework** | Metrics, ablation, learning curves, reporting | [bench-5-analysis-framework](bench-5-analysis-framework.md) |

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

Every benchmark runs against two baselines using the same underlying LLM:

| Baseline | Description | Simulates |
|----------|-------------|-----------|
| **A: Monolithic agent** | Same LLM + same tools, single conversation, self-directed tool use | Cursor agent / Claude agent mode |
| **B: Simple chain** | Hardcoded sequential script, no branching/loops/context management | n8n / Zapier-style automation |

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

1. **bench-5** (analysis framework) — build harness first so all benchmarks report consistently
2. **bench-1** (WorFBench) — smallest adapter surface, directly tests meta-orchestrator DAG quality
3. **bench-2** (GAIA) — highest-visibility public leaderboard, proves end-to-end capability
4. **bench-4** (custom long-tail) — demonstrates DAN-specific features no published benchmark covers
5. **bench-3** (AppWorld) — largest integration surface, leverages MCP bridge

## Marketing Deliverables from Benchmark Results

- **Leaderboard placement** on GAIA (public HuggingFace leaderboard)
- **"Impossible Workflow Challenge"** — the custom suite as a public challenge to other tools
- **Peer-reviewable paper** — WorFBench + GAIA + ablation analysis = workshop/conference submission
- **Cost comparison calculator** — interactive demo showing token/cost savings per pipeline depth
- **Landing page data** — success rate × complexity chart, cost-over-time learning curve

## Dependencies

- Meta-orchestrator (`MetaController`, `WorkflowPlanner`, `IntentCompiler`) must handle benchmark task descriptions
- `ToolRegistry` must support benchmark-required tools (web_search, pdf_read, python_eval, shell, file I/O)
- Engine token/cost tracking must be per-node granular for ablation
- Learning system (`DAN_LEARNING_MODE=1`) must be stable for multi-run experiments
