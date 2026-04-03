"""Engine-side lint config generation and adaptation helpers."""

from __future__ import annotations

import re
from typing import Any, Callable

from dan.linter import IntentConfig, LintConfig, RuleSeverity, SemanticConfig, StructuralConfig
from dan.models.edges import DataEdge
from dan.worker.model import Worker

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
IntentRefiner = Callable[[str, dict[str, Any]], str | None]


def _resolve_worker_instruction(node: Worker, graph: Any | None) -> str:
    parts: list[str] = []
    catalog = getattr(graph, "worker_resources", {}) or {}
    ctx = node.context
    if ctx is not None:
        if ctx.inherit_defaults:
            default_bundle = catalog.get("defaults") or {}
            if default_bundle.get("instruction"):
                parts.append(str(default_bundle["instruction"]))
        if ctx.instruction_profile_ref:
            profile = (catalog.get("instruction_profiles") or {}).get(ctx.instruction_profile_ref)
            if isinstance(profile, dict) and profile.get("instruction"):
                parts.append(str(profile["instruction"]))
        for ref in ctx.context_bundle_refs:
            bundle = (catalog.get("context_bundles") or {}).get(ref)
            if isinstance(bundle, dict) and bundle.get("instruction"):
                parts.append(str(bundle["instruction"]))
    if node.instruction:
        parts.append(node.instruction)
    elif node.persona:
        parts.append(node.persona)
    return "\n\n".join(part for part in parts if part)


def _keywords(text: str) -> list[str]:
    seen: list[str] = []
    for token in _TOKEN_RE.findall(text.lower()):
        if token not in seen and token not in {"the", "and", "for", "with", "this", "that", "from"}:
            seen.append(token)
    return seen[:8]


def _default_min_similarity(target_node: Any) -> float:
    if not isinstance(target_node, Worker):
        return 0.7
    task_tier = None
    if target_node.llm_hints is not None:
        task_tier = target_node.llm_hints.task_tier
    if task_tier is None and target_node.authority_policy is not None:
        task_tier = target_node.authority_policy.task_tier_cap
    return 0.85 if task_tier == "critical" else 0.7


def _deterministic_intent_text(
    source_node: Any,
    target_node: Worker,
    target_port: Any,
    instruction: str,
) -> str:
    parts = [
        target_node.role,
        instruction,
        target_port.description,
        target_node.description,
        getattr(source_node, "description", ""),
    ]
    return " ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())


def _intent_refinement_context(
    source_node: Any,
    target_node: Worker,
    target_port: Any,
    graph: Any | None,
    instruction: str,
) -> dict[str, Any]:
    metadata = getattr(graph, "metadata", None)
    return {
        "graph_name": getattr(metadata, "name", ""),
        "graph_description": getattr(metadata, "description", ""),
        "source_node_id": getattr(source_node, "id", ""),
        "source_node_type": getattr(source_node, "node_type", ""),
        "source_description": getattr(source_node, "description", ""),
        "target_node_id": target_node.id,
        "target_node_type": target_node.node_type,
        "target_role": target_node.role,
        "target_description": target_node.description,
        "target_port": target_port.name,
        "target_port_description": target_port.description,
        "instruction": instruction,
    }


def _refine_intent_text(
    base_intent: str,
    *,
    refiner: IntentRefiner | None,
    context: dict[str, Any],
) -> str:
    if refiner is None or not base_intent:
        return base_intent
    try:
        refined = refiner(base_intent, context)
    except Exception:
        return base_intent
    if isinstance(refined, str) and refined.strip():
        return refined.strip()
    return base_intent


def generate_lint_config(
    source_node: Any,
    target_node: Any,
    edge: DataEdge,
    graph: Any | None = None,
    *,
    intent_refiner: IntentRefiner | None = None,
) -> LintConfig | None:
    """Generate a conservative lint config for a data edge."""
    target_port = next(
        (port for port in getattr(target_node, "input_ports", []) if port.name == edge.target_port),
        None,
    )
    if target_port is None:
        return None

    structural = None
    if target_port.json_schema or target_port.required:
        properties = (
            (target_port.json_schema or {}).get("properties", {})
            if isinstance((target_port.json_schema or {}).get("properties", {}), dict)
            else {}
        )
        string_max_lengths = {
            name: int(prop["maxLength"])
            for name, prop in properties.items()
            if isinstance(prop, dict) and isinstance(prop.get("maxLength"), int)
        }
        ranges = {
            name: {
                bound: float(prop[bound])
                for bound in ("minimum", "maximum")
                if isinstance(prop, dict) and isinstance(prop.get(bound), (int, float))
            }
            for name, prop in properties.items()
            if isinstance(prop, dict)
            and any(isinstance(prop.get(bound), (int, float)) for bound in ("minimum", "maximum"))
        }
        format_patterns = {
            name: str(prop["pattern"])
            for name, prop in properties.items()
            if isinstance(prop, dict) and isinstance(prop.get("pattern"), str) and prop.get("pattern")
        }
        structural = StructuralConfig(
            json_schema=target_port.json_schema or None,
            required_keys=list((target_port.json_schema or {}).get("required", [])),
            string_max_lengths=string_max_lengths,
            ranges=ranges,
            format_patterns=format_patterns,
        )

    semantic = None
    intent = None
    if isinstance(target_node, Worker):
        instruction = _resolve_worker_instruction(target_node, graph)
        semantic_text = " ".join(part for part in [target_node.description, target_port.description, target_node.role, instruction] if part)
        keywords = _keywords(semantic_text)
        if semantic_text or keywords:
            semantic = SemanticConfig(
                topic_keywords=keywords,
                reference_text=semantic_text,
                min_similarity=_default_min_similarity(target_node) if semantic_text else None,
            )
        base_intent = _deterministic_intent_text(source_node, target_node, target_port, instruction)
        intent_text = _refine_intent_text(
            base_intent,
            refiner=intent_refiner,
            context=_intent_refinement_context(source_node, target_node, target_port, graph, instruction),
        )
        if intent_text:
            intent = IntentConfig(intent=intent_text, required_keywords=_keywords(intent_text)[:4])
    else:
        semantic_text = " ".join(part for part in [getattr(target_node, "description", ""), target_port.description] if part)
        if semantic_text:
            semantic = SemanticConfig(topic_keywords=_keywords(semantic_text), reference_text=semantic_text)

    if not any([structural, semantic, intent]):
        return None
    return LintConfig(
        structural=structural,
        semantic=semantic,
        intent=intent,
        severity=RuleSeverity.ERROR,
        autofix=["fill_defaults", "truncate", "clamp"],
    )
