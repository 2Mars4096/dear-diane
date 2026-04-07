"""Structured-generation helpers for workflow generation."""

from __future__ import annotations

import os
import tempfile
from typing import Any


def structured_generation_mode() -> str:
    mode = str(
        os.environ.get("DAN_STRUCTURED_GENERATION", "disabled") or "disabled"
    ).strip().lower()
    if mode not in {"disabled", "canary", "enabled"}:
        return "disabled"
    return mode


def structured_generation_max_dependencies() -> int:
    raw = str(
        os.environ.get("DAN_STRUCTURED_MAX_DEPENDENCIES_PER_NODE", "1") or "1"
    ).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def structured_execution_smoke_mode() -> str:
    raw = str(
        os.environ.get("DAN_STRUCTURED_EXECUTION_SMOKE", "auto") or "auto"
    ).strip().lower()
    if raw not in {"disabled", "auto", "required"}:
        return "auto"
    return raw


def structured_execution_smoke_timeout() -> float:
    raw = str(
        os.environ.get("DAN_STRUCTURED_EXECUTION_SMOKE_TIMEOUT", "20") or "20"
    ).strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 20.0


def infer_schedule_intent(text: str) -> Any | None:
    from dan.meta.workflow_spec import ScheduleIntent

    lower = str(text or "").lower()
    trigger: str | None = None
    if any(token in lower for token in ("daily", "every day", "each day")):
        trigger = "daily"
    elif any(
        token in lower
        for token in (
            "weekly",
            "every monday",
            "every tuesday",
            "every wednesday",
            "every thursday",
            "every friday",
            "every saturday",
            "every sunday",
        )
    ):
        trigger = "weekly"
    elif any(token in lower for token in ("monthly", "every month", "each month")):
        trigger = "monthly"
    if trigger is None:
        return None
    delivery_target = None
    if "email" in lower:
        delivery_target = "email"
    elif "slack" in lower:
        delivery_target = "slack"
    elif "notify" in lower:
        delivery_target = "notification"
    return ScheduleIntent(
        trigger=trigger,
        delivery_target=delivery_target,
        notes=["Inferred from the original workflow-authoring prompt."],
    )


def structured_generation_eligibility(
    spec: Any,
    *,
    max_dependencies: int | None = None,
) -> tuple[bool, str]:
    max_dependencies = (
        structured_generation_max_dependencies()
        if max_dependencies is None
        else max_dependencies
    )
    dependency_heavy_nodes = [
        node.node_id
        for node in getattr(spec, "nodes", []) or []
        if len(getattr(node, "dependencies", []) or []) > max_dependencies
    ]
    if dependency_heavy_nodes:
        return (
            False,
            "Structured generation skipped because some nodes exceed the configured "
            f"dependency cap ({max_dependencies}): {', '.join(dependency_heavy_nodes[:5])}",
        )
    unsupported_types = {
        node.node_id: node.node_type
        for node in getattr(spec, "nodes", []) or []
        if node.node_type
        not in {
            "llm_operator",
            "tool_operator",
            "code_operator",
            "worker",
            "gate",
            "for_each",
            "rag_operator",
            "human",
        }
    }
    if unsupported_types:
        preview = ", ".join(
            f"{node_id}:{node_type}"
            for node_id, node_type in list(unsupported_types.items())[:5]
        )
        return (
            False,
            "Structured generation skipped because the candidate intent contains "
            f"unsupported runtime node types for the staged linker: {preview}",
        )
    return True, ""


