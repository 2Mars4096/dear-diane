---
type: tool
tool_id: run_backtest
tool_config:
  return_series: true
---

> Accepts: item (object), branch_trigger (object)
> Returns: quintiles (array), spread_q5_q1_bps (number), dates (array), cumulative_quintiles (object), ls_cumulative (array), strategy_name (string), factor_saved_path (string), error (string)

Runs one backtest. Item must have lookback, skip, start_year, end_year.
Formation-universe filter is enforced: rows with `|price| < 1` are excluded.
