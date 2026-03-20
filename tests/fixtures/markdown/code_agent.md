---
type: code
language: python
---

> Accepts: data (object)
> Returns: result (object)

```python
import json
output = {"result": json.dumps(data, indent=2), "length": len(str(data))}
```
