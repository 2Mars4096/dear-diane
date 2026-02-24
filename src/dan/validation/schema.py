"""Port schema compatibility checking.

MVP policy (locked in Phase 0):
  - Source output must contain all *required* fields of the target input
    with matching primitive types.
  - Extra optional fields in the source are allowed.
  - Top-level type must match.
  - Array items are checked recursively.
"""

from __future__ import annotations

from typing import Any


def check_schema_compatible(
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    path: str = "$",
) -> list[str]:
    """Return a list of human-readable incompatibility errors.

    An empty list means the schemas are compatible under the MVP policy.
    """
    errors: list[str] = []

    src_type = source.get("type")
    tgt_type = target.get("type")

    if tgt_type and src_type != tgt_type:
        errors.append(f"{path}: type mismatch — source is '{src_type}', target expects '{tgt_type}'")
        return errors

    if tgt_type == "object":
        errors.extend(_check_object(source, target, path))
    elif tgt_type == "array":
        errors.extend(_check_array(source, target, path))

    return errors


def _check_object(
    source: dict[str, Any],
    target: dict[str, Any],
    path: str,
) -> list[str]:
    errors: list[str] = []
    tgt_props = target.get("properties", {})
    src_props = source.get("properties", {})
    tgt_required = set(target.get("required", []))

    for field in tgt_required:
        field_path = f"{path}.{field}"
        if field not in src_props:
            errors.append(f"{field_path}: required by target but missing from source")
            continue
        if field in tgt_props:
            errors.extend(
                check_schema_compatible(src_props[field], tgt_props[field], path=field_path)
            )

    for field in set(tgt_props) - tgt_required:
        if field in src_props and field in tgt_props:
            field_path = f"{path}.{field}"
            errors.extend(
                check_schema_compatible(src_props[field], tgt_props[field], path=field_path)
            )

    return errors


def _check_array(
    source: dict[str, Any],
    target: dict[str, Any],
    path: str,
) -> list[str]:
    tgt_items = target.get("items")
    src_items = source.get("items")
    if tgt_items and src_items:
        return check_schema_compatible(src_items, tgt_items, path=f"{path}[]")
    if tgt_items and not src_items:
        return [f"{path}[]: target specifies item schema but source does not"]
    return []
