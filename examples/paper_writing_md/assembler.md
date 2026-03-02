---
type: code
language: python
---

> Accepts: results (array), text (string)
> Returns: result (string)

```python
# results = list from for_each; text = optional fallback (single section)
raw = results if (results and isinstance(results, list)) else (text if text else [])
sections = raw if isinstance(raw, list) else [raw] if raw else []
draft_parts = []
for i, section in enumerate(sections):
    if isinstance(section, dict):
        part = section.get("text", str(section))
    else:
        part = str(section)
    draft_parts.append(part)
result = "\n\n---\n\n".join(draft_parts)
```
