# Vibe Research — Factor Analysis

Multi-department quantitative factor research workflow. CRSP/Compustat data from `AUTO_QUANT_ROOT` (default: `~/Dropbox/CUHK-phd/projects/auto-quant`).

## quant_lib

CRSP/Compustat loaders, momentum factor, quintile backtest. Run directly:

```bash
python examples/vibe_research_md/quant_lib/run_backtest.py --start 2015 --end 2023
```

## Multi-Department Adaptive (workflow_multi_dept)

See [WORKFLOW.md](WORKFLOW.md) for design notes. **Flattened:** Drill into orchestrator_and_departments to see orchestrator + all department nodes (strategy manager, coder, backtest, etc.) in one view. Orchestrator manages departments (max 6, delete for good). Strategy Manager (per department) with naming `dept_code+numbering+desc`. Strategy Coder produces Python code; **run_strategy_script** tool executes script-as-param, validates factor schema, runs backtest. Built-in (momentum/reversal) uses run_backtest; custom strategies use run_strategy_script. Factor schema: `date`, `permno`, `ret`, `factor`|`mom`. Compustat: 6-month lag between data date and formation. **max_factors** (hard limit): workflow stops when this many factors have been generated.

```bash
python examples/vibe_research_md/run_multi_dept.py --start 2012 --end 2022 --max-factors 10
python examples/vibe_research_md/run_multi_dept.py --build-only  # save graphs/vibe_research_multi_dept.json
```

**Plots require matplotlib.** Install with: `pip install matplotlib` or `pip install dan[quant]`.

Output: `output/grid_summary.csv`, `output/plots/`, `output/department_state.json`. `dan-serve` auto-reloads on code changes by default; use `--no-reload` to disable.
