"""Compatibility bridges between legacy compute nodes and Worker."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from dan.models.control_flow import GateNode
from dan.models.legacy import (
    CodeOperator,
    HumanInTheLoopNode,
    HumanNode,
    InputNode,
    InputVariable,
    LLMOperator,
    RAGOperator,
    ReduceNode,
    ReflectionNode,
    RouterNode,
    ToolOperator,
    ValidationRule,
    ValidatorNode,
    VoteNode,
)
from dan.models.nodes import NodeBase
from dan.models.ports import InputPort, OutputPort
from dan.worker.model import LLMHints, Worker, llm_hints_configured

if TYPE_CHECKING:
    from dan.models.graph import Graph


# Legacy runtime node types that the current Worker bridge can materialize as a
# real Worker contract today.
BRIDGED_LEGACY_NODE_TYPES: frozenset[str] = frozenset({
    "llm_operator",
    "tool_operator",
    "code_operator",
    "input",
    "router",
    "validator",
    "reflection",
    "rag_operator",
    "human",
    "human_in_the_loop",
    "vote",
    "reduce",
})

# Runtime node types that intentionally do not have a direct `legacy_to_worker`
# bridge yet. Some are retained control/runtime primitives; others are older
# compute-like shapes that still execute honestly through their legacy path
# until a concrete Worker-native bridge exists.
EXPLICIT_NON_BRIDGED_LEGACY_NODE_TYPES: frozenset[str] = frozenset({
    "if_else",
    "gate",
    "while_loop",
    "for_each",
    "parallel_subagents",
    "orchestrator",
    "composite",
    "agent_team",
    "goal_loop",
})


def supports_legacy_conversion(node_type: str) -> bool:
    """Whether `legacy_to_worker()` has a first-class bridge for this node type."""

    return node_type in BRIDGED_LEGACY_NODE_TYPES


def legacy_to_worker(node: NodeBase) -> Worker | None:
    """Convert a legacy compute node into an equivalent Worker when possible."""
    common: dict[str, Any] = {
        "id": node.id,
        "name": node.name,
        "description": node.description,
        "input_ports": [InputPort.model_validate(p.model_dump()) for p in node.input_ports],
        "output_ports": [OutputPort.model_validate(p.model_dump()) for p in node.output_ports],
        "position": node.position,
        "ui": dict(node.ui),
        "metadata": dict(node.metadata),
        "tags": list(node.tags),
        "retry_policy": node.retry_policy,
        "read_set": list(node.read_set),
        "write_set": list(node.write_set),
        "memoize": node.memoize,
        "cache_ttl": node.cache_ttl,
    }
    if isinstance(node, LLMOperator):
        return Worker(
            **common,
            model=node.model,
            llm_hints=LLMHints(
                prompt_template=node.prompt_template,
                system_prompt=node.system_prompt,
                temperature=node.temperature,
                max_tokens=node.max_tokens,
                output_json_schema=node.output_json_schema,
                tools=list(node.tools),
                max_tool_rounds=node.max_tool_rounds,
                task_tier=node.task_tier,
                history_policy=node.history_policy,
            ),
        )
    if isinstance(node, ToolOperator):
        metadata = dict(common["metadata"])
        metadata.setdefault("tool_config", dict(node.tool_config))
        return Worker(**{**common, "metadata": metadata}, tool_ids=[node.tool_id])
    if isinstance(node, CodeOperator):
        return Worker(**common, code=node.code, language=node.language)
    if isinstance(node, InputNode):
        metadata = dict(common["metadata"])
        metadata.setdefault(
            "input_variables",
            [InputVariable.model_validate(v.model_dump(mode="json")).model_dump(mode="json") for v in node.variables],
        )
        return Worker(**{**common, "metadata": metadata})
    if isinstance(node, RouterNode):
        metadata = dict(common["metadata"])
        metadata.setdefault("route_descriptions", dict(node.route_descriptions))
        return Worker(
            **{**common, "metadata": metadata},
            role="router",
            model=node.model,
            llm_hints=LLMHints(task_tier=node.task_tier),
        )
    if isinstance(node, ValidatorNode):
        metadata = dict(common["metadata"])
        metadata.setdefault("validator_on_failure", node.on_failure)
        metadata.setdefault("validator_strict_mode", node.strict_mode)
        return Worker(
            **{**common, "metadata": metadata},
            role="validator",
            validation_rules=[ValidationRule.model_validate(rule.model_dump(mode="json")) for rule in node.validation_rules],
        )
    if isinstance(node, ReflectionNode):
        metadata = dict(common["metadata"])
        metadata.setdefault("reflection_prompt", node.reflection_prompt)
        metadata.setdefault("reflection_model", node.reflection_model)
        metadata.setdefault("reflection_source", node.source)
        metadata.setdefault("reflection_source_config", dict(node.source_config))
        metadata.setdefault("reflection_output_format", node.output_format)
        metadata.setdefault("reflection_max_principles", node.max_principles)
        metadata.setdefault("reflection_min_confidence", node.min_confidence)
        metadata.setdefault("reflection_dedup_strategy", node.dedup_strategy)
        return Worker(
            **{**common, "metadata": metadata},
            role="reflection",
            model=node.reflection_model,
            llm_hints=LLMHints(task_tier=node.task_tier),
        )
    if isinstance(node, ReduceNode):
        metadata = dict(common["metadata"])
        metadata.setdefault("reduce_expression", node.reducer)
        return Worker(**{**common, "metadata": metadata}, role="reduce")
    if isinstance(node, RAGOperator):
        metadata = dict(common["metadata"])
        metadata.setdefault("rag_collection", node.collection)
        metadata.setdefault("rag_top_k", node.top_k)
        metadata.setdefault("rag_similarity_threshold", node.similarity_threshold)
        metadata.setdefault("rag_embedding_model", node.embedding_model)
        metadata.setdefault("rag_vector_store_config", dict(node.vector_store_config))
        metadata.setdefault("rag_query_template", node.query_template)
        metadata.setdefault("rag_include_metadata", node.include_metadata)
        metadata.setdefault("rag_rerank", node.rerank)
        return Worker(**{**common, "metadata": metadata}, role="rag")
    if isinstance(node, HumanInTheLoopNode):
        metadata = dict(common["metadata"])
        metadata.setdefault("human_prompt", node.prompt)
        metadata.setdefault("human_timeout_seconds", node.timeout_seconds)
        metadata.setdefault("human_default_action", node.default_action)
        metadata.setdefault("human_input_schema", node.input_schema)
        metadata.setdefault("human_output_schema", node.output_schema)
        metadata.setdefault("human_render_mode", node.render_mode)
        metadata.setdefault("human_options", node.options)
        metadata.setdefault("human_instructions", node.instructions)
        metadata.setdefault("human_render_target", node.render_target)
        return Worker(**{**common, "metadata": metadata}, role="human_in_the_loop")
    if isinstance(node, HumanNode):
        metadata = dict(common["metadata"])
        metadata.setdefault("human_prompt", node.prompt)
        metadata.setdefault("human_timeout_seconds", node.timeout_seconds)
        metadata.setdefault("human_default_action", node.default_action)
        metadata.setdefault("human_input_schema", node.input_schema)
        metadata.setdefault("human_output_schema", node.output_schema)
        metadata.setdefault("human_render_mode", node.render_mode)
        metadata.setdefault("human_options", node.options)
        metadata.setdefault("human_instructions", node.instructions)
        metadata.setdefault("human_render_target", node.render_target)
        return Worker(**{**common, "metadata": metadata}, role="human")
    if isinstance(node, VoteNode):
        metadata = dict(common["metadata"])
        metadata.setdefault("vote_candidates", list(node.candidates))
        metadata.setdefault("vote_num_votes", node.num_votes)
        metadata.setdefault("vote_prompt_template", node.prompt_template)
        metadata.setdefault("vote_system_prompt", node.system_prompt)
        metadata.setdefault("vote_temperature", node.temperature)
        metadata.setdefault("vote_output_json_schema", node.output_json_schema)
        metadata.setdefault("vote_strategy", node.vote_strategy)
        metadata.setdefault(
            "vote_config",
            None if node.vote_config is None else node.vote_config.model_dump(mode="json"),
        )
        metadata.setdefault("vote_parallelism", node.parallelism)
        metadata.setdefault("vote_timeout_seconds", node.timeout_seconds)
        return Worker(
            **{**common, "metadata": metadata},
            role="vote",
            llm_hints=LLMHints(task_tier=node.task_tier),
        )
    return None


def worker_to_legacy(node: Worker) -> NodeBase | None:
    """Project a simple Worker back onto an existing compute node model."""
    common: dict[str, Any] = {
        "id": node.id,
        "name": node.name,
        "description": node.description,
        "input_ports": node.input_ports,
        "output_ports": node.output_ports,
        "position": node.position,
        "ui": node.ui,
        "metadata": node.metadata,
        "tags": node.tags,
        "retry_policy": node.retry_policy,
        "read_set": node.read_set,
        "write_set": node.write_set,
        "memoize": node.memoize,
        "cache_ttl": node.cache_ttl,
    }
    metadata = dict(node.metadata or {})
    task_tier = node.llm_hints.task_tier if node.llm_hints is not None else None

    if node.control_flow is not None:
        gate_kwargs: dict[str, Any] = {
            "condition": node.control_flow.condition,
            "gate_mode": node.control_flow.gate_mode,
            "max_iterations": node.control_flow.max_iterations,
        }
        if node.control_flow.state_schema is not None:
            gate_kwargs["state_schema"] = node.control_flow.state_schema
        if node.control_flow.state_defaults is not None:
            gate_kwargs["state_defaults"] = node.control_flow.state_defaults
        if node.control_flow.feedback_selector is not None:
            gate_kwargs["feedback_selector"] = node.control_flow.feedback_selector
        if node.control_flow.artifact_ports is not None:
            gate_kwargs["artifact_ports"] = node.control_flow.artifact_ports
        return GateNode(**common, **gate_kwargs)

    if "input_variables" in metadata:
        return InputNode(
            **common,
            variables=[
                InputVariable.model_validate(variable)
                for variable in (metadata.get("input_variables") or [])
            ],
        )

    if node.role == "router" or "route_descriptions" in metadata:
        return RouterNode(
            **common,
            model=node.model or "",
            route_descriptions=dict(metadata.get("route_descriptions") or {}),
            task_tier=task_tier,
        )

    if node.role == "validator" or node.validation_rules or "validation_rules" in metadata:
        return ValidatorNode(
            **common,
            validation_rules=[
                ValidationRule.model_validate(rule)
                for rule in (
                    [rule.model_dump(mode="json") for rule in node.validation_rules]
                    if node.validation_rules
                    else (metadata.get("validation_rules") or [])
                )
            ],
            on_failure=metadata.get("validator_on_failure", "route"),
            strict_mode=bool(metadata.get("validator_strict_mode", False)),
        )

    if node.role == "reflection" or any(
        key in metadata
        for key in (
            "reflection_prompt",
            "reflection_model",
            "reflection_source",
            "reflection_source_config",
            "reflection_output_format",
            "reflection_max_principles",
            "reflection_min_confidence",
            "reflection_dedup_strategy",
        )
    ):
        return ReflectionNode(
            **common,
            reflection_prompt=str(metadata.get("reflection_prompt") or ""),
            reflection_model=metadata.get("reflection_model"),
            source=metadata.get("reflection_source", "last_run"),
            source_config=dict(metadata.get("reflection_source_config") or {}),
            output_format=metadata.get("reflection_output_format", "principles"),
            max_principles=int(metadata.get("reflection_max_principles", 10)),
            min_confidence=float(metadata.get("reflection_min_confidence", 0.3)),
            dedup_strategy=metadata.get("reflection_dedup_strategy", "embedding_similarity"),
            task_tier=task_tier,
        )

    if node.role == "reduce" or "reduce_expression" in metadata:
        return ReduceNode(
            **common,
            reducer=str(metadata.get("reduce_expression") or ""),
        )

    if node.role in {"human", "human_in_the_loop"} or any(
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
        node_cls = HumanInTheLoopNode if node.role == "human_in_the_loop" else HumanNode
        human_kwargs: dict[str, Any] = {
            "prompt": str(metadata.get("human_prompt") or ""),
            "render_mode": metadata.get("human_render_mode", "text"),
            "instructions": str(metadata.get("human_instructions") or ""),
            "render_target": metadata.get("human_render_target", "dialog"),
        }
        if "human_timeout_seconds" in metadata:
            human_kwargs["timeout_seconds"] = metadata.get("human_timeout_seconds")
        if "human_default_action" in metadata:
            human_kwargs["default_action"] = metadata.get("human_default_action")
        if "human_input_schema" in metadata:
            human_kwargs["input_schema"] = metadata.get("human_input_schema")
        if "human_output_schema" in metadata:
            human_kwargs["output_schema"] = metadata.get("human_output_schema")
        if "human_options" in metadata:
            human_kwargs["options"] = metadata.get("human_options")
        return node_cls(**common, **human_kwargs)

    if node.role == "vote" or any(
        key in metadata
        for key in (
            "vote_candidates",
            "vote_num_votes",
            "vote_prompt_template",
            "vote_system_prompt",
            "vote_temperature",
            "vote_output_json_schema",
            "vote_strategy",
            "vote_config",
            "vote_parallelism",
            "vote_timeout_seconds",
        )
    ):
        vote_kwargs: dict[str, Any] = {
            "candidates": list(metadata.get("vote_candidates") or []),
            "num_votes": int(metadata.get("vote_num_votes", 3)),
            "prompt_template": str(metadata.get("vote_prompt_template") or ""),
            "system_prompt": str(metadata.get("vote_system_prompt") or ""),
            "temperature": float(metadata.get("vote_temperature", 0.7)),
            "vote_strategy": metadata.get("vote_strategy", "majority"),
            "parallelism": int(metadata.get("vote_parallelism", 3)),
        }
        if "vote_output_json_schema" in metadata:
            vote_kwargs["output_json_schema"] = metadata.get("vote_output_json_schema")
        if "vote_config" in metadata:
            vote_kwargs["vote_config"] = metadata.get("vote_config")
        if "vote_timeout_seconds" in metadata:
            vote_kwargs["timeout_seconds"] = metadata.get("vote_timeout_seconds")
        if task_tier is not None:
            vote_kwargs["task_tier"] = task_tier
        return VoteNode(**common, **vote_kwargs)

    if node.role in {"rag", "retriever"} or "rag_collection" in metadata:
        similarity_threshold = metadata.get("rag_similarity_threshold")
        return RAGOperator(
            **common,
            collection=str(metadata.get("rag_collection") or ""),
            top_k=int(metadata.get("rag_top_k", 5)),
            similarity_threshold=(
                None if similarity_threshold is None else float(similarity_threshold)
            ),
            embedding_model=str(metadata.get("rag_embedding_model") or ""),
            vector_store_config=dict(metadata.get("rag_vector_store_config") or {}),
            query_template=str(metadata.get("rag_query_template") or "{query}"),
            include_metadata=bool(metadata.get("rag_include_metadata", True)),
            rerank=bool(metadata.get("rag_rerank", False)),
        )

    if node.code:
        return CodeOperator(**common, code=node.code, language=node.language)
    if (node.model is not None or llm_hints_configured(node.llm_hints)) and not node.tool_ids:
        hints = node.llm_hints or LLMHints()
        return LLMOperator(
            **common,
            model=node.model or "",
            prompt_template=hints.prompt_template or "{input}",
            system_prompt=hints.system_prompt,
            temperature=hints.temperature,
            max_tokens=hints.max_tokens,
            output_json_schema=hints.output_json_schema,
            tools=hints.tools,
            max_tool_rounds=hints.max_tool_rounds,
            history_policy=hints.history_policy,
            task_tier=hints.task_tier,
        )
    if len(node.tool_ids) == 1 and not node.model:
        return ToolOperator(
            **common,
            tool_id=node.tool_ids[0],
            tool_config=dict(node.metadata.get("tool_config") or {}),
        )
    return None


def convert_graph(graph: "Graph") -> "Graph":
    """Convert convertible legacy nodes in a graph (and subgraphs) to Workers."""
    from dan.models.graph import Graph

    converted_nodes: list[NodeBase] = []
    for node in graph.nodes:
        converted = legacy_to_worker(node)
        if converted is not None:
            converted_nodes.append(converted)
        else:
            converted_nodes.append(node.model_copy(deep=True))

    return Graph(
        version=graph.version,
        metadata=graph.metadata.model_copy(deep=True),
        nodes=converted_nodes,
        edges=[edge.model_copy(deep=True) for edge in graph.edges],
        sub_graphs={
            key: convert_graph(sub_graph)
            for key, sub_graph in graph.sub_graphs.items()
        },
        entry_points=list(graph.entry_points),
        exit_points=list(graph.exit_points),
        shared_context=[decl.model_copy(deep=True) for decl in graph.shared_context],
        artifact_refs=[artifact.model_copy(deep=True) for artifact in graph.artifact_refs],
        worker_resources=deepcopy(graph.worker_resources),
        hyperedges=[hyperedge.model_copy(deep=True) for hyperedge in graph.hyperedges],
    )


def validate_conversion(original: "Graph", converted: "Graph") -> list[str]:
    """Return structural drift errors for a converted graph."""
    errors: list[str] = []

    if len(original.nodes) != len(converted.nodes):
        errors.append(
            f"node count changed ({len(original.nodes)} -> {len(converted.nodes)})"
        )

    original_node_ids = [node.id for node in original.nodes]
    converted_node_ids = [node.id for node in converted.nodes]
    if original_node_ids != converted_node_ids:
        errors.append(
            f"node ids changed ({original_node_ids!r} -> {converted_node_ids!r})"
        )

    original_edges = [
        (
            edge.source_node_id,
            edge.source_port,
            edge.target_node_id,
            edge.target_port,
            edge.edge_type,
        )
        for edge in original.edges
    ]
    converted_edges = [
        (
            edge.source_node_id,
            edge.source_port,
            edge.target_node_id,
            edge.target_port,
            edge.edge_type,
        )
        for edge in converted.edges
    ]
    if original_edges != converted_edges:
        errors.append(f"edges changed ({original_edges!r} -> {converted_edges!r})")

    if original.entry_points != converted.entry_points:
        errors.append(
            f"entry points changed ({original.entry_points!r} -> {converted.entry_points!r})"
        )
    if original.exit_points != converted.exit_points:
        errors.append(
            f"exit points changed ({original.exit_points!r} -> {converted.exit_points!r})"
        )

    original_subgraphs = set(original.sub_graphs)
    converted_subgraphs = set(converted.sub_graphs)
    if original_subgraphs != converted_subgraphs:
        errors.append(
            f"sub_graph keys changed ({sorted(original_subgraphs)!r} -> {sorted(converted_subgraphs)!r})"
        )

    for key in sorted(original_subgraphs & converted_subgraphs):
        sub_errors = validate_conversion(original.sub_graphs[key], converted.sub_graphs[key])
        errors.extend(f"{key}: {error}" for error in sub_errors)

    return errors
