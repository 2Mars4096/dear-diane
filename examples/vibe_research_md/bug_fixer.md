---
type: code
language: python
---

> Accepts: backtest_result (object)
> Returns: result (object)

Bug fixer: catches malformed or failed backtest output, returns safe structure so the loop never breaks. Aggregator always receives valid dict.

```python
try:
    r = backtest_result if isinstance(backtest_result, dict) else {}
except NameError:
    r = {}
if not r or r.get("error"):
    safe = {
        "strategy_name": r.get("strategy_name", "unknown"),
        "error": r.get("error", "backtest failed"),
        "spread_q5_q1_bps": 0,
        "quintiles": {},
        "dates": [],
        "cumulative_quintiles": {},
        "ls_cumulative": [],
    }
else:
    safe = r
result = safe
```
