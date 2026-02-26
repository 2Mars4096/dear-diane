---
type: code
language: python
---

> Accepts: results (array), strategies_tried (array), iteration (number), try_more (boolean), start_year (number), end_year (number), max_factors (number), active_departments (array), deleted_departments (array)
> Returns: result (object)

Governor: normalizes outputs so the loop never breaks. Controls stopping with hard constraints (all failed, max_factors reached, or safety cap hit). Outputs single object for gate.

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
    _incoming_try_more = bool(try_more) if try_more is not None else True
except NameError:
    _incoming_try_more = True
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
    active_departments = list(active_departments) if isinstance(active_departments, list) else []
except NameError:
    active_departments = []
try:
    deleted_departments = list(deleted_departments) if isinstance(deleted_departments, list) else []
except NameError:
    deleted_departments = []

# Governance policy: stop by hard constraints only.
try_more = True
safety_cap = max_factors + 10
valid_results = [r for r in results if isinstance(r, dict) and not r.get("error")]
if len(results) > 0 and len(valid_results) == 0:
    try_more = False
if len(results) >= max_factors:
    try_more = False
if iteration >= safety_cap:
    try_more = False
if len(active_departments) == 0:
    try_more = False
result = {
    "result": {
        "results": results,
        "strategies_tried": strategies_tried,
        "iteration": iteration,
        "try_more": try_more,
        "start_year": start_year,
        "end_year": end_year,
        "max_factors": max_factors,
        "active_departments": active_departments,
        "deleted_departments": deleted_departments,
    }
}
```
