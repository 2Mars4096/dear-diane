"""Load and validate golden intent fixtures for the generation quality suite."""

from __future__ import annotations

import json
from pathlib import Path

_DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "golden_intents"

_VARIANT_ORDER = {"simple": 0, "standard": 1, "complex": 2}


def _sort_key(fixture: dict) -> tuple[str, int]:
    return (fixture.get("family", ""), _VARIANT_ORDER.get(fixture.get("variant", ""), 99))


def _validate_against_schema(fixture: dict, schema: dict) -> None:
    """Validate a fixture dict against the JSON schema.

    Uses ``jsonschema`` when available; falls back to a lightweight
    structural check that covers the required/type constraints used
    by the golden-intent schema.
    """
    try:
        import jsonschema

        jsonschema.validate(instance=fixture, schema=schema)
    except ImportError:
        _validate_fallback(fixture, schema)


def _validate_fallback(fixture: dict, schema: dict) -> None:
    """Minimal validation without the jsonschema package."""
    required = schema.get("required", [])
    for field in required:
        if field not in fixture:
            raise ValueError(f"Missing required field: {field!r}")

    props = schema.get("properties", {})

    if "family" in fixture:
        allowed = props.get("family", {}).get("enum", [])
        if allowed and fixture["family"] not in allowed:
            raise ValueError(
                f"Invalid family {fixture['family']!r}; expected one of {allowed}"
            )

    if "variant" in fixture:
        allowed = props.get("variant", {}).get("enum", [])
        if allowed and fixture["variant"] not in allowed:
            raise ValueError(
                f"Invalid variant {fixture['variant']!r}; expected one of {allowed}"
            )

    expected = fixture.get("expected", {})
    if not isinstance(expected, dict):
        raise ValueError("'expected' must be a dict")

    expected_props = props.get("expected", {}).get("properties", {})
    for req_field in props.get("expected", {}).get("required", []):
        if req_field not in expected:
            raise ValueError(f"Missing required field in 'expected': {req_field!r}")

    min_nodes = expected.get("min_nodes")
    if min_nodes is not None:
        min_spec = expected_props.get("min_nodes", {})
        if not isinstance(min_nodes, int):
            raise ValueError(f"'min_nodes' must be an integer, got {type(min_nodes).__name__}")
        if min_nodes < min_spec.get("minimum", 1):
            raise ValueError(f"'min_nodes' must be >= {min_spec.get('minimum', 1)}")


def load_golden_intents(fixtures_dir: str | None = None) -> list[dict]:
    """Load all golden intent fixtures, validate against schema, return list.

    Parameters
    ----------
    fixtures_dir:
        Path to the directory containing the JSON fixtures and ``schema.json``.
        Defaults to ``tests/fixtures/golden_intents/``.

    Returns
    -------
    list[dict]
        Fixtures sorted by ``(family, variant)``.
    """
    root = Path(fixtures_dir) if fixtures_dir else _DEFAULT_FIXTURES_DIR

    schema_path = root / "schema.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    with open(schema_path) as f:
        schema = json.load(f)

    fixtures: list[dict] = []
    for fp in sorted(root.iterdir()):
        if fp.suffix != ".json" or fp.name == "schema.json":
            continue
        with open(fp) as f:
            data = json.load(f)
        _validate_against_schema(data, schema)
        data["_source_file"] = fp.name
        fixtures.append(data)

    fixtures.sort(key=_sort_key)
    return fixtures
