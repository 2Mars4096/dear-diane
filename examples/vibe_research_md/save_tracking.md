---
type: code
language: python
---

> Accepts: department_code (string), updated_tracking (object)
> Returns: result (object)

Persists the strategy manager's updated tracking list to `output/tracking_{dept_code}.json`.

```python
try:
    department_code = str(department_code) if department_code else "MOM"
except NameError:
    department_code = "MOM"
try:
    tracking = dict(updated_tracking) if isinstance(updated_tracking, dict) else {}
except NameError:
    tracking = {}
out_dir = Path("examples/vibe_research_md/output")
out_dir.mkdir(parents=True, exist_ok=True)
tracking_path = out_dir / f"tracking_{department_code}.json"
to_save = {
    "department_code": department_code,
    "tried": list(tracking.get("tried", [])),
    "to_try": list(tracking.get("to_try", [])),
    "department_notes": str(tracking.get("department_notes", "")),
}
tracking_path.write_text(json.dumps(to_save, indent=2, default=str), encoding="utf-8")
result = {"saved": str(tracking_path)}
```
