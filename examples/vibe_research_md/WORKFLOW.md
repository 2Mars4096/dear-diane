# Multi-Department Adaptive Workflow

Design notes for the vibe research multi-dept example. See `workflow_multi_dept.md` and `run_multi_dept.py`.

## Design Summary

- **Orchestrator:** Decides try_more, to_delete, to_create. Max 6 departments. Deleted persists.
- **Departments:** Run in parallel via each(). Per dept: Strategy Manager → Coder (if custom) or strategy_to_item (if built-in) → run_backtest / run_strategy_script → bug_fixer → aggregator.
- **Meta department:** Runs every N main-loop iterations. Monitors department outputs, Sharpe top K, blends (avg rankings), outputs BLEND_*.parquet.
- **run_strategy_script:** Script-as-param tool. Executes code, validates factor schema, runs backtest.
- **Factor schema:** date, permno, ret, factor|mom. Validated in run_strategy_script.

## Tasks (example-specific)

- [x] Factor schema (factor_schema.py) + validate_factor_df
- [x] run_backtest_from_factor_df in backtest.py
- [x] run_strategy_script tool
- [x] get_department_state, update_department_state tools
- [x] orchestrator, strategy_manager_dept, strategy_coder agents
- [x] workflow_multi_dept.md — flow with if(use_builtin) branch (single dept v1)
- [x] run_multi_dept.py script
- [x] apply_compustat_lag in factor_schema; compustat_lag_months
- [ ] Parallel departments via each(department_iteration) — orchestrator → persist → each(dept) → governor
- [ ] Strategy coder retry: wrap in loop(compile_check → coder → run_strategy, until: success, max: 3)
- [ ] meta_department composite — monitor, Sharpe, blend top K (every N iterations)

## Notes

- Meta runs every N iterations (C): gate or counter in loop.
- Department codes: LLM decides, pattern DOC + numbering + desc.
- Compustat gap: documented in prompts; hardcode in loader when used.
- Editor viz (loop_groups, orchestrator visibility): see project plan 6-13.
