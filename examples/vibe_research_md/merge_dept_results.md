---
type: code
language: python
---

> Accepts: persist_output (object), dept_results (array)
> Returns: result (object), results (array), strategies_tried (array), iteration (number), try_more (boolean), start_year (number), end_year (number), max_factors (number), active_departments (array), deleted_departments (array)

Merges per-department ForEach results into loop state for governor. Combines persist state with all department contributions (including failures for visibility); increments iteration once.

```python
try:
    inp = persist_output if persist_output is not None else {}
except NameError:
    inp = {}
inner = inp.get("result", inp) if isinstance(inp.get("result"), dict) else inp
results = list(inner.get("results", [])) if isinstance(inner.get("results"), list) else []
strategies_tried = list(inner.get("strategies_tried", [])) if isinstance(inner.get("strategies_tried"), list) else []
iteration = int(inner.get("iteration", 0)) if inner.get("iteration") is not None else 0
try_more = bool(inner.get("try_more", True)) if inner.get("try_more") is not None else True
start_year = int(inner.get("start_year", 2010)) if inner.get("start_year") is not None else 2010
end_year = int(inner.get("end_year", 2023)) if inner.get("end_year") is not None else 2023
max_factors = max(1, int(inner.get("max_factors", 20))) if inner.get("max_factors") is not None else 20
active_departments = list(inner.get("active_departments", [])) if isinstance(inner.get("active_departments"), list) else []
deleted_departments = list(inner.get("deleted_departments", [])) if isinstance(inner.get("deleted_departments"), list) else []

try:
    dept_results = list(dept_results) if isinstance(dept_results, list) else []
except NameError:
    dept_results = []

for c in dept_results:
    if isinstance(c, dict):
        bt = c.get("backtest_result")
        if bt and isinstance(bt, dict):
            results.append(bt)
        sid = c.get("strategy_id") or c.get("strategy_type")
        if sid or c.get("params"):
            strategies_tried.append({"strategy_id": c.get("strategy_id", ""), "strategy_type": c.get("strategy_type", ""), "params": c.get("params", {})})
iteration = iteration + 1

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
    },
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
```
