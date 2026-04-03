# 47-2: Structural Rules

**Parent:** [47-agent-output-linter](47-agent-output-linter.md)
**Status:** completed
**Goal:** Implement Tier 1 structural lint rules — deterministic, zero-cost, catches ~30% of handoff failures.

## Tasks

- [x] 1. Implement `SchemaConformanceRule` in `src/dan/linter/rules/structural.py`
  - [x] 1-1. Validate data against a JSON Schema using `jsonschema` (already a project dependency)
  - [x] 1-2. Report each validation error as a separate `LintDiagnostic` with the JSON path
  - [x] 1-3. Auto-fix: when a required field is missing and has a `default` in the schema, fill it
- [x] 2. Implement `RequiredFieldsRule` (landed as `required_keys`)
  - [x] 2-1. Check that all fields in `required_fields` list exist and are not `None`
  - [x] 2-2. Auto-fix: fill with type-appropriate defaults (`""` for str, `0` for int, `[]` for list, `{}` for dict)
- [x] 3. Implement `NonEmptyRule` (landed as `non_empty_keys`)
  - [x] 3-1. Check that fields in `non_empty` list are not empty strings, empty lists, empty dicts
  - [x] 3-2. No auto-fix — empty content cannot be meaningfully generated without context
- [x] 4. Implement `RangeRule`
  - [x] 4-1. Check that numeric fields in `ranges` dict fall within `(min, max)` bounds
  - [x] 4-2. Auto-fix: clamp to nearest bound
- [x] 5. Implement `TypeCheckRule` behavior (subsumed by `SchemaConformanceRule` + `coerce`)
  - [x] 5-1. Check that each field matches its expected Python type (str, int, float, list, dict, bool)
  - [x] 5-2. Auto-fix: attempt safe coercion (str→int, str→float, int→float, list→str via join)
- [x] 6. Implement `FormatPatternRule`
  - [x] 6-1. Check that string fields match regex patterns in `format_patterns` dict
  - [x] 6-2. No auto-fix — regex-compliant content cannot be generated without context
- [x] 7. Implement `MaxLengthRule`
  - [x] 7-1. Check string fields against `max_lengths` dict (character count)
  - [x] 7-2. Auto-fix: truncate to max length, preserving word boundaries where possible
- [x] 8. Register all Tier 1 rules in `structural.py` as `STRUCTURAL_RULES` list
- [x] 9. Tests
  - [x] 9-1. `tests/test_linter/test_structural.py` — each rule in isolation (pass, fail, auto-fix)
  - [x] 9-2. Full Tier 1 lint pass on realistic agent output payloads
  - [x] 9-3. Auto-fix idempotency: fixing then re-linting must produce `passed=True`
  - [x] 9-4. Performance: Tier 1 lint on a 10KB payload completes in < 1ms

## Rule Inventory

| Rule | Config Source | Severity | Auto-Fix |
|------|-------------|----------|----------|
| SchemaConformanceRule | `structural.schema` | error | fill defaults from schema |
| RequiredFieldsRule | `structural.required_fields` | error | fill type defaults |
| NonEmptyRule | `structural.non_empty` | error | none |
| RangeRule | `structural.ranges` | error | clamp to bounds |
| TypeCheckRule | `structural.type_checks` | error | safe coercion |
| FormatPatternRule | `structural.format_patterns` | warning | none |
| MaxLengthRule | `structural.max_lengths` | warning | truncate |

## Relationship to Existing Validation

DAN already has `ValidatorNode` with five rule types: `required_keys`, `non_empty`, `schema_conformance`, `type_check`, `custom_expression`. The structural lint rules overlap with these intentionally:

- **ValidatorNode** is a visible graph node that a user explicitly places in the workflow. It runs at a specific point in the graph.
- **Structural lint** is invisible middleware at every edge. It runs automatically between every producer and consumer.

The structural lint rules share the same validation logic where possible (both use `jsonschema` for schema validation, both check required fields the same way). But they are separate code — the linter does not import from `dan.models.control_flow` or the validator executor.

## Decisions

- The landed structural layer treats `required_keys`, `non_empty_keys`, and schema type mismatches plus safe `coerce` as the practical equivalents of standalone `RequiredFieldsRule`, `NonEmptyRule`, and `TypeCheckRule`. That keeps the runtime small without losing the actual behavior we need.
- `STRUCTURAL_RULES` is now an explicit registry of the landed Tier 1 rule inventory, even though execution still runs through one deterministic validator function instead of instantiating one class per rule.

## Notes

- The auto-fix for `SchemaConformanceRule` is conservative: only fills fields that have explicit `default` values in the JSON Schema. Missing fields without defaults remain as errors.
- `MaxLengthRule` truncation preserves word boundaries by finding the last space before the limit, avoiding mid-word cuts.
- All structural rules are deterministic: same input, same result, every time. No randomness, no model calls.
- `tests/test_linter/test_structural.py` now covers the explicit rule registry, per-field path reporting, and relint stability after composed deterministic fixes.
- The structural suite now also includes a realistic agent payload regression and an explicit latency gate. `tests/eval/structural_lint_benchmark.py` measures Tier 1 lint on a representative structured payload and currently passes the strict `< 1ms` median gate with the report saved at `tests/eval/results/20260403T070153Z_structural_lint_benchmark.json`.
