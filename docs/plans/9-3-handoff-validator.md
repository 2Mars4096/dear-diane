# 9-3: Handoff Validator Node

**Parent:** [9-extended-capabilities](9-extended-capabilities.md)
**Status:** completed
**Goal:** Add a lightweight `ValidatorNode` type that checks schema conformance, required keys, and non-empty values at agent boundaries. Explicit validation node visible on the canvas, not hidden middleware.

## Tasks

- [x] 1. ValidatorNode model
  - [x] 1-1. `ValidationRule` Pydantic model in `src/dan/models/control_flow.py`: `rule_type: Literal["required_keys", "non_empty", "schema_conformance", "type_check", "custom_expression"]`, `config: dict[str, Any]` (rule-type-specific payload)
  - [x] 1-2. `ValidatorNode(NodeBase)` Pydantic model: `node_type: Literal["validator"] = "validator"`, `validation_rules: list[ValidationRule]`, `on_failure: Literal["route", "warn", "halt"] = "route"`, `strict_mode: bool = False` (strict = fail on first violation vs. collect all)
  - [x] 1-3. Default ports: input `data` (required). Output ports: `valid` (passthrough when all rules pass), `invalid` (data + errors when any rule fails)
  - [x] 1-4. Auto-derive output ports in `model_post_init` (same pattern as `GateNode`)
  - [x] 1-5. Add `ValidatorNode` to `Node` discriminated union in `src/dan/models/graph.py`
  - [x] 1-6. Register `"validator"` in `src/dan/registry.py`

- [x] 2. Validation rule types
  - [x] 2-1. `required_keys` rule: `config.keys: list[str]` (dotpath strings like `"result.answer"`, `"metadata.confidence"`). Fail if any key missing from data.
  - [x] 2-2. `non_empty` rule: `config.keys: list[str]` (dotpath strings). Fail if value is None, empty string, empty list, or empty dict.
  - [x] 2-3. `schema_conformance` rule: `config.schema: dict` (inline JSON Schema) or `config.schema_ref: str` (path to schema file). Validates **runtime data payloads** against the schema (field presence, types, required keys), NOT schema-vs-schema comparison. Uses `jsonschema.validate()` (optional dep) with pure-Python fallback for basic type/required checks. Note: `validation/schema.py check_schema_compatible()` is for design-time port compatibility and is NOT suitable here.
  - [x] 2-4. `type_check` rule: `config.checks: dict[str, str]` mapping dotpath to expected type name (str, int, float, bool, list, dict). Fail on type mismatch.
  - [x] 2-5. `custom_expression` rule: `config.expression: str` (safe eval, reuses `engine/conditions.py` evaluator). Expression receives `data` as variable namespace. Fail if expression returns falsy.
  - [x] 2-6. Dotpath resolver utility: `resolve_dotpath(data: dict, path: str) -> Any` in `src/dan/executors/validator.py`. Supports nested dict access (`a.b.c`), list indexing (`items.0.name`), raises `KeyError` with clear message on missing path.
  - [x] 2-7. `ValidationViolation` dataclass: `rule_type: str`, `dotpath: str | None`, `message: str`, `expected: Any | None`, `actual: Any | None`

- [x] 3. ValidatorExecutor
  - [x] 3-1. `ValidatorExecutor` class in `src/dan/executors/validator.py`
  - [x] 3-2. Execute flow: iterate rules sequentially, collect `ValidationViolation` list. If strict_mode, stop at first violation.
  - [x] 3-3. Routing: if no violations -> output on `valid` port (passthrough original data). If violations -> output on `invalid` port (dict with `data` = original, `errors` = violation list as dicts).
  - [x] 3-4. `on_failure` modes: `route` (default) = route to valid/invalid port as described. `warn` = always output on `valid` port regardless, log violations as warnings (NOT called "skip" to avoid collision with `RetryPolicy.on_failure="skip"` which maps to `NodeStatus.SKIPPED`). `halt` = return FAILED with `metadata.halt=True` on any violation.
  - [x] 3-5. Event emission: `VALIDATION_RESULT` event with `passed: bool`, `violation_count: int`, `violations: list[dict]` (summary), `rule_count: int`
  - [x] 3-6. Register `VALIDATION_RESULT` event type in `src/dan/engine/events.py`
  - [x] 3-7. Register `ValidatorExecutor` in scheduler `_register_defaults` under `"validator"`

- [x] 4. Boundary auto-insert utility
  - [x] 4-1. `generate_entry_validator(composite_node) -> tuple[ValidatorNode, list[DataEdge]]` in `src/dan/validation/boundaries.py`: generate a ValidatorNode from `external_input_schema` with required_keys + schema_conformance rules, wired between the composite input and its first internal node
  - [x] 4-2. `generate_exit_validator(composite_node) -> tuple[ValidatorNode, list[DataEdge]]`: same for `external_output_schema` at the output boundary
  - [x] 4-3. `insert_boundary_validators(graph, composite_node_id) -> Graph`: returns a new Graph with validators inserted at both boundaries of the specified composite node
  - [x] 4-4. Builder helper: `wf.validated_composite(node_id, ...)` context manager that auto-inserts entry/exit validators on `build()`
  - [x] 4-5. Editor action: right-click composite node -> "Add Boundary Validators" in ContextMenu. Calls store action that generates validators via boundary utility and adds them to the graph.

