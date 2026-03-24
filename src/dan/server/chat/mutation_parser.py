"""Mutation JSON parsing, normalization, and audit persistence."""

from __future__ import annotations

import json
import logging
from typing import Any

from dan.agent_runtime.mutation_parsing import (
    _JSON_BLOCK_RE,
    _normalize_usage,
    _try_parse_mutation_json,
)
from dan.agent_runtime.mutation_preview import (
    _coerce_strict_edges,
    _normalize_generated_mutation_ops,
    normalize_mutation_ops_for_chat,
)
from dan.providers import CompletionResult
from dan.server.chat.helpers import _extract_cited_sources
from dan.server.search_models import CitationRecord, CitationVerification, SearchResult

logger = logging.getLogger(__name__)


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


def extract_mutation_from_result(
    result: CompletionResult,
) -> dict[str, Any] | None:
    """Extract a mutation plan dict from a completion result."""
    if result.tool_calls:
        for tc in result.tool_calls:
            func = tc.get("function", {})
            if func.get("name") == "plan_graph_mutations":
                try:
                    data = json.loads(func["arguments"])
                    if isinstance(data.get("operations"), list):
                        return data
                except (json.JSONDecodeError, KeyError):
                    pass
    return _try_parse_mutation_json(result.text or "")

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
        applied_ops = getattr(dry_result, "applied_ops", None)
        if applied_ops is None:
            applied_ops = getattr(dry_result, "applied_operations", [])
        n_ops = len(applied_ops or [])
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
            prompt_module_ids=[
                str(module_id)
                for module_id in (audit_metadata.get("prompt_module_ids") or [])
                if str(module_id or "").strip()
            ],
            workflow_guidance_injected=bool(audit_metadata.get("workflow_guidance_injected", False)),
            workflow_guidance_surface=str(audit_metadata.get("workflow_guidance_surface") or ""),
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
