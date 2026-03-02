---
type: code
language: python
---

> Accepts: start_year (integer), end_year (integer), max_factors (integer)
> Returns: results (array), strategies_tried (array), iteration (number), try_more (boolean), start_year (number), end_year (number), max_factors (number)

Entry point for multi-department workflow. Reads run inputs (with fallback defaults) and outputs initial loop state. The while-gate's state_schema picks these up as the initial scope values.

```python
_sy = int(start_year) if start_year else 2010
_ey = int(end_year) if end_year else 2023
_mf = max(1, int(max_factors)) if max_factors else 20
result = {
    "results": [],
    "strategies_tried": [],
    "iteration": 0,
    "try_more": True,
    "start_year": _sy,
    "end_year": _ey,
    "max_factors": _mf,
}
```
