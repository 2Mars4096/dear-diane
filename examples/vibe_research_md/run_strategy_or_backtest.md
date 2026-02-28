---
type: tool
tool_id: run_strategy_script
tool_config:
  return_series: true
---

> Accepts: code (string), params (object), start_year (number), end_year (number), strategy_name (string), branch_trigger (object)
> Returns: result (object), quintiles (array), spread_q5_q1_bps (number), dates (array), cumulative_quintiles (object), ls_cumulative (array), strategy_name (string), factor_saved_path (string), factor_save_error (string), script_saved_path (string), script_save_error (string), error (string)

Runs custom strategy code. Code is saved first to `output/scripts/{strategy_name}.py`,
then executed. Script must define build_factor(crsp_path, start_year, end_year, **params)
-> DataFrame. Factor must have columns date, permno, ret, factor|mom.
Validates in try/except, saves factor parquet, then runs backtest.
Formation-universe filter is enforced during backtest: rows with `|price| < 1` are excluded.
