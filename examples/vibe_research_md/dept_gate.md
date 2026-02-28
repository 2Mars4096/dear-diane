---
type: code
language: python
---

> Accepts: assignment (object)
> Returns: department_code (string), theme (string), action (string), params_hint (object), start_year (number), end_year (number), skip (boolean)

Unpacks the orchestrator's assignment for this department. Extracts fields and sets skip=true when action=="halt" so downstream nodes can short-circuit.

```python
try:
    a = dict(assignment) if isinstance(assignment, dict) else {}
except NameError:
    a = {}
department_code = str(a.get("dept_code", "MOM"))
theme = str(a.get("theme", ""))
action = str(a.get("action", "continue"))
params_hint = dict(a.get("params_hint", {})) if isinstance(a.get("params_hint"), dict) else {}
start_year = int(a.get("start_year", 2010))
end_year = int(a.get("end_year", 2023))
skip = action != "continue"
result = {
    "department_code": department_code,
    "theme": theme,
    "action": action,
    "params_hint": params_hint,
    "start_year": start_year,
    "end_year": end_year,
    "skip": skip,
}
```
