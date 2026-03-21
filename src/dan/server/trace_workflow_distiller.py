"""Distill audited chat tool traces into reusable workflow drafts.

The initial scope is intentionally narrow:
- source of truth is a persisted ``ChatAuditRecord``
- output is a validated ``WorkflowIntent`` draft plus lightweight metadata
- abstraction stays heuristic and bounded; ambiguous semantics are preserved
  in stage descriptions instead of over-fitting exact runtime arguments
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from dan.meta.intent_schema import StageIntent, StageType, WorkflowIntent


class TraceWorkflowParameter(BaseModel):
    """Generalized input parameter inferred from one or more tool arguments."""

    name: str
    placeholder: str
    source_keys: list[str] = Field(default_factory=list)
    example: str = ""
    inferred_type: str = "string"


class TraceWorkflowAction(BaseModel):
    """Normalized action distilled from an audited tool call."""

    raw_tool_name: str
    generalized_tool_name: str
    stage_type: StageType
    description: str
    generalized_args: dict[str, Any] = Field(default_factory=dict)
    parameter_refs: list[str] = Field(default_factory=list)
    action_kind: str = "tool"


class TraceWorkflowDraft(BaseModel):
    """A reusable workflow draft abstracted from one audited chat turn."""

    source_turn_id: str = ""
    source_surface_id: str = ""
    source_workflow_id: str = ""
    source_run_id: str = ""
    generalized_goal: str = ""
    summary: str = ""
    parameters: list[TraceWorkflowParameter] = Field(default_factory=list)
    action_trace: list[TraceWorkflowAction] = Field(default_factory=list)
    workflow_intent: WorkflowIntent


_SKIP_TOOLS = {
    "emit_workflow_intent",
    "plan_graph_mutations",
    "plan_workflow_mutation",
    "get_run_logs",
    "get_run_checkpoints",
    "start_run",
    "resume_run",
    "cancel_run",
}

_ParamRef = tuple[str, str, str]


def distill_workflow_trace_from_audit(record: Any) -> TraceWorkflowDraft | None:
    """Build a reusable workflow draft from a persisted chat audit record."""
    tool_calls = list(getattr(record, "tool_calls", []) or [])
    if not tool_calls:
        return None

    registry: dict[str, TraceWorkflowParameter] = {}
    actions: list[TraceWorkflowAction] = []
    for raw_call in tool_calls:
        action = _normalize_tool_call(raw_call, registry)
        if action is not None:
            actions.append(action)

    actions = _collapse_redundant_actions(actions)
    if len(actions) < 2:
        return None

    stages = _build_stage_sequence(
        user_goal=str(getattr(record, "user_message", "") or "").strip(),
        actions=actions,
    )
    if not stages:
        return None

    global_inputs = [param.name for param in registry.values()]
    intent = WorkflowIntent(
        goal=_generalize_goal(str(getattr(record, "user_message", "") or "").strip()),
        stages=stages,
        global_inputs=global_inputs,
        global_outputs=[stages[-1].outputs[0]] if stages[-1].outputs else [],
    )
    return TraceWorkflowDraft(
        source_turn_id=str(getattr(record, "turn_id", "") or ""),
        source_surface_id=str(getattr(record, "surface_id", "") or ""),
        source_workflow_id=str(getattr(record, "workflow_id", "") or ""),
        source_run_id=str(getattr(record, "run_id", "") or ""),
        generalized_goal=intent.goal,
        summary=(
            f"Distilled {len(actions)} audited actions into "
            f"{len(intent.stages)} reusable stages."
        ),
        parameters=list(registry.values()),
        action_trace=actions,
        workflow_intent=intent,
    )


def _normalize_tool_call(
    raw_call: Any,
    registry: dict[str, TraceWorkflowParameter],
) -> TraceWorkflowAction | None:
    raw_name = str(getattr(raw_call, "tool_name", "") or "").strip()
    if not raw_name:
        raw_name = str(raw_call.get("tool_name", "") or "").strip()
    if not raw_name:
        return None

    normalized_name = _normalize_tool_name(raw_name)
    if normalized_name in _SKIP_TOOLS:
        return None

    args = getattr(raw_call, "args", None)
    if args is None and isinstance(raw_call, dict):
        args = raw_call.get("args")
    args = args if isinstance(args, dict) else {}

    generalized_args, param_refs = _generalize_args(
        args,
        registry,
        tool_name=normalized_name,
    )
    stage_type = _stage_type_for_tool(normalized_name)
    action_kind = _action_kind_for_tool(normalized_name)
    description = _describe_action(normalized_name, generalized_args)

    return TraceWorkflowAction(
        raw_tool_name=raw_name,
        generalized_tool_name=normalized_name,
        stage_type=stage_type,
        description=description,
        generalized_args=generalized_args,
        parameter_refs=param_refs,
        action_kind=action_kind,
    )


def _normalize_tool_name(name: str) -> str:
    lowered = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")
    alias_map = {
        "read_file": "file_read",
        "write_file": "file_write",
        "save_file": "file_write",
        "save_markdown": "file_write",
        "search_web": "web_search",
        "web_lookup": "web_search",
        "run_python": "code_execution",
        "python": "code_execution",
    }
    return alias_map.get(lowered, lowered)


def _stage_type_for_tool(tool_name: str) -> StageType:
    if tool_name in {"code_execution"} or "python" in tool_name or "code" in tool_name:
        return StageType.code_execution
    return StageType.tool_call


def _action_kind_for_tool(tool_name: str) -> str:
    if "search" in tool_name or "web" in tool_name or "browse" in tool_name:
        return "research"
    if "read" in tool_name or "list" in tool_name:
        return "read"
    if "write" in tool_name or "save" in tool_name:
        return "write"
    if tool_name == "code_execution":
        return "compute"
    return "tool"


def _generalize_args(
    args: dict[str, Any],
    registry: dict[str, TraceWorkflowParameter],
    *,
    tool_name: str,
) -> tuple[dict[str, Any], list[str]]:
    generalized: dict[str, Any] = {}
    param_refs: list[str] = []
    for key, value in args.items():
        rewritten, inferred_refs = _generalize_value(
            key,
            value,
            key_path=key,
            tool_name=tool_name,
        )
        generalized[key] = rewritten
        for param_name, source_key, inferred_type in inferred_refs:
            if param_name not in registry:
                registry[param_name] = TraceWorkflowParameter(
                    name=param_name,
                    placeholder=f"{{{param_name}}}",
                    source_keys=[source_key],
                    example=_coerce_example(args, source_key),
                    inferred_type=inferred_type,
                )
            elif source_key not in registry[param_name].source_keys:
                registry[param_name].source_keys.append(source_key)
            if param_name not in param_refs:
                param_refs.append(param_name)
    return generalized, param_refs


def _coerce_example(args: dict[str, Any], source_key: str) -> str:
    current: Any = args
    for segment in source_key.split("."):
        if isinstance(current, dict):
            current = current.get(segment)
            continue
        if isinstance(current, list) and segment.isdigit():
            index = int(segment)
            if 0 <= index < len(current):
                current = current[index]
                continue
        return ""
    return str(current) if current is not None else ""


def _generalize_value(
    key: str,
    value: Any,
    *,
    key_path: str,
    tool_name: str,
) -> tuple[Any, list[_ParamRef]]:
    if isinstance(value, dict):
        nested: dict[str, Any] = {}
        refs: list[_ParamRef] = []
        for child_key, child_value in value.items():
            child_path = f"{key_path}.{child_key}"
            rewritten, child_refs = _generalize_value(
                child_key,
                child_value,
                key_path=child_path,
                tool_name=tool_name,
            )
            nested[child_key] = rewritten
            refs.extend(child_refs)
        return nested, refs
    if isinstance(value, list):
        items = []
        refs: list[_ParamRef] = []
        for index, item in enumerate(value):
            list_path = f"{key_path}.{index}"
            rewritten, child_refs = _generalize_value(
                key,
                item,
                key_path=list_path,
                tool_name=tool_name,
            )
            items.append(rewritten)
            refs.extend(child_refs)
        return items, refs

    text = str(value).strip()
    key_slug = re.sub(r"[^a-z0-9_]+", "_", key_path.lower()).strip("_") or "value"

    if not text:
        return value, []
    if text.startswith("http://") or text.startswith("https://") or "url" in key_slug:
        return "{url}", [("url", key_path, "url")]
    if _looks_like_path(text) or any(token in key_slug for token in ("path", "file", "dir", "folder")):
        if tool_name == "file_write":
            param = "output_path"
        elif tool_name == "file_read":
            param = "input_path"
        else:
            param = "output_path" if "output" in key_slug or "write" in key_slug else "input_path"
        if "dir" in key_slug or "folder" in key_slug:
            param = "directory_path"
        inferred_type = "directory" if param == "directory_path" else "path"
        return f"{{{param}}}", [(param, key_path, inferred_type)]
    if any(token in key_slug for token in ("query", "search", "topic", "goal", "pattern")):
        return "{query}", [("query", key_path, "string")]
    if any(token in key_slug for token in ("content", "text", "prompt", "message")) and len(text) > 24:
        return "{content}", [("content", key_path, "text")]
    if isinstance(value, (int, float)) or text.isdigit():
        return f"{{{key_slug}}}", [(key_slug, key_path, "number")]
    if len(text) > 80:
        return f"{{{key_slug}}}", [(key_slug, key_path, "text")]
    return text, []


def _looks_like_path(text: str) -> bool:
    return (
        text.startswith("/")
        or text.startswith("./")
        or text.startswith("../")
        or bool(re.search(r"\.[a-z0-9]{1,8}$", text.lower()))
        or ("\\" in text and ":" in text)
    )


def _describe_action(tool_name: str, generalized_args: dict[str, Any]) -> str:
    if tool_name == "web_search":
        query = generalized_args.get("query") or generalized_args.get("q") or "{query}"
        return f"Search the web for {query}."
    if tool_name == "file_read":
        path = generalized_args.get("path") or generalized_args.get("file_path") or "{input_path}"
        return f"Read input from {path}."
    if tool_name == "file_write":
        path = generalized_args.get("path") or generalized_args.get("file_path") or "{output_path}"
        return f"Write the final artifact to {path}."
    if tool_name == "code_execution":
        return "Transform the gathered inputs with code."
    arg_bits = []
    for key, value in generalized_args.items():
        arg_bits.append(f"{key}={value}")
    suffix = f" using {', '.join(arg_bits[:3])}" if arg_bits else ""
    return f"Run tool '{tool_name}'{suffix}."


def _collapse_redundant_actions(
    actions: list[TraceWorkflowAction],
) -> list[TraceWorkflowAction]:
    collapsed: list[TraceWorkflowAction] = []
    for action in actions:
        if collapsed:
            prev = collapsed[-1]
            if (
                prev.generalized_tool_name == action.generalized_tool_name
                and prev.generalized_args == action.generalized_args
            ):
                continue
        collapsed.append(action)
    return collapsed


def _build_stage_sequence(
    *,
    user_goal: str,
    actions: list[TraceWorkflowAction],
) -> list[StageIntent]:
    stages: list[StageIntent] = []
    prev_output: str | None = None
    stage_names: set[str] = set()
    has_transform_like = any(
        action.stage_type in {StageType.code_execution, StageType.transform}
        for action in actions
    )

    for idx, action in enumerate(actions):
        if action.action_kind == "write" and prev_output and not has_transform_like and idx > 0:
            synth_name = _unique_stage_name("prepare_output", stage_names)
            synth_output = f"{synth_name}_output"
            stages.append(
                StageIntent(
                    name=synth_name,
                    description=(
                        f"Synthesize the requested deliverable for: "
                        f"{_generalize_goal(user_goal)}"
                    ),
                    stage_type=StageType.transform,
                    inputs=[prev_output],
                    outputs=[synth_output],
                )
            )
            prev_output = synth_output
            has_transform_like = True

        stage_name = _unique_stage_name(
            _preferred_stage_name(action.generalized_tool_name),
            stage_names,
        )
        output_name = "final_output" if idx == len(actions) - 1 else f"{stage_name}_output"
        inputs: list[str] = []
        if prev_output:
            inputs.append(prev_output)
        for ref in action.parameter_refs:
            if ref not in inputs:
                inputs.append(ref)
        config: dict[str, Any] = {}
        if action.stage_type == StageType.tool_call:
            config["tool_id"] = action.generalized_tool_name
            if action.generalized_args:
                config["generalized_args"] = action.generalized_args
        elif action.stage_type == StageType.code_execution:
            if action.generalized_args:
                config["generalized_args"] = action.generalized_args
        stages.append(
            StageIntent(
                name=stage_name,
                description=action.description,
                stage_type=action.stage_type,
                inputs=inputs,
                outputs=[output_name],
                config=config,
            )
        )
        prev_output = output_name

    return stages


def _preferred_stage_name(tool_name: str) -> str:
    explicit = {
        "web_search": "research",
        "file_read": "read_input",
        "file_write": "write_output",
        "code_execution": "transform_data",
    }
    return explicit.get(tool_name, tool_name)


def _unique_stage_name(base: str, seen: set[str]) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", base.lower()).strip("_") or "stage"
    candidate = slug
    suffix = 2
    while candidate in seen:
        candidate = f"{slug}_{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


def _generalize_goal(goal: str) -> str:
    text = goal.strip()
    if not text:
        return "Reusable workflow distilled from audited task execution"
    text = re.sub(r"https?://\S+", "{url}", text)
    text = re.sub(r"(?<!\w)/(?:[^\s/]+/)*[^\s]+", "{path}", text)
    text = re.sub(r"\s+", " ", text)
    return text[:220]
