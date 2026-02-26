---
type: code
language: python
---

> Accepts: results (array), strategies_tried (array), iteration (number), backtest_result (object), strategy_id (string), strategy_type (string), params (object), try_more (boolean), start_year (number), end_year (number)
> Returns: results (array), strategies_tried (array), iteration (number), try_more (boolean), start_year (number), end_year (number)

Merges new backtest result into running lists. Never fails — always returns valid structure.

```python
try:
    results = list(results) if isinstance(results, list) else []
except NameError:
    results = []
try:
    strategies_tried = list(strategies_tried) if isinstance(strategies_tried, list) else []
except NameError:
    strategies_tried = []
try:
    iteration = int(iteration) if iteration is not None else 0
except NameError:
    iteration = 0
try:
    try_more = bool(try_more) if try_more is not None else True
except NameError:
    try_more = True
try:
    start_year = int(start_year) if start_year is not None else 2010
except NameError:
    start_year = 2010
try:
    end_year = int(end_year) if end_year is not None else 2023
except NameError:
    end_year = 2023
try:
    backtest_result = backtest_result if isinstance(backtest_result, dict) else {}
except NameError:
    backtest_result = {}
try:
    strategy_id = strategy_id if strategy_id is not None else ""
except NameError:
    strategy_id = ""
try:
    strategy_type = strategy_type if strategy_type is not None else ""
except NameError:
    strategy_type = ""
try:
    params = params if params is not None else {}
except NameError:
    params = {}
strategy_config = {"strategy_id": strategy_id, "strategy_type": strategy_type, "params": params or {}}

if backtest_result:
    results.append(backtest_result)
if strategy_id or strategy_type:
    strategies_tried.append(strategy_config)
iteration = iteration + 1

result = {
    "results": results,
    "strategies_tried": strategies_tried,
    "iteration": iteration,
    "try_more": try_more,
    "start_year": start_year,
    "end_year": end_year,
}
```
