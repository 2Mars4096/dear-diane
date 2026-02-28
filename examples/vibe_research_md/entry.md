---
type: code
language: python
---

> Accepts: start_year (number), end_year (number), max_factors (number)
> Returns: results (array), strategies_tried (array), iteration (number), try_more (boolean), start_year (number), end_year (number), max_factors (number)

Entry point for multi-department workflow. Reads run inputs and outputs initial loop state. The while-gate's state_schema picks these up as the initial scope values.

```python
try:
    start_year = int(start_year) if start_year is not None else 2010
except NameError:
    start_year = 2010
try:
    end_year = int(end_year) if end_year is not None else 2023
except NameError:
    end_year = 2023
try:
    max_factors = int(max_factors) if max_factors is not None else 20
except NameError:
    max_factors = 20
max_factors = max(1, max_factors)
result = {
    "results": [],
    "strategies_tried": [],
    "iteration": 0,
    "try_more": True,
    "start_year": start_year,
    "end_year": end_year,
    "max_factors": max_factors,
}
```
