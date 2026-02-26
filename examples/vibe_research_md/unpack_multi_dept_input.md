---
type: code
language: python
---

> Accepts: input (object)
> Returns: results (array), strategies_tried (array), iteration (number), try_more (boolean), start_year (number), end_year (number), max_factors (number), active_departments (array), deleted_departments (array), results_summary (object)


Unpacks loop input for multi-department iteration.

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
active_departments = list(inner.get("active_departments", [])) if isinstance(inner.get("active_departments"), list) else []
if not active_departments:
    active_departments = ["MOM", "REV", "FUND", "CASH", "OPS", "ML"]
deleted_departments = list(inner.get("deleted_departments", [])) if isinstance(inner.get("deleted_departments"), list) else []
try:
    max_factors = int(inner.get("max_factors", 20)) if inner.get("max_factors") is not None else 20
except (NameError, TypeError):
    max_factors = 20
max_factors = max(1, max_factors)
results_summary = {}
if results:
    by_name = {}
    for r in results:
        if isinstance(r, dict) and r.get("strategy_name"):
            by_name[r["strategy_name"]] = r.get("spread_q5_q1_bps", 0)
    results_summary = {"n_results": len(results), "by_strategy": by_name}
result = {
    "results": results,
    "strategies_tried": strategies_tried,
    "iteration": iteration,
    "try_more": try_more,
    "start_year": start_year,
    "end_year": end_year,
    "max_factors": max_factors,
    "active_departments": active_departments,
    "deleted_departments": deleted_departments,
    "results_summary": results_summary,
}
```
