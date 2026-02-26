---
type: composite
---

> Accepts: input (object)
> Returns: result (object)

One strategy cycle: unpack → strategy_manager_dept → if use_builtin then strategy_to_item→backtest else strategy_coder→run_strategy → bug_fixer → aggregator → governor.

## Agents

- [unpack](unpack_strategy_creation_multi.md)
- [strategy_manager_dept](strategy_manager_dept.md)
- [strategy_to_item](strategy_to_item.md)
- [backtest_runner](backtest_runner.md)
- [strategy_coder](strategy_coder.md)
- [run_strategy](run_strategy_or_backtest.md)
- [bug_fixer](bug_fixer.md)
- [aggregator](aggregator.md)
- [governor](governor.md)

## Flow

unpack.params_hint → strategy_manager_dept.params_hint
unpack.results → strategy_manager_dept.results
unpack.strategies_tried → strategy_manager_dept.strategies_tried
unpack.iteration → strategy_manager_dept.iteration
unpack.department_code → strategy_manager_dept.department_code
strategy_manager_dept | if("use_builtin", then: strategy_to_item, else: strategy_coder)
strategy_manager_dept.strategy_id → strategy_to_item.strategy_id
strategy_manager_dept.strategy_type → strategy_to_item.strategy_type
strategy_manager_dept.params → strategy_to_item.params
unpack.start_year → strategy_to_item.start_year
unpack.end_year → strategy_to_item.end_year
strategy_to_item.result → backtest_runner.item
strategy_manager_dept.strategy_id → strategy_coder.strategy_id
strategy_manager_dept.strategy_type → strategy_coder.strategy_type
strategy_manager_dept.params → strategy_coder.params
strategy_manager_dept.reuse_config → strategy_coder.reuse_config
unpack.department_code → strategy_coder.department_code
backtest_runner.result → bug_fixer.backtest_result
strategy_coder.code → run_strategy.code
strategy_manager_dept.params → run_strategy.params
unpack.start_year → run_strategy.start_year
unpack.end_year → run_strategy.end_year
strategy_manager_dept.strategy_id → run_strategy.strategy_name
run_strategy.result → bug_fixer.backtest_result
bug_fixer.result → aggregator.backtest_result
strategy_manager_dept.strategy_id → aggregator.strategy_id
strategy_manager_dept.strategy_type → aggregator.strategy_type
strategy_manager_dept.params → aggregator.params
unpack.results → aggregator.results
unpack.strategies_tried → aggregator.strategies_tried
unpack.iteration → aggregator.iteration
unpack.try_more → aggregator.try_more
unpack.start_year → aggregator.start_year
unpack.end_year → aggregator.end_year
aggregator.results → governor.results
aggregator.strategies_tried → governor.strategies_tried
aggregator.iteration → governor.iteration
aggregator.try_more → governor.try_more
aggregator.start_year → governor.start_year
aggregator.end_year → governor.end_year
unpack.max_factors → governor.max_factors
unpack.active_departments → governor.active_departments
unpack.deleted_departments → governor.deleted_departments
