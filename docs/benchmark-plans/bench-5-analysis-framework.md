# Bench 5: Analysis & Reporting Framework

**Parent:** [benchmark-plan](benchmark-plan.md)
**Status:** not-started
**Goal:** Build the shared measurement infrastructure, ablation methodology, and reporting pipeline so all benchmarks produce consistent, comparable, publication-ready results.

## Why This First

The analysis framework must exist before running any benchmark. Without consistent measurement, each benchmark produces ad-hoc results that can't be compared or aggregated. Build once, use everywhere.

## Components

### 1. Metrics Collector

A standardized data structure that every benchmark adapter populates after each run.

```python
@dataclass
class BenchmarkResult:
    benchmark: str          # "worfbench" | "gaia" | "appworld" | "custom_litreview" | ...
    task_id: str            # benchmark-specific task identifier
    approach: str           # "dan_meta" | "dan_solver" | "dan_handbuilt" | "monolithic" | "simple_chain"
    
    # Outcome
    success: bool           # task completed with acceptable output
    quality_score: float    # 0-1 normalized (benchmark-specific mapping)
    quality_method: str     # "exact_match" | "human_1_5" | "llm_judge" | "test_pass_rate"
    
    # Efficiency
    total_tokens: int       # sum of all input + output tokens
    total_cost_usd: float   # sum of all API costs
    wall_clock_seconds: float
    
    # Granular
    per_node_tokens: dict[str, int]     # node_id → tokens
    per_node_cost: dict[str, float]     # node_id → cost
    per_node_model: dict[str, str]      # node_id → model used
    per_node_duration: dict[str, float] # node_id → seconds
    
    # Failure analysis
    failure_point: str | None     # node_id or step number where failure occurred
    failure_type: str | None      # "context_confusion" | "tool_error" | "timeout" | "quality_gate" | ...
    recovery_attempted: bool
    recovery_succeeded: bool
    
    # Learning (multi-run)
    run_number: int              # 1-indexed, for learning curve analysis
    learning_features_active: list[str]  # ["prompt_optimization", "model_tiering", ...]
    
    # Metadata
    llm_model: str               # primary model used
    dan_version: str
    timestamp: datetime
    config: dict                 # full run config for reproducibility
```

### 2. Ablation Controller

Automates running the same benchmark task with different feature configurations.

```python
ABLATION_CONFIGS = {
    "full":             {"projection": True,  "while_loop": True,  "tier_policy": True,  "parallel": True,  "learning": True},
    "no_projection":    {"projection": False, "while_loop": True,  "tier_policy": True,  "parallel": True,  "learning": True},
    "no_while_loop":    {"projection": True,  "while_loop": False, "tier_policy": True,  "parallel": True,  "learning": True},
    "no_tier_policy":   {"projection": True,  "while_loop": True,  "tier_policy": False, "parallel": True,  "learning": True},
    "no_parallel":      {"projection": True,  "while_loop": True,  "tier_policy": True,  "parallel": False, "learning": True},
    "no_learning":      {"projection": True,  "while_loop": True,  "tier_policy": True,  "parallel": True,  "learning": False},
    "baseline_minimal": {"projection": False, "while_loop": False, "tier_policy": False, "parallel": False, "learning": False},
}
```

For each ablation config, map to actual DAN engine/config toggles:
- `projection: False` → set all context edges to `pass_everything=True`
- `while_loop: False` → convert review-revise loops to single-pass
- `tier_policy: False` → set `DAN_ENABLE_TIER_POLICY=0`, use single model everywhere
- `parallel: False` → serialize all ForEach/fan-out nodes
- `learning: False` → set `DAN_LEARNING_MODE=0`

### 3. Results Database

Store all results in a structured format for querying and visualization.

- **Storage:** SQLite database at `benchmarks/results.db`
- **Tables:** `runs`, `per_node_metrics`, `ablation_runs`, `learning_curves`
- **Export:** CSV/Parquet for analysis notebooks, JSON for reports

### 4. Visualization Pipeline

Standard charts generated from the results database:

| Chart | What It Shows | Used In |
|-------|--------------|---------|
| **Success rate × complexity** | DAN vs. baselines as task complexity increases | Landing page, paper |
| **Token usage comparison** | Per-approach token consumption by benchmark | Cost calculator |
| **Cost over time (learning curve)** | Cost per run across 5 runs | Marketing, paper |
| **Ablation waterfall** | Feature contribution to overall score | Paper |
| **Failure mode distribution** | Where each approach breaks down | Internal analysis |
| **Per-node cost heatmap** | Cost distribution across workflow nodes | Token optimization |
| **Quality vs. cost Pareto** | Trade-off frontier for model tier configurations | Tier policy tuning |

