---
type: code
language: python
---

> Accepts: department_code (string)
> Returns: tracking_list (object)

Loads the department's persistent tracking list from `output/tracking_{dept_code}.json`. Returns empty structure on first run. Port default provides `""` for department_code.

```python
department_code = str(department_code) if department_code else "MOM"
tracking_path = Path("examples/vibe_research_v2_md/output") / f"tracking_{department_code}.json"
tracking_list = {"tried": [], "to_try": [], "department_notes": ""}
if tracking_path.exists():
    try:
        raw = json.loads(tracking_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            tracking_list = {
                "tried": list(raw.get("tried", [])),
                "to_try": list(raw.get("to_try", [])),
                "department_notes": str(raw.get("department_notes", "")),
            }
    except (json.JSONDecodeError, OSError):
        pass
result = {"tracking_list": tracking_list}
```
