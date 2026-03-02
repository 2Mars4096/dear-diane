---
type: code
language: python
---

> Accepts: results (array), strategies_tried (array), iteration (integer), start_year (integer), end_year (integer), max_factors (integer), new_results (array), new_strategies (array)
> Returns: results (array), strategies_tried (array), iteration (number), try_more (boolean), start_year (number), end_year (number), max_factors (number)

Governor: accumulates results, increments iteration, enforces stopping conditions. All state fields are injected by scope; new_results/new_strategies come from merge via data edges. No defensive defaults needed — scope and port defaults guarantee values.

```python
results = list(results) + list(new_results)
strategies_tried = list(strategies_tried) + list(new_strategies)
iteration = iteration + 1

seen = set()
dedup = []
for s in strategies_tried:
    if not isinstance(s, dict):
        continue
    sid = str(s.get("strategy_id", "")).strip()
    if not sid or sid in seen:
        continue
    seen.add(sid)
    dedup.append(s)
strategies_tried = dedup

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
