---
type: code
language: python
---

> Accepts: assignment (object), prev_result (object)
> Returns: assignment (object), result (object)

Passes department assignment through after the previous department result arrives.
Used to serialize department execution one-by-one.

```python
try:
    assignment_obj = dict(assignment) if isinstance(assignment, dict) else {}
except NameError:
    assignment_obj = {}

# Read prev_result intentionally to enforce dependency ordering.
try:
    _ = dict(prev_result) if isinstance(prev_result, dict) else {}
except NameError:
    _ = {}

result = {"assignment": assignment_obj}
```
