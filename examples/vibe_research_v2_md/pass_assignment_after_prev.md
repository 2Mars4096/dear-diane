---
type: code
language: python
---

> Accepts: assignment (object), prev_result (object)
> Returns: assignment (object), result (object)

Passes department assignment through after the previous department result arrives.
Used to serialize department execution one-by-one. Port defaults provide `{}` for both inputs if missing.

```python
_ = prev_result
result = {"assignment": dict(assignment) if isinstance(assignment, dict) else {}}
```