### 5. Report Generator

Automated report from results database:

```
# DAN Benchmark Report — {date}

## Summary
- Benchmarks run: {N}
- Total tasks: {N}
- DAN success rate: {X}% vs. monolithic: {Y}% (Δ{Z}%)
- Token savings: {X}% average via context projection
- Cost savings: {X}x average via model tiering

## Per-Benchmark Results
[table + charts per benchmark]

## Feature Ablation
[waterfall chart showing contribution of each feature]

## Learning Curves
[cost and quality over 5 runs]

## Failure Analysis
[taxonomy of failure modes by approach]
```

## Tasks

- [ ] 1. **Data models**
  - [ ] 1-1. Define `BenchmarkResult` Pydantic model
  - [ ] 1-2. Define `AblationConfig` model
  - [ ] 1-3. Define per-node metrics schema
  - [ ] 1-4. Define learning curve data schema
- [ ] 2. **Results database**
  - [ ] 2-1. SQLite schema design
  - [ ] 2-2. Insert/query API
  - [ ] 2-3. CSV/Parquet export
  - [ ] 2-4. Deduplication and idempotency (safe to re-run)
- [ ] 3. **Metrics collector integration**
  - [ ] 3-1. Hook into `Engine.run()` to capture per-node token/cost/duration
  - [ ] 3-2. Hook into `MetaController` to capture planning time/tokens
  - [ ] 3-3. Build monolithic agent baseline runner with same metric capture
  - [ ] 3-4. Build simple chain baseline runner with same metric capture
- [ ] 4. **Ablation controller**
  - [ ] 4-1. Config → engine toggle mapping
  - [ ] 4-2. Automated ablation runner (loop over configs × tasks)
  - [ ] 4-3. Results aggregation per config
- [ ] 5. **Visualization**
  - [ ] 5-1. Success rate × complexity chart (matplotlib/plotly)
  - [ ] 5-2. Token usage comparison bar chart
  - [ ] 5-3. Learning curve line chart
  - [ ] 5-4. Ablation waterfall chart
  - [ ] 5-5. Failure mode pie/bar chart
  - [ ] 5-6. Per-node cost heatmap
- [ ] 6. **Report generator**
  - [ ] 6-1. Markdown template with chart placeholders
  - [ ] 6-2. Auto-fill from results database
  - [ ] 6-3. PDF export (optional, via pandoc or weasyprint)
- [ ] 7. **Benchmark CLI**
  - [ ] 7-1. `python -m benchmarks run <benchmark> [--ablation] [--runs N]`
  - [ ] 7-2. `python -m benchmarks report [--format md|pdf|html]`
  - [ ] 7-3. `python -m benchmarks compare <run1> <run2>`

## Monolithic Agent Baseline Design

The monolithic baseline must use the same LLM and the same tools as DAN, but in a single-conversation ReAct loop:

```python
class MonolithicAgent:
    """Single-conversation agent with tool access. Simulates Cursor/Claude agent mode."""
    
    def __init__(self, model: str, tools: list[Tool]):
        self.model = model
        self.tools = tools
        self.conversation = []
    
    async def solve(self, task: str) -> str:
        self.conversation.append({"role": "user", "content": task})
        while True:
            response = await llm_call(self.model, self.conversation, tools=self.tools)
            if response.has_tool_calls:
                results = await execute_tools(response.tool_calls)
                self.conversation.extend(results)
            else:
                return response.text
```

Key: the monolithic agent accumulates ALL tool results into one conversation. This is exactly the pattern that breaks at scale due to context confusion.

## Simple Chain Baseline Design

```python
class SimpleChain:
    """Sequential script with no branching, loops, or context management."""
    
    async def solve(self, task: str, steps: list[Step]) -> str:
        context = task
        for step in steps:
            result = await step.execute(context)
            context += "\n" + result  # accumulate everything
        return context
```

Key: no quality gates, no branching, no parallel execution. This is the n8n/Zapier baseline.

## Estimated Effort

- Data models + database: 2 days
- Metrics collector integration: 2 days
- Ablation controller: 1 day
- Visualization pipeline: 2 days
- Report generator: 1 day
- Benchmark CLI: 1 day
- **Total: ~9 days**

## Decisions

- (to be filled during execution)

## Notes

- Build this first — all other benchmarks depend on it
- Per-node metrics require engine instrumentation — verify existing token tracking is granular enough
- The monolithic baseline must be fair: same model, same tools, same information. The only difference is architecture.
- Consider making the visualization pipeline interactive (Plotly) for the marketing website
- The benchmark CLI should support `--dry-run` for testing without API calls
