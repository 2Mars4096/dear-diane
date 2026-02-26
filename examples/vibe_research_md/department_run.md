---
type: composite
---

> Accepts: item (object) — one department item from expand_departments
> Returns: result (object) — backtest_result, strategy_id, strategy_type, params

One department run: unpack item → load tracking → strategy manager → save tracking → if builtin then backtest else coder+run_strategy → bug fixer → output. Runs in parallel per department via each(department_run). Each manager maintains a persistent tracking list (tried + to_try roadmap) on disk.

## Agents

- [unpack_item](unpack_item.md)
- [load_tracking](load_tracking.md)
- [strategy_manager_dept](strategy_manager_dept.md)
- [save_tracking](save_tracking.md)
- [strategy_to_item](strategy_to_item.md)
- [backtest_runner](backtest_runner.md)
- [strategy_coder](strategy_coder.md)
- [run_strategy](run_strategy_or_backtest.md)
- [bug_fixer](bug_fixer.md)
- [output_per_dept](output_per_dept.md)

## Flow

unpack_item.department_code → load_tracking.department_code
load_tracking.tracking_list → strategy_manager_dept.tracking_list
unpack_item.department_code → strategy_manager_dept.department_code
unpack_item.params_hint → strategy_manager_dept.params_hint
unpack_item.results → strategy_manager_dept.results
unpack_item.strategies_tried → strategy_manager_dept.strategies_tried
unpack_item.iteration → strategy_manager_dept.iteration
unpack_item.n_total_results → strategy_manager_dept.n_total_results
strategy_manager_dept.updated_tracking → save_tracking.updated_tracking
unpack_item.department_code → save_tracking.department_code
unpack_item.start_year → strategy_to_item.start_year
unpack_item.end_year → strategy_to_item.end_year
strategy_manager_dept | if("use_builtin", then: strategy_to_item, else: strategy_coder)
strategy_manager_dept.strategy_id → strategy_to_item.strategy_id
strategy_manager_dept.strategy_type → strategy_to_item.strategy_type
strategy_manager_dept.params → strategy_to_item.params
strategy_manager_dept_if_strategy_to_item_strategy_coder.true → backtest_runner.branch_trigger
strategy_to_item.result → backtest_runner.item
strategy_manager_dept_if_strategy_to_item_strategy_coder.false → run_strategy.branch_trigger
strategy_manager_dept.strategy_id → strategy_coder.strategy_id
strategy_manager_dept.strategy_type → strategy_coder.strategy_type
strategy_manager_dept.params → strategy_coder.params
strategy_manager_dept.reuse_config → strategy_coder.reuse_config
unpack_item.department_code → strategy_coder.department_code
strategy_coder.code → run_strategy.code
strategy_manager_dept.params → run_strategy.params
unpack_item.start_year → run_strategy.start_year
unpack_item.end_year → run_strategy.end_year
strategy_manager_dept.strategy_id → run_strategy.strategy_name
backtest_runner.result → bug_fixer.backtest_result
run_strategy.result → bug_fixer.backtest_result
bug_fixer.result → output_per_dept.backtest_result
strategy_manager_dept.strategy_id → output_per_dept.strategy_id
strategy_manager_dept.strategy_type → output_per_dept.strategy_type
strategy_manager_dept.params → output_per_dept.params
