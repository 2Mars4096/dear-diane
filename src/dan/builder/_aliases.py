"""Private builder alias helpers.

These keep the public WorkflowBuilder DSL stable while centralizing the
Worker/legacy projection logic used by the alias methods.
"""

from __future__ import annotations

from typing import Any

from dan.builder.compiler import BuildError, _PendingNode
from dan.models.legacy import (
    CodeOperator,
    HumanInTheLoopNode,
    HumanNode,
    InputNode,
    LLMOperator,
    RAGOperator,
    ReduceNode,
    ReflectionNode,
    RouterNode,
    ToolOperator,
    ValidatorNode,
    VoteNode,
)
from dan.worker.model import Worker
from dan.worker import presets as worker_presets


def legacy_compute_pending_from_worker(
    node_id: str,
    *,
    expected_node_type: str,
    worker_kwargs: dict[str, Any],
    explicit_input_ports: list[Any],
    explicit_output_ports: list[Any],
) -> _PendingNode:
    """Project a Worker-shaped alias payload back to a legacy pending node."""

    copied_input_ports = [port.model_copy(deep=True) for port in explicit_input_ports]
    copied_output_ports = [port.model_copy(deep=True) for port in explicit_output_ports]
    materialized_worker_kwargs = dict(worker_kwargs)
    metadata = dict(materialized_worker_kwargs.get("metadata") or {})
    if "tool_config" in materialized_worker_kwargs:
        metadata["tool_config"] = dict(materialized_worker_kwargs.pop("tool_config") or {})
    if metadata:
        materialized_worker_kwargs["metadata"] = metadata
    worker = Worker(
        id=node_id,
        name=str(materialized_worker_kwargs.get("name") or node_id),
        description=str(materialized_worker_kwargs.get("description") or ""),
        input_ports=[port.model_copy(deep=True) for port in copied_input_ports],
        output_ports=[port.model_copy(deep=True) for port in copied_output_ports],
        **{
            k: v
            for k, v in materialized_worker_kwargs.items()
            if k not in {"name", "description"}
        },
    )
    legacy = worker_presets.worker_to_legacy(worker)
    if legacy is None or legacy.node_type != expected_node_type:
        raise BuildError([
            f"Could not project Worker alias {node_id!r} to legacy node type {expected_node_type!r}",
        ])

    kwargs: dict[str, Any] = {
        "name": legacy.name,
        "description": legacy.description,
    }
    if isinstance(legacy, LLMOperator):
        kwargs.update({
            "model": legacy.model,
            "prompt_template": legacy.prompt_template,
            "system_prompt": legacy.system_prompt,
            "temperature": legacy.temperature,
        })
        if legacy.max_tokens is not None:
            kwargs["max_tokens"] = legacy.max_tokens
        if legacy.output_json_schema is not None:
            kwargs["output_json_schema"] = legacy.output_json_schema
    elif isinstance(legacy, ToolOperator):
        kwargs.update({
            "tool_id": legacy.tool_id,
            "tool_config": dict(legacy.tool_config),
        })
    elif isinstance(legacy, RAGOperator):
        kwargs.update({
            "collection": legacy.collection,
            "top_k": legacy.top_k,
            "query_template": legacy.query_template,
            "include_metadata": legacy.include_metadata,
            "rerank": legacy.rerank,
        })
        if legacy.similarity_threshold is not None:
            kwargs["similarity_threshold"] = legacy.similarity_threshold
        if legacy.embedding_model:
            kwargs["embedding_model"] = legacy.embedding_model
        if legacy.vector_store_config:
            kwargs["vector_store_config"] = dict(legacy.vector_store_config)
    elif isinstance(legacy, HumanNode):
        kwargs.update({
            "prompt": legacy.prompt,
            "render_mode": legacy.render_mode,
            "instructions": legacy.instructions,
            "render_target": legacy.render_target,
        })
        if legacy.timeout_seconds is not None:
            kwargs["timeout_seconds"] = legacy.timeout_seconds
        if legacy.default_action is not None:
            kwargs["default_action"] = legacy.default_action
        if legacy.input_schema is not None:
            kwargs["input_schema"] = legacy.input_schema
        if legacy.output_schema is not None:
            kwargs["output_schema"] = legacy.output_schema
        if legacy.options is not None:
            kwargs["options"] = list(legacy.options)
    elif isinstance(legacy, VoteNode):
        kwargs.update({
            "candidates": list(legacy.candidates),
            "num_votes": legacy.num_votes,
            "prompt_template": legacy.prompt_template,
            "system_prompt": legacy.system_prompt,
            "temperature": legacy.temperature,
            "vote_strategy": legacy.vote_strategy,
            "parallelism": legacy.parallelism,
        })
        if legacy.output_json_schema is not None:
            kwargs["output_json_schema"] = legacy.output_json_schema
        if legacy.vote_config is not None:
            kwargs["vote_config"] = legacy.vote_config
        if legacy.task_tier is not None:
            kwargs["task_tier"] = legacy.task_tier
        if legacy.timeout_seconds is not None:
            kwargs["timeout_seconds"] = legacy.timeout_seconds
    elif isinstance(legacy, InputNode):
        kwargs["variables"] = list(legacy.variables)
    elif isinstance(legacy, ReduceNode):
        kwargs["reducer"] = legacy.reducer
    elif isinstance(legacy, RouterNode):
        kwargs.update({
            "model": legacy.model,
            "route_descriptions": dict(legacy.route_descriptions),
        })
        if legacy.task_tier is not None:
            kwargs["task_tier"] = legacy.task_tier
    elif isinstance(legacy, ValidatorNode):
        kwargs.update({
            "validation_rules": list(legacy.validation_rules),
            "on_failure": legacy.on_failure,
            "strict_mode": legacy.strict_mode,
        })
    elif isinstance(legacy, ReflectionNode):
        kwargs.update({
            "reflection_prompt": legacy.reflection_prompt,
            "source": legacy.source,
            "source_config": dict(legacy.source_config),
            "output_format": legacy.output_format,
            "max_principles": legacy.max_principles,
            "min_confidence": legacy.min_confidence,
            "dedup_strategy": legacy.dedup_strategy,
        })
        if legacy.reflection_model is not None:
            kwargs["reflection_model"] = legacy.reflection_model
        if legacy.task_tier is not None:
            kwargs["task_tier"] = legacy.task_tier
    elif isinstance(legacy, CodeOperator):
        kwargs.update({
            "code": legacy.code,
            "language": legacy.language,
        })
        if legacy.read_set:
            kwargs["read_set"] = legacy.read_set
        if legacy.write_set:
            kwargs["write_set"] = legacy.write_set
    elif isinstance(legacy, HumanInTheLoopNode):
        kwargs.update({
            "prompt": legacy.prompt,
            "timeout_seconds": legacy.timeout_seconds,
            "default_action": legacy.default_action,
        })
    else:
        raise BuildError([
            f"Unsupported legacy projection type {type(legacy).__name__!r} for alias {node_id!r}",
        ])

    return _PendingNode(
        id=node_id,
        node_type=expected_node_type,
        kwargs=kwargs,
        explicit_input_ports=copied_input_ports,
        explicit_output_ports=copied_output_ports,
    )


def worker_pending_alias(
    node_id: str,
    *,
    worker_kwargs: dict[str, Any],
    explicit_input_ports: list[Any],
    explicit_output_ports: list[Any],
) -> _PendingNode:
    """Materialize a simple compute alias directly as a Worker pending node."""

    materialized_kwargs = dict(worker_kwargs)
    metadata = dict(materialized_kwargs.get("metadata") or {})
    if "tool_config" in materialized_kwargs:
        metadata["tool_config"] = dict(materialized_kwargs.pop("tool_config") or {})
    if metadata:
        materialized_kwargs["metadata"] = metadata
    return _PendingNode(
        id=node_id,
        node_type="worker",
        kwargs=materialized_kwargs,
        explicit_input_ports=[port.model_copy(deep=True) for port in explicit_input_ports],
        explicit_output_ports=[port.model_copy(deep=True) for port in explicit_output_ports],
    )
