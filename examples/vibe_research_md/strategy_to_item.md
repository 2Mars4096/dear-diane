---
type: code
language: python
---

> Accepts: strategy_id (string), strategy_type (string), params (object), start_year (number), end_year (number)
> Returns: result (object)

Maps strategy config to backtest item format.

```python
params = params if isinstance(params, dict) else {}
strategy_type = str(strategy_type) if strategy_type else "momentum"
lookback = int(params.get("lookback_months", params.get("lookback", 12)))
skip = int(params.get("skip_months", params.get("skip", 1)))
name = str(strategy_id) if strategy_id else f"momentum_{lookback}_{skip}"
item = {
    "lookback": lookback,
    "skip": skip,
    "name": name,
    "start_year": int(start_year) if start_year else 2010,
    "end_year": int(end_year) if end_year else 2023,
}
result = {"result": item}
```
