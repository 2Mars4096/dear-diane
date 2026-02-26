---
type: code
language: python
---

> Accepts: backtest_result (object), strategy_id (string), strategy_type (string), params (object)
> Returns: result (object with backtest_result, strategy_id, strategy_type, params)

Outputs per-department contribution for merge_dept_results. Each department run produces one such object.

```python
try:
    backtest_result = backtest_result if isinstance(backtest_result, dict) else {}
except NameError:
    backtest_result = {}
try:
    strategy_id = str(strategy_id) if strategy_id else ""
except NameError:
    strategy_id = ""
try:
    strategy_type = str(strategy_type) if strategy_type else ""
except NameError:
    strategy_type = ""
try:
    params = dict(params) if isinstance(params, dict) else {}
except NameError:
    params = {}
result = {
    "backtest_result": backtest_result,
    "strategy_id": strategy_id,
    "strategy_type": strategy_type,
    "params": params,
}
```