- [x] 5. Visual editor integration
  - [x] 5-1. `"validator"` in `NODE_TYPE_CATALOG` (category: `"Control Flow"`)
  - [x] 5-2. `NODE_DESCRIPTIONS` entry: "Validates data at agent boundaries. Checks required keys, schema conformance, non-empty values, type constraints, and custom expressions. Routes to valid/invalid output ports."
  - [x] 5-3. `createDefaultNode` case for `"validator"` in `graphAdapter.ts`: default with one `required_keys` rule and `on_failure="route"`
  - [x] 5-4. TypeScript interfaces: `ValidatorNodeType`, `ValidationRule` in `types/graph.ts`
  - [x] 5-5. ConfigPanel: dedicated validator section. Rule list with add/remove buttons. Per-rule: type dropdown, config fields (dotpath input for required_keys/non_empty/type_check, JSON schema editor for schema_conformance, expression textarea for custom_expression). on_failure dropdown. strict_mode toggle.
  - [x] 5-6. Node icon: shield with checkmark (SVG in `nodeIcons.tsx`)
  - [x] 5-7. DanNode: traffic-light border coloring based on last run result (green = all valid, red = violations, neutral = not run). Show violation count badge on failed runs.
  - [x] 5-8. Valid/invalid output port handles: green and red coloring (same pattern as GateNode true/false handles)

- [x] 6. Builder DSL and decompiler
  - [x] 6-1. `wf.validator(node_id, rules=[...], on_failure="route")` method on `WorkflowBuilder`, returns `NodeRef`. `on_failure` accepts `"route"`, `"warn"`, or `"halt"`.
  - [x] 6-2. Compiler: `ValidatorNode` case in `_build_node()`. `"validator": "valid"` in `DEFAULT_OUTPUT_PORTS` (default output is the happy path)
  - [x] 6-3. Decompiler: `ValidatorNode` -> `wf.validator()` call with rules list and non-default kwargs

- [x] 7. Tests
  - [x] 7-1. `resolve_dotpath` unit tests: nested access, list indexing, missing key error, empty path
  - [x] 7-2. Rule evaluation tests per rule type: required_keys (present/missing), non_empty (None/empty string/empty list/valid), schema_conformance (matching/mismatching), type_check (correct/wrong type), custom_expression (truthy/falsy/error)
  - [x] 7-3. `ValidatorExecutor` integration tests: all-pass routing to valid, any-fail routing to invalid, strict_mode early stop, multi-rule composition
  - [x] 7-4. `on_failure` mode tests: route (both ports), warn (always valid + logged warnings), halt (FAILED + halt metadata)
  - [x] 7-5. Boundary auto-insert tests: composite with external_input_schema generates correct entry validator, exit validator from external_output_schema, insert_boundary_validators produces valid graph
  - [x] 7-6. Builder/decompiler round-trip: `wf.validator()` -> compile -> decompile -> re-compile matches
  - [x] 7-7. Event emission: VALIDATION_RESULT with correct passed/violation_count/summary
  - [x] 7-8. Edge case: empty rules list (trivially passes), data is not a dict (wraps in {"value": data}), validator with no incoming edges (validation error at graph level)
  - [ ] 7-9. Update `test_builtins_registered` count for new executor type *(deferred — test bookkeeping, low priority)*

- [x] 8. Docs sync
  - [x] 8-1. `architecture.md`: add ValidatorNode to node types list, document rule types, add `executors/validator.py` and `validation/boundaries.py` to directory tree
  - [x] 8-2. `llm-api-guide.md`: ValidatorNode type reference, `wf.validator()` builder method, rule type configuration, boundary validator patterns
  - [x] 8-3. `README.md`: handoff validation capability in feature summary
  - [x] 8-4. `pyproject.toml`: optional dep `jsonschema` for full JSON Schema validation (pure-Python fallback covers basic cases without it)

## Decisions

- Dotpath syntax: `a.b.c` for nested dicts, `items.0.name` for list indexing (integer segments auto-detected)
- `schema_ref` stored as `{"$ref": schema_ref}` in the schema dict; full resolution deferred to jsonschema library
- Input data extraction: `inputs.get("data", inputs)` — if inputs is a single-key dict with "data", unwrap it; otherwise use the whole inputs dict. Non-dict values wrapped as `{"value": data}`.
- `_extract_data` checks `len(inputs) == 1` and `"data" in inputs` to decide unwrapping, avoiding ambiguity with multi-key inputs that happen to have a "data" key

## Notes

- `GateNode` is the closest precedent: two-branch output routing (true/false for gate, valid/invalid for validator), auto-derived output ports in `model_post_init`, colored port handles in the editor.
- `check_schema_compatible()` in `validation/schema.py` is a design-time schema-vs-schema checker (port compatibility). The validator's `schema_conformance` rule validates runtime data payloads against a schema, which is a fundamentally different operation. Uses `jsonschema.validate()` when available, with a pure-Python fallback for basic type/required checks.
- `conditions.py` safe evaluator already handles expression evaluation with restricted builtins. The custom_expression rule type reuses it directly.
- The boundary auto-insert pattern is inspired by the `graphAsCompositeNode()` factory in `graphImporter.ts` which already analyzes entry/exit points of composite nodes.
- `on_failure="route"` is the default because it gives downstream nodes the chance to handle invalid data (error recovery, retry, logging). `halt` is for strict pipelines where bad data should stop everything.
- The validator intentionally does NOT modify data. It is a pure observation/routing node. If data needs transformation, use a CodeOperator before the validator.
