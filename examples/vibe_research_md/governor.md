---
type: code
language: python
---

> Accepts: new_results (array), new_strategies (array)
> Returns: results (array), strategies_tried (array), iteration (number), try_more (boolean), start_year (number), end_year (number), max_factors (number)

Governor: accumulates results from this iteration into loop state, increments iteration, and enforces stopping conditions. Outputs all state fields so the while-gate scope is updated.

State fields (results, strategies_tried, iteration, start_year, end_year, max_factors) are injected by state_schema if not provided by explicit edges.

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
    start_year = int(start_year) if start_year is not None else 2010
except NameError:
    start_year = 2010
try:
    end_year = int(end_year) if end_year is not None else 2023
except NameError:
    end_year = 2023
try:
    max_factors = max(1, int(max_factors)) if max_factors is not None else 20
except NameError:
    max_factors = 20
try:
    new_results = list(new_results) if isinstance(new_results, list) else []
except NameError:
    new_results = []
try:
    new_strategies = list(new_strategies) if isinstance(new_strategies, list) else []
except NameError:
    new_strategies = []

results = results + new_results
strategies_tried = strategies_tried + new_strategies
iteration = iteration + 1

# Deduplicate strategies by strategy_id while preserving order.
seen = set()
dedup_strategies = []
for s in strategies_tried:
    if not isinstance(s, dict):
        continue
    sid = str(s.get("strategy_id", "")).strip()
    if not sid or sid in seen:
        continue
    seen.add(sid)
    dedup_strategies.append(s)
strategies_tried = dedup_strategies

try_more = True
safety_cap = max_factors + 10
if len(results) >= max_factors:
    try_more = False
if len(strategies_tried) >= max_factors:
    try_more = False
if iteration >= safety_cap:
    try_more = False
valid = [r for r in results if isinstance(r, dict) and not r.get("error")]
if len(results) > 0 and len(valid) == 0:
    try_more = False

result = {
    "results": results,
    "strategies_tried": strategies_tried,
    "iteration": iteration,
    "try_more": try_more,
    "start_year": start_year,
    "end_year": end_year,
    "max_factors": max_factors,
}
```
