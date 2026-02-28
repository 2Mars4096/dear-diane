---
type: code
language: python
---

> Accepts: trigger (object)
> Returns: result (object)

No-op sink for halted department branches. Keeps branch gating isolated so
normal backtest paths are not accidentally skipped.

```python
try:
    _ = trigger
except NameError:
    _ = None
result = {}
```
