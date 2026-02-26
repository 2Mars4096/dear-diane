---
type: code
language: python
---

> Accepts: input (object)
> Returns: result (array of department items)

Expands persist_dept_state result into a list of department items for parallel execution. One item per active department. Each department receives only its own results, strategies_tried, and params_hint — filtered by strategy_id/strategy_name prefix matching the department code (e.g. MOM_001_...).

```python
try:
    inp = input if input is not None else {}
except NameError:
    inp = {}
inner = inp.get("result", inp) if isinstance(inp.get("result"), dict) else inp
all_results = list(inner.get("results", [])) if isinstance(inner.get("results"), list) else []
all_strategies = list(inner.get("strategies_tried", [])) if isinstance(inner.get("strategies_tried"), list) else []
iteration = int(inner.get("iteration", 0)) if inner.get("iteration") is not None else 0
try_more = bool(inner.get("try_more", True)) if inner.get("try_more") is not None else True
start_year = int(inner.get("start_year", 2010)) if inner.get("start_year") is not None else 2010
end_year = int(inner.get("end_year", 2023)) if inner.get("end_year") is not None else 2023
max_factors = max(1, int(inner.get("max_factors", 20))) if inner.get("max_factors") is not None else 20
active = list(inner.get("active_departments", [])) if isinstance(inner.get("active_departments"), list) else []
if not active:
    active = ["MOM", "REV", "FUND", "CASH", "OPS", "ML"]
deleted = list(inner.get("deleted_departments", [])) if isinstance(inner.get("deleted_departments"), list) else []

def _belongs(name_or_id, dept_code):
    s = str(name_or_id) if name_or_id else ""
    return s.upper().startswith(dept_code.upper() + "_")

items = []
for dept in active:
    dept_results = [r for r in all_results if isinstance(r, dict) and _belongs(r.get("strategy_name", ""), dept)]
    dept_strategies = [s for s in all_strategies if isinstance(s, dict) and _belongs(s.get("strategy_id", ""), dept)]
    dept_hint = {}
    if dept_strategies:
        last = dept_strategies[-1]
        if isinstance(last.get("params"), dict):
            dept_hint = dict(last["params"])
    items.append({
        "department_code": str(dept),
        "params_hint": dept_hint,
        "results": dept_results,
        "strategies_tried": dept_strategies,
        "n_total_results": len(all_results),
        "iteration": iteration,
        "try_more": try_more,
        "start_year": start_year,
        "end_year": end_year,
        "max_factors": max_factors,
        "active_departments": active,
        "deleted_departments": deleted,
    })
result = items
```
