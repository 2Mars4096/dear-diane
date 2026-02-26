---
type: tool
tool_id: run_python
tool_config: {}
---

> Accepts: code (string), **context (any keyword args — e.g. item, results, out_dir)
> Returns: result (any), stdout (string), stderr (string), error (string | null)

Generic Python execution. Code must assign to `result` or `output` for structured return. Context kwargs are injected as variables. Preferred pattern for agent-generated code (plotting, CSV, transforms).
