"""Mutation JSON parsing, normalization, and audit persistence."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from dan.server.chat.helpers import _extract_cited_sources
from dan.server.search_models import CitationRecord, CitationVerification, SearchResult

logger = logging.getLogger(__name__)


def _normalize_usage(raw: dict[str, int] | None) -> dict[str, int]:
    """Normalize provider usage dicts to both UI and telemetry keys."""
    if not raw:
        return {}
    prompt = int(raw.get("prompt", 0) or raw.get("prompt_tokens", 0) or 0)
    completion = int(
        raw.get("completion", 0) or raw.get("completion_tokens", 0) or 0
    )
    total = int(raw.get("total_tokens", 0) or (prompt + completion))
    cached_input_tokens = int(raw.get("cached_input_tokens", 0) or 0)
    cache_write_tokens = int(raw.get("cache_write_tokens", 0) or 0)
    return {
        "prompt": prompt,
        "completion": completion,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_tokens": cache_write_tokens,
    }


def _merge_usage_totals(total: dict[str, int], raw: dict[str, int] | None) -> dict[str, int]:
    """Accumulate provider usage dicts across multiple LLM calls."""
    if not raw:
        return total
    prior_prompt = int(total.get("prompt", 0) or total.get("prompt_tokens", 0) or 0)
    prior_completion = int(
        total.get("completion", 0) or total.get("completion_tokens", 0) or 0
    )
    prior_total = int(total.get("total_tokens", 0) or (prior_prompt + prior_completion))
    normalized = _normalize_usage(raw)
    merged = dict(total)
    merged_prompt = prior_prompt + normalized.get("prompt", 0)
    merged_completion = prior_completion + normalized.get("completion", 0)
    merged["prompt"] = merged_prompt
    merged["completion"] = merged_completion
    merged["prompt_tokens"] = merged_prompt
    merged["completion_tokens"] = merged_completion
    merged["total_tokens"] = prior_total + normalized.get("total_tokens", 0)
    merged["cached_input_tokens"] = int(merged.get("cached_input_tokens", 0) or 0) + normalized.get(
        "cached_input_tokens", 0
    )
    merged["cache_write_tokens"] = int(merged.get("cache_write_tokens", 0) or 0) + normalized.get(
        "cache_write_tokens", 0
    )
    return merged


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _try_parse_mutation_json(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a mutation plan from freeform LLM text."""
    for match in _JSON_BLOCK_RE.finditer(text):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict) and isinstance(data.get("operations"), list):
                return data
        except (json.JSONDecodeError, KeyError):
            continue
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict) and isinstance(data.get("operations"), list):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _coerce_strict_edges(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For build-mode (empty-graph) flows, enforce strict edge validation.

    Prevents auto-creation of misspelled ports by setting strict=True on
    add_edge ops.  If the LLM explicitly sets strict=False, that override
    is preserved via setdefault.
    """
    result = []
    for op in operations:
        op = dict(op)
        if op.get("op") == "add_edge":
            op.setdefault("strict", True)
        result.append(op)
    return result


def _normalize_generated_mutation_ops(
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize common LLM schema drifts in mutation operations.

    This is intentionally conservative: it only repairs a few high-frequency
    shape mismatches so dry-run can proceed and auto-repair has a chance to
    converge.
    """
    normalized: list[dict[str, Any]] = []
    for op in operations:
        if not isinstance(op, dict):
            continue
        op_norm = dict(op)
        if op_norm.get("op") == "add_edge":
            cfg = op_norm.get("config")
            if isinstance(cfg, dict):
                for key in ("source_id", "source_port", "target_id", "target_port"):
                    if key not in op_norm and key in cfg:
                        op_norm[key] = cfg[key]
                op_norm.pop("config", None)
            normalized.append(op_norm)
            continue

        if op_norm.get("op") != "add_node":
            normalized.append(op_norm)
            continue

        config = op_norm.get("config")
        if not isinstance(config, dict):
            normalized.append(op_norm)
            continue
        cfg = dict(config)
        node_type = op_norm.get("node_type")

        if node_type == "parallel_subagents":
            branch_graphs = cfg.get("branch_graphs")
            if isinstance(branch_graphs, dict):
                cfg["branch_graphs"] = list(branch_graphs.keys())
            elif isinstance(branch_graphs, str):
                cfg["branch_graphs"] = [branch_graphs]

            branch_inputs = cfg.get("branch_inputs")
            if isinstance(branch_inputs, dict):
                remapped: dict[str, dict[str, Any]] = {}
                for branch, val in branch_inputs.items():
                    if isinstance(val, dict):
                        remapped[branch] = val
                    elif isinstance(val, str):
                        remapped[branch] = {"input": val}
                    else:
                        remapped[branch] = {"input": val}
                cfg["branch_inputs"] = remapped

        elif node_type == "validator":
            on_failure = cfg.get("on_failure")
            if isinstance(on_failure, str):
                alias = {
                    "retry": "route",
                    "continue": "warn",
                    "ignore": "warn",
                    "stop": "halt",
                    "fail": "halt",
                }
                cfg["on_failure"] = alias.get(on_failure, on_failure)

            rules = cfg.get("validation_rules")
            if isinstance(rules, list):
                fixed_rules: list[dict[str, Any]] = []
                for item in rules:
                    if not isinstance(item, dict):
                        continue
                    rule = dict(item)
                    rule_type = rule.get("rule_type")
                    if rule_type in ("required_field", "required_fields"):
                        rule["rule_type"] = "required_keys"
                        rule_cfg = rule.get("config")
                        if not isinstance(rule_cfg, dict):
                            rule_cfg = {}
                        if "keys" not in rule_cfg:
                            if isinstance(rule_cfg.get("field"), str):
                                rule_cfg["keys"] = [rule_cfg["field"]]
                            elif isinstance(rule_cfg.get("key"), str):
                                rule_cfg["keys"] = [rule_cfg["key"]]
                            elif isinstance(rule_cfg.get("fields"), list):
                                rule_cfg["keys"] = list(rule_cfg["fields"])
                            elif isinstance(rule_cfg.get("required"), list):
                                rule_cfg["keys"] = list(rule_cfg["required"])
                            elif isinstance(rule.get("field"), str):
                                rule_cfg["keys"] = [rule["field"]]
                            elif isinstance(rule.get("key"), str):
                                rule_cfg["keys"] = [rule["key"]]
                        rule["config"] = rule_cfg
                    fixed_rules.append(rule)
                cfg["validation_rules"] = fixed_rules

        op_norm["config"] = cfg
        normalized.append(op_norm)

    return normalized


def _build_args_preview(mutation_data: dict[str, Any]) -> str:
    """Build a short human-readable summary of mutation arguments."""
    desc = mutation_data.get("description", "")
    ops = mutation_data.get("operations", [])
    n_ops = len(ops)
    if desc:
        return f"{desc} ({n_ops} operation{'s' if n_ops != 1 else ''})"
    if n_ops > 0:
        op_types = [op.get("op", "?") for op in ops[:3]]
        suffix = f" +{n_ops - 3} more" if n_ops > 3 else ""
        return ", ".join(op_types) + suffix
    return f"{n_ops} operations"


def _build_dry_run_preview(dry_result: Any) -> tuple[str, str]:
    """Build (status, output_preview) from a dry-run result."""
    if dry_result.success:
        n_ops = len(dry_result.applied_operations) if hasattr(dry_result, "applied_operations") else 0
        return "success", f"Dry run passed ({n_ops} operations applied)"
    errors = [e.message for e in dry_result.errors] if dry_result.errors else ["Unknown error"]
    return "error", f"Dry run failed: {errors[0]}"


def _try_persist_audit(
    *,
    workflow_id: str,
    message_id: str,
    user_message: str,
    assistant_message: str,
    mode: str,
    model: str,
    audit_tool_records: list[dict[str, Any]],
    prompt_messages: list[dict[str, Any]] | None = None,
    surface: str | None = None,
    error: str | None = None,
    audit_metadata: dict[str, Any] | None = None,
) -> None:
    """Best-effort audit record persistence — never raises."""
    try:
        from dan.server.audit import ChatAuditRecord, ChatAuditStore, ToolCallRecord
    except ImportError:
        return

    try:
        audit_metadata = audit_metadata or {}
        prompt_messages = prompt_messages or []
        tc_records = [
            ToolCallRecord(
                tool_name=r.get("tool_name", ""),
                args=r.get("args") or {"preview": r.get("args_preview", "")},
                result_summary=r.get("output_preview", "")[:500],
                status=r.get("status", "success"),
                duration_ms=r.get("duration_ms", 0),
                source_urls=list(r.get("source_urls") or []),
                source_files=list(r.get("source_files") or []),
                search_results=[
                    item if isinstance(item, SearchResult) else SearchResult.model_validate(item)
                    for item in (r.get("search_results") or [])
                ],
                citations=[
                    item if isinstance(item, CitationRecord) else CitationRecord.model_validate(item)
                    for item in (r.get("citations") or [])
                ],
                citation_verifications=[
                    item
                    if isinstance(item, CitationVerification)
                    else CitationVerification.model_validate(item)
                    for item in (r.get("citation_verifications") or [])
                ],
            )
            for r in audit_tool_records
        ]
        cited = _extract_cited_sources(audit_tool_records)
        aggregated_search_results = [
            result
            for record in tc_records
            for result in record.search_results
        ]
        aggregated_citations = [
            citation
            for record in tc_records
            for citation in record.citations
        ]
        aggregated_verifications = [
            verification
            for record in tc_records
            for verification in record.citation_verifications
        ]
        run_id = str(audit_metadata.get("run_id") or "").strip()
        if not run_id:
            for record in audit_tool_records:
                result_data = record.get("result_data")
                if isinstance(result_data, dict) and result_data.get("run_id"):
                    run_id = str(result_data["run_id"])
                    break
        record = ChatAuditRecord(
            turn_id=message_id,
            surface_id=surface or "server",
            project_id=str(audit_metadata.get("project_id") or ""),
            task_id=str(audit_metadata.get("task_id") or ""),
            workflow_id=workflow_id,
            run_id=run_id,
            user_message=user_message,
            assistant_message=assistant_message or error or "",
            intent=str(audit_metadata.get("intent") or ""),
            mode=mode,
            prompt_messages=[
                {
                    "role": str(msg.get("role", "")),
                    "content": str(msg.get("content", "")),
                }
                for msg in prompt_messages
            ],
            model=model,
            tool_calls=tc_records,
            cited_sources=cited,
            search_results=aggregated_search_results,
            citations=aggregated_citations,
            citation_verifications=aggregated_verifications,
            memory_item_ids=list(audit_metadata.get("memory_item_ids") or []),
        )
        ChatAuditStore().append(record)
    except Exception:
        logger.warning("Audit record persistence failed", exc_info=True)
