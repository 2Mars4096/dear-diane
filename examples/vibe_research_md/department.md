---
type: composite
---

> Accepts: assignment (object)
> Returns: result (object)

**Generic department pipeline.** Receives an assignment (dept_code, theme, action, params_hint, start_year, end_year) from the orchestrator. If action=="halt", dept_gate sets skip=true and routes to a dedicated halt node so plotting/backtest normalization is not branch-gated away on normal runs. Otherwise: load tracking → strategy manager → [builtin backtest | custom coder+run] → bug fixer (immediate per-factor plot) → save tracking (immediate status update) → output.

Instances of this composite can be wired in parallel or sequentially — one per department.

## Agents

- [dept_gate](dept_gate.md)
- [dept_halt](dept_halt.md)
- [load_tracking](load_tracking.md)
- [strategy_manager](strategy_manager.md)
- [save_tracking](save_tracking.md)
- [strategy_to_item](strategy_to_item.md)
- [backtest_runner](backtest_runner.md)
- [strategy_coder](strategy_coder.md)
- [run_strategy](run_strategy_or_backtest.md)
- [bug_fixer](bug_fixer.md)

## Flow

dept_gate | if("skip", then: dept_halt, else: load_tracking)
dept_gate.department_code → load_tracking.department_code
dept_gate.department_code → strategy_manager.department_code
dept_gate.theme → strategy_manager.theme
dept_gate.params_hint → strategy_manager.params_hint
dept_gate.start_year → strategy_to_item.start_year
dept_gate.end_year → strategy_to_item.end_year
load_tracking.tracking_list → strategy_manager.tracking_list
strategy_manager.updated_tracking → save_tracking.updated_tracking
dept_gate.department_code → save_tracking.department_code
strategy_manager | if("use_builtin", then: strategy_to_item, else: strategy_coder)
strategy_manager.strategy_id → strategy_to_item.strategy_id
strategy_manager.strategy_type → strategy_to_item.strategy_type
strategy_manager.params → strategy_to_item.params
strategy_manager_if_strategy_to_item_strategy_coder.true → backtest_runner.branch_trigger
strategy_to_item.result → backtest_runner.item
strategy_manager_if_strategy_to_item_strategy_coder.false → run_strategy.branch_trigger
strategy_manager.strategy_id → strategy_coder.strategy_id
strategy_manager.strategy_type → strategy_coder.strategy_type
strategy_manager.params → strategy_coder.params
strategy_manager.reuse_config → strategy_coder.reuse_config
dept_gate.department_code → strategy_coder.department_code
strategy_coder.code → run_strategy.code
strategy_manager.params → run_strategy.params
dept_gate.start_year → run_strategy.start_year
dept_gate.end_year → run_strategy.end_year
strategy_manager.strategy_id → run_strategy.strategy_name
backtest_runner.result → bug_fixer.backtest_result
run_strategy.result → bug_fixer.backtest_result
bug_fixer.result → save_tracking.backtest_result
