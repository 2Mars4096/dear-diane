---
type: code
language: python
---

> Accepts: item (object)
> Returns: department_code (string), params_hint (object), results (array), strategies_tried (array), iteration (number), n_total_results (number), start_year (number), end_year (number), max_factors (number), active_departments (array), deleted_departments (array)

Unpacks a department item from ForEach for strategy creation. Receives item from each(department_run).

```python
try:
    item = item if item is not None else {}
except NameError:
    item = {}
department_code = str(item.get("department_code", "MOM"))
params_hint = dict(item.get("params_hint", {})) if isinstance(item.get("params_hint"), dict) else {}
results = list(item.get("results", [])) if isinstance(item.get("results"), list) else []
strategies_tried = list(item.get("strategies_tried", [])) if isinstance(item.get("strategies_tried"), list) else []
iteration = int(item.get("iteration", 0)) if item.get("iteration") is not None else 0
start_year = int(item.get("start_year", 2010)) if item.get("start_year") is not None else 2010
end_year = int(item.get("end_year", 2023)) if item.get("end_year") is not None else 2023
max_factors = max(1, int(item.get("max_factors", 20))) if item.get("max_factors") is not None else 20
n_total_results = int(item.get("n_total_results", 0)) if item.get("n_total_results") is not None else 0
active_departments = list(item.get("active_departments", [])) if isinstance(item.get("active_departments"), list) else []
deleted_departments = list(item.get("deleted_departments", [])) if isinstance(item.get("deleted_departments"), list) else []
result = {
    "department_code": department_code,
    "params_hint": params_hint,
    "results": results,
    "strategies_tried": strategies_tried,
    "iteration": iteration,
    "n_total_results": n_total_results,
    "start_year": start_year,
    "end_year": end_year,
    "max_factors": max_factors,
    "active_departments": active_departments,
    "deleted_departments": deleted_departments,
}
```
