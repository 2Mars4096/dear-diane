# Vibe Research — Factor Analysis

Multi-department quantitative factor research workflow. CRSP/Compustat data from `AUTO_QUANT_ROOT` (default: `~/Dropbox/CUHK-phd/projects/auto-quant`).

## Architecture (v2)

```
entry → while(try_more, max: 60) {
    orchestrator → [dept_MOM, dept_REV, dept_FUND] → merge → governor
} → write_csv
```

Three fixed departments execute one-by-one (MOM → REV → FUND). An orchestrator assigns themes and halt/continue decisions each iteration. Loop state (results, strategies, iteration counters) is managed via the while-gate's `state_schema`. `max_factors` caps attempted strategies (`strategies_tried`) as well as aggregated results, so retries/failures do not run unbounded.

Each department runs a generic pipeline: gate (`skip` routes to `dept_halt`) → load tracking → strategy manager (LLM) → [builtin backtest | custom coder+run] → bug fixer (plot immediately) → save tracking (result status updated immediately). Per-strategy result snapshots are written immediately to `output/results/{strategy}.json`, with rolling snapshots in `output/latest_result_{dept}.json`.

Backtests enforce a formation-universe price rule: stocks with `|price| < 1` at portfolio formation are excluded.
For custom strategies, generated scripts are persisted before execution at
`output/scripts/{strategy}.py`.

## File Inventory

| File | Type | Purpose |
|------|------|---------|
| `workflow_multi_dept.md` | workflow | Top-level: entry → loop → csv |
| `entry.md` | code | Initialize loop state from run inputs |
| `iteration.md` | composite | One iteration: orchestrator → 3 depts (sequential) → merge → governor |
| `orchestrator.md` | llm | Review state, assign themes/actions per department |
| `department.md` | composite | Generic dept pipeline (referenced 3x) |
| `pass_assignment_after_prev.md` | code | Serializes department execution order (MOM→REV→FUND) |
| `dept_gate.md` | code | Unpack orchestrator assignment, check halt |
| `dept_halt.md` | code | Halt-branch no-op sink (isolates skip gate path) |
| `strategy_manager.md` | llm | Pick next strategy for this department |
| `strategy_coder.md` | llm | Write `build_factor()` Python code for custom strategies |
| `strategy_to_item.md` | code | Map builtin strategy config to backtest item |
| `backtest_runner.md` | tool | Run builtin momentum/reversal backtest |
| `run_strategy_or_backtest.md` | tool | Execute custom strategy code |
| `bug_fixer.md` | code | Normalize backtest output, catch errors |
| `load_tracking.md` | code | Load department tracking list from disk |
| `save_tracking.md` | code | Persist updated tracking list to disk |
| `merge.md` | code | Merge 3 department outputs |
| `governor.md` | code | Accumulate results, enforce stopping conditions |
| `write_csv.md` | code | Write grid_summary.csv |

## Running

```bash
python examples/vibe_research_md/run_multi_dept.py --start 2012 --end 2022 --max-factors 10
python examples/vibe_research_md/run_multi_dept.py --debug-events
python examples/vibe_research_md/run_multi_dept.py --build-only
python examples/vibe_research_md/run_multi_dept.py --emit-python
```

## Requirements

- `DAN_LLM_API_KEY` in `.env` or environment
- `AUTO_QUANT_ROOT` pointing to data directory (CRSP + Compustat)
- `pip install matplotlib` for plots
