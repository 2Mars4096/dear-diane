---
type: code
language: python
---

> Accepts: text (string)
> Returns: result (string)

```python
raw = results if "results" in locals() else text
sections = raw if isinstance(raw, list) else [raw]
draft_parts = []
for i, section in enumerate(sections):
    if isinstance(section, dict):
        text = section.get("text", str(section))
    else:
        text = str(section)
    draft_parts.append(text)

output = "\n\n---\n\n".join(draft_parts)
```
