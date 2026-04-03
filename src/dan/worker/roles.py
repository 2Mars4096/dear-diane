"""Role helpers for Worker presets."""

from __future__ import annotations

from typing import Any

from dan.worker.model import ControlFlowConfig, Worker, WorkerAuthority


def _pop_llm_hints_alias(kwargs: dict[str, Any]) -> dict[str, Any]:
    llm_alias = kwargs.pop("llm", None)
    llm_hints = kwargs.pop("llm_hints", None)
    if llm_alias is not None and llm_hints is not None:
        raise ValueError("role helpers accept either `llm` or `llm_hints`, not both")
    effective = llm_hints if llm_hints is not None else llm_alias
    if effective is None:
        return kwargs
    kwargs["llm_hints"] = effective
    return kwargs


def role(name: str, /, **kwargs: Any) -> Worker:
    """Create a Worker role instance with sensible defaults."""
    worker_id = kwargs.pop("id", name)
    worker_name = kwargs.pop("name", name)
    worker_role = kwargs.pop("role", name)
    return Worker(id=worker_id, name=worker_name, role=worker_role, **kwargs)


def llm_agent(worker_id: str, *, model: str, persona: str = "", **kwargs: Any) -> Worker:
    """Convenience Worker for simple LLM-backed agents."""
    kwargs = _pop_llm_hints_alias(kwargs)
    return role(
        "llm_agent",
        id=worker_id,
        model=model,
        persona=persona,
        **kwargs,
    )


def tool_runner(worker_id: str, *, tool_ids: list[str], **kwargs: Any) -> Worker:
    """Convenience Worker for direct tool execution."""
    return role(
        "tool_runner",
        id=worker_id,
        tool_ids=tool_ids,
        **kwargs,
    )


def script(worker_id: str, *, code: str, language: str = "python", **kwargs: Any) -> Worker:
    """Convenience Worker for deterministic code execution."""
    return role(
        "script",
        id=worker_id,
        code=code,
        language=language,
        **kwargs,
    )


def reviewer(worker_id: str, *, model: str, persona: str = "Review carefully.", **kwargs: Any) -> Worker:
    """Convenience Worker for review/evaluation style LLM steps."""
    kwargs = _pop_llm_hints_alias(kwargs)
    kwargs.setdefault("instruction", "Review the provided work carefully.")
    return role(
        "reviewer",
        id=worker_id,
        model=model,
        persona=persona,
        **kwargs,
    )


def manager(
    worker_id: str,
    *,
    sub_workers: dict[str, str],
    model: str | None = None,
    **kwargs: Any,
) -> Worker:
    """Convenience Worker for coordinating named sub-workers."""
    kwargs = _pop_llm_hints_alias(kwargs)
    kwargs.setdefault("authority", WorkerAuthority.LEAD)
    kwargs.setdefault("instruction", "Coordinate declared sub-workers and merge their outputs.")
    return role(
        "manager",
        id=worker_id,
        model=model,
        sub_workers=sub_workers,
        **kwargs,
    )


def router(worker_id: str, *, model: str, **kwargs: Any) -> Worker:
    """Convenience Worker for route-selection style LLM steps."""
    kwargs = _pop_llm_hints_alias(kwargs)
    kwargs.setdefault("output_ports", [{"name": "route"}, {"name": "result"}])
    return role(
        "router",
        id=worker_id,
        model=model,
        **kwargs,
    )


def gate(
    worker_id: str,
    *,
    condition: str,
    gate_mode: str = "if_else",
    max_iterations: int = 10,
    **kwargs: Any,
) -> Worker:
    """Convenience Worker for gate-style control-flow delegation."""
    if "control_flow" in kwargs:
        raise ValueError("gate() does not accept an explicit `control_flow` override")
    return role(
        "gate",
        id=worker_id,
        control_flow=ControlFlowConfig(
            condition=condition,
            gate_mode=gate_mode,
            max_iterations=max_iterations,
        ),
        **kwargs,
    )


def validator(
    worker_id: str,
    *,
    rules: list[dict[str, Any]],
    on_failure: str = "route",
    strict_mode: bool = False,
    **kwargs: Any,
) -> Worker:
    """Convenience Worker for validation boundaries."""
    metadata = dict(kwargs.pop("metadata", {}) or {})
    metadata.setdefault("validation_rules", list(rules))
    metadata.setdefault("validator_on_failure", on_failure)
    metadata.setdefault("validator_strict_mode", strict_mode)
    kwargs.setdefault("input_ports", [{"name": "data", "required": False}])
    kwargs.setdefault("output_ports", [{"name": "valid"}, {"name": "invalid"}])
    return role(
        "validator",
        id=worker_id,
        metadata=metadata,
        **kwargs,
    )


def reducer(
    worker_id: str,
    *,
    expression: str,
    **kwargs: Any,
) -> Worker:
    """Convenience Worker for reduction/aggregation expressions."""
    metadata = dict(kwargs.pop("metadata", {}) or {})
    metadata.setdefault("reduce_expression", expression)
    return role(
        "reduce",
        id=worker_id,
        metadata=metadata,
        **kwargs,
    )


def observer(worker_id: str, **kwargs: Any) -> Worker:
    """Convenience Worker for human-observer or oversight-style roles."""
    tool_ids = list(kwargs.pop("tool_ids", []))
    if "human_input" not in tool_ids:
        tool_ids.append("human_input")
    kwargs.setdefault("instruction", "Observe the workflow and collect human feedback when needed.")
    return role(
        "observer",
        id=worker_id,
        tool_ids=tool_ids,
        **kwargs,
    )