def structured_execution_smoke_inputs(
    spec: Any,
    candidate_graph: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str], str | None]:
    def _structured_executor_kind(node: dict[str, Any]) -> str:
        metadata = dict(node.get("metadata", {}) or {})
        structured = dict(metadata.get("structured_generation", {}) or {})
        explicit_kind = str(structured.get("executor_kind") or "").strip().lower()
        if explicit_kind:
            return explicit_kind

        node_type = str(node.get("node_type") or "").strip()
        if node_type == "tool_operator":
            return "tool"
        if node_type == "code_operator":
            return "code"
        if node_type == "llm_operator":
            return "llm"
        if node_type == "rag_operator":
            return "rag"
        if node_type == "human":
            return "human"
        if node_type != "worker":
            return ""

        if str(node.get("code") or "").strip():
            return "code"
        tool_ids = [
            str(item).strip()
            for item in (node.get("tool_ids") or [])
            if str(item).strip()
        ]
        if not tool_ids and str(node.get("tool_id") or "").strip():
            tool_ids = [str(node.get("tool_id") or "").strip()]
        if tool_ids:
            return "tool"
        if any(
            key in metadata
            for key in (
                "human_prompt",
                "human_timeout_seconds",
                "human_default_action",
                "human_input_schema",
                "human_output_schema",
                "human_render_mode",
                "human_options",
                "human_instructions",
                "human_render_target",
            )
        ):
            return "human"
        if node.get("model") is not None or node.get("llm_hints") is not None:
            return "llm"
        return ""

    def _iter_graph_nodes(graph_dict: dict[str, Any]) -> list[dict[str, Any]]:
        discovered: list[dict[str, Any]] = list(graph_dict.get("nodes", []) or [])
        for sub_graph in dict(graph_dict.get("sub_graphs", {}) or {}).values():
            if isinstance(sub_graph, dict):
                discovered.extend(_iter_graph_nodes(sub_graph))
        return discovered

    nodes = _iter_graph_nodes(candidate_graph)
    executor_kinds = {
        kind
        for kind in (_structured_executor_kind(node) for node in nodes)
        if kind
    }
    tool_ids = {
        str(node.get("tool_id") or "").strip()
        for node in nodes
        if _structured_executor_kind(node) == "tool"
        and str(node.get("tool_id") or "").strip()
    }
    tool_ids.update(
        str(item).strip()
        for node in nodes
        if _structured_executor_kind(node) == "tool"
        for item in (node.get("tool_ids") or [])
        if str(item).strip()
    )
    if "code" in executor_kinds:
        return (
            None,
            [],
            "Structured execution smoke skipped because the candidate contains generated code operators.",
        )
    if executor_kinds & {"llm", "human", "rag"}:
        return (
            None,
            [],
            "Structured execution smoke skipped because the candidate requires model-backed or human execution.",
        )
    unsafe_tools = {
        tool_id
        for tool_id in tool_ids
        if tool_id not in {"file_read", "csv_read"}
    }
    if unsafe_tools:
        preview = ", ".join(sorted(list(unsafe_tools))[:5])
        return (
            None,
            [],
            "Structured execution smoke skipped because the candidate uses external or side-effecting tools: "
            f"{preview}",
        )

    inputs: dict[str, Any] = {}
    cleanup_paths: list[str] = []
    for name in list(getattr(spec, "global_inputs", []) or []):
        lowered = str(name or "").strip().lower()
        if not lowered:
            continue
        if any(token in lowered for token in ("folder", "directory", "_dir")):
            return (
                None,
                cleanup_paths,
                f"Structured execution smoke skipped because global input {name!r} needs a directory fixture.",
            )
        if any(token in lowered for token in ("csv",)):
            fd, path = tempfile.mkstemp(
                prefix="dan-structured-smoke-",
                suffix=".csv",
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("name,value\nsample,1\n")
            inputs[name] = path
            cleanup_paths.append(path)
            continue
        if any(token in lowered for token in ("path", "file")):
            fd, path = tempfile.mkstemp(
                prefix="dan-structured-smoke-",
                suffix=".txt",
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("sample fixture content\n")
            inputs[name] = path
            cleanup_paths.append(path)
            continue
        if any(token in lowered for token in ("records", "rows", "items", "entries", "symbols", "watchlist")):
            inputs[name] = [{"value": "sample"}]
            continue
        if any(token in lowered for token in ("count", "limit", "max", "top")):
            inputs[name] = 1
            continue
        if any(token in lowered for token in ("enabled", "notify", "send")):
            inputs[name] = True
            continue
        inputs[name] = f"sample value for {name}"
    return inputs, cleanup_paths, None


def cleanup_structured_smoke_paths(paths: list[str]) -> None:
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


__all__ = [
    "cleanup_structured_smoke_paths",
    "infer_schedule_intent",
    "structured_execution_smoke_inputs",
    "structured_execution_smoke_mode",
    "structured_execution_smoke_timeout",
    "structured_generation_eligibility",
    "structured_generation_max_dependencies",
    "structured_generation_mode",
]
