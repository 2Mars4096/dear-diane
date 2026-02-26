---
type: code
language: python
---

> Accepts: input (object)
> Returns: results (array)

Extracts results array from adaptive loop output (gate.done). Same format as grid_run.results for write_csv and plot_one.

```python
inp = input or {}
inner = inp.get("result", inp) if isinstance(inp.get("result"), dict) else inp
results = list(inner.get("results", [])) if isinstance(inner.get("results"), list) else []
result = {"results": results, "result": results}
```
