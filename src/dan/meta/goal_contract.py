from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_STRING_KEYS = (
    "goal",
    "deliverable",
    "next_step",
    "route_target",
)

_LIST_KEYS = (
    "constraints",
    "required_action_hints",
    "completion_checks",
    "route_action_hints",
)


def _coerce_string(value: Any) -> str:
    return str(value or "").strip()


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def normalize_goal_contract(contract: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(contract, Mapping):
        return {}
    normalized: dict[str, Any] = {}
    for key in _STRING_KEYS:
        value = _coerce_string(contract.get(key))
        if value:
            normalized[key] = value
    for key in _LIST_KEYS:
        values = _coerce_string_list(contract.get(key))
        if values:
            normalized[key] = values
    return normalized


def build_goal_contract(
    *,
    deliberation: Any = None,
    route_target: str | None = None,
    route_action_hints: Any = None,
) -> dict[str, Any]:
    contract: dict[str, Any] = {}
    if deliberation is not None:
        for key in ("goal", "deliverable", "next_step"):
            value = _coerce_string(getattr(deliberation, key, ""))
            if value:
                contract[key] = value
        for key in ("constraints", "required_action_hints", "completion_checks"):
            values = _coerce_string_list(getattr(deliberation, key, []))
            if values:
                contract[key] = values
    if route_target:
        contract["route_target"] = _coerce_string(route_target)
    route_hints = _coerce_string_list(route_action_hints)
    if route_hints:
        contract["route_action_hints"] = route_hints
    return normalize_goal_contract(contract)


def render_goal_contract_section(
    contract: Mapping[str, Any] | None,
    *,
    heading: str = "## Goal Contract",
    preamble: str | None = None,
) -> str:
    normalized = normalize_goal_contract(contract)
    if not normalized:
        return ""

    lines: list[str] = [heading]
    if preamble:
        preamble_text = preamble.strip()
        if preamble_text:
            lines.append(preamble_text)

    goal = normalized.get("goal")
    if goal:
        lines.append(f"- Goal: {goal}")

    deliverable = normalized.get("deliverable")
    if deliverable:
        lines.append(f"- Deliverable: {deliverable}")

    constraints = normalized.get("constraints", [])
    if constraints:
        lines.append("- Constraints:")
        lines.extend(f"  - {item}" for item in constraints)

    required_actions = normalized.get("required_action_hints", [])
    if required_actions:
        lines.append(f"- Required actions: {', '.join(required_actions)}")

    completion_checks = normalized.get("completion_checks", [])
    if completion_checks:
        lines.append("- Completion checks:")
        lines.extend(f"  - {item}" for item in completion_checks)

    next_step = normalized.get("next_step")
    if next_step:
        lines.append(f"- Immediate next step: {next_step}")

    route_target = normalized.get("route_target")
    if route_target:
        lines.append(f"- Route target: {route_target}")

    route_hints = normalized.get("route_action_hints", [])
    if route_hints:
        lines.append(f"- Route action hints: {', '.join(route_hints)}")

    return "\n".join(lines)
