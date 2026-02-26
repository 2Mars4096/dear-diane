---
type: tool
tool_id: run_strategy_script
tool_config:
  return_series: true
---

> Accepts: code (string), params (object), start_year (number), end_year (number), strategy_name (string)
> Returns: result (object), quintiles (object), spread_q5_q1_bps (number), dates (array), cumulative_quintiles (object), ls_cumulative (array), strategy_name (string), error (string)

Runs custom strategy code. Code must define build_factor(crsp_path, start_year, end_year, **params) -> DataFrame.
Factor must have columns date, permno, ret, factor|mom. Validates in try/except, runs backtest.
