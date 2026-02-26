---
type: code
language: python
---

> Accepts: to_delete (array), to_create (array), results (array), strategies_tried (array), iteration (number), try_more (boolean), start_year (number), end_year (number), max_factors (number), active_departments (array), deleted_departments (array)
> Returns: result (object)

Persists department state in fixed-department mode. Add/delete decisions are ignored for now; passes loop context through to strategy creation.

Uses pre-injected `json` and `Path` from code executor builtins (no import needed).

```python
fixed_departments = ["MOM", "REV", "FUND", "CASH", "OPS", "ML"]
# Fixed department mode for now: ignore dynamic add/delete decisions.
new_active = list(fixed_departments)
new_deleted = []
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
    start_year = int(start_year) if start_year else 2010
except NameError:
    start_year = 2010
try:
    end_year = int(end_year) if end_year else 2023
except NameError:
    end_year = 2023
try:
    max_factors = max(1, int(max_factors)) if max_factors is not None else 20
except NameError:
    max_factors = 20
out_dir = Path("examples/vibe_research_md/output")
out_dir.mkdir(parents=True, exist_ok=True)
state_path = out_dir / "department_state.json"
state_path.write_text(
    json.dumps(
        {"active": new_active, "deleted": new_deleted, "max_departments": len(fixed_departments)},
        indent=2,
    ),
    encoding="utf-8",
)
result = {
    "result": {
        "results": results,
        "strategies_tried": strategies_tried,
        "iteration": iteration,
        "try_more": try_more,
        "start_year": start_year,
        "end_year": end_year,
        "max_factors": max_factors,
        "active_departments": new_active,
        "deleted_departments": new_deleted,
    }
}
```
