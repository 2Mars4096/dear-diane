---
type: code
language: python
---

> Accepts: input (object)
> Returns: results (array), strategies_tried (array), iteration (number), try_more (boolean), start_year (number), end_year (number), max_factors (number), department_code (string), params_hint (object), active_departments (array), deleted_departments (array)

Unpacks persist_dept_state result for strategy_creation_multi.

```python
try:
    inp = input if input is not None else {}
except NameError:
    inp = {}
inner = inp.get("result", inp) if isinstance(inp.get("result"), dict) else inp
results = list(inner.get("results", [])) if isinstance(inner.get("results"), list) else []
strategies_tried = list(inner.get("strategies_tried", [])) if isinstance(inner.get("strategies_tried"), list) else []
iteration = int(inner.get("iteration", 0)) if inner.get("iteration") is not None else 0
try_more = bool(inner.get("try_more", True)) if inner.get("try_more") is not None else True
start_year = int(inner.get("start_year", 2010)) if inner.get("start_year") is not None else 2010
end_year = int(inner.get("end_year", 2023)) if inner.get("end_year") is not None else 2023
active = list(inner.get("active_departments", ["MOM"])) if isinstance(inner.get("active_departments"), list) else ["MOM"]
department_code = str(active[0]) if active else "MOM"
try:
    max_factors = max(1, int(inner.get("max_factors", 20))) if inner.get("max_factors") is not None else 20
except (NameError, TypeError):
    max_factors = 20
deleted = list(inner.get("deleted_departments", [])) if isinstance(inner.get("deleted_departments"), list) else []
params_hint = {}
if strategies_tried:
    last = strategies_tried[-1]
    if isinstance(last, dict):
        params_hint = dict(last.get("params", {}))
result = {
    "results": results,
    "strategies_tried": strategies_tried,
    "iteration": iteration,
    "try_more": try_more,
    "start_year": start_year,
    "end_year": end_year,
    "max_factors": max_factors,
    "department_code": department_code,
    "params_hint": params_hint,
    "active_departments": active,
    "deleted_departments": deleted,
}
```
