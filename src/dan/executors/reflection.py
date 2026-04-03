"""Reflection executor — LLM-based causal analysis of run failures."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.models.legacy import ReflectionNode
from dan.models.nodes import NodeBase

from .provider_runtime import resolve_completion_provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default prompts
# ---------------------------------------------------------------------------

_DEFAULT_REFLECTION_PROMPT = """\
You are a failure analysis expert. Analyze the following error data from \
workflow execution runs and extract structured causal principles.

For each distinct failure pattern, produce a principle with:
- condition: When this applies (specific scenario description)
- action: What to do or avoid (actionable guidance)
- reason: Why this matters (root cause explanation)
- confidence: Your confidence in this principle (0.0 to 1.0)
- tags: Relevant labels for targeting (e.g., "llm_operator", "data_processing", "api_calls")
- repair_level: Classification of what kind of fix is needed (see below)
- suggested_parameter_changes: (only when repair_level is "parameter_fix") dictionary of parameter changes
- structural_description: (only when repair_level is "structural_fix" or "redesign") description of structural changes

## Repair Level Classification

Classify each principle into exactly one repair level:

- "retry" — The error is transient (network timeout, rate limit, temporary API outage). A retry with backoff would fix it. No principle needed — skip these unless the pattern is chronic.
- "prompt_fix" — The LLM produced wrong output because the instructions were insufficient or misleading. Fix by changing the prompt or adding guidance. Most common level.
- "parameter_fix" — The node configuration is wrong: wrong model, temperature too high/low, insufficient max_tokens, timeout too short, wrong tool config. Fix by changing node parameters. Include suggested_parameter_changes.
- "structural_fix" — The workflow topology needs changes: a missing validation step, wrong node ordering, missing error handler. Fix requires adding/removing/rewiring nodes.
- "redesign" — The fundamental approach is wrong. The entire workflow or major section needs rethinking.

Output a JSON array of principles. Each principle must have: \
condition, action, reason, confidence, tags, repair_level.
Include suggested_parameter_changes only for parameter_fix. \
Include structural_description only for structural_fix or redesign.

Guidelines:
- Focus on ROOT CAUSES, not symptoms
- Be SPECIFIC, not generic (bad: "avoid errors", good: "pre-filter dates before 1990 for CRSP merge_asof")
- Include actionable guidance that an LLM agent can follow
- Set confidence based on how many runs show the same pattern and how clear the cause is
- Do NOT exceed {max_principles} principles
- Default to "prompt_fix" if unsure about the repair level

## Examples of good principles:
[{{"condition": "CRSP data loader receives dates before 1990", \
"action": "Pre-filter input dates to avoid merge_asof tolerance errors", \
"reason": "merge_asof with DateOffset tolerance fails on pre-1990 data due to calendar boundaries", \
"confidence": 0.85, "tags": ["data_processing", "crsp"], "repair_level": "prompt_fix"}},
{{"condition": "LLM generates overly verbose JSON exceeding token limit", \
"action": "Switch to a model with larger context window or reduce input size", \
"reason": "Model max_tokens is 4096 but typical output requires 6000+ tokens", \
"confidence": 0.9, "tags": ["llm_operator"], "repair_level": "parameter_fix", \
"suggested_parameter_changes": {{"model": "gpt-4-turbo", "max_tokens": 8192}}}},
{{"condition": "API response validation fails silently, corrupting downstream data", \
"action": "Add a validator node between the API call and data processing nodes", \
"reason": "No validation step exists to catch malformed API responses before they propagate", \
"confidence": 0.75, "tags": ["api_calls", "validation"], "repair_level": "structural_fix", \
"structural_description": "Insert a ValidatorNode after the API call node with JSON schema validation"}}]

## Examples of bad principles (avoid these):
[{{"condition": "An error occurs", "action": "Fix the error", \
"reason": "Errors are bad", "confidence": 0.5, "tags": [], "repair_level": "prompt_fix"}}]
"""


# ---------------------------------------------------------------------------
# CausalPrinciple — lightweight dict-based representation
# ---------------------------------------------------------------------------

def _make_principle(
    *,
    condition: str,
    action: str,
    reason: str,
    confidence: float,
    tags: list[str] | None = None,
    source_run_ids: list[str] | None = None,
    source_node_ids: list[str] | None = None,
    workflow_id: str = "",
    repair_level: str = "prompt_fix",
    suggested_parameter_changes: dict[str, Any] | None = None,
    structural_description: str = "",
) -> dict[str, Any]:
    """Build a CausalPrinciple dict with defaults."""
    now = time.time()
    return {
        "id": str(uuid.uuid4()),
        "condition": condition,
        "action": action,
        "reason": reason,
        "confidence": max(0.0, min(1.0, confidence)),
        "tags": tags or [],
        "source_run_ids": source_run_ids or [],
        "source_node_ids": source_node_ids or [],
        "workflow_id": workflow_id,
        "created_at": now,
        "updated_at": now,
        "repair_level": repair_level,
        "suggested_parameter_changes": suggested_parameter_changes or {},
        "structural_description": structural_description,
    }


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class ReflectionExecutor:
    """Executes ReflectionNode — gathers error context, calls LLM to distill
    causal principles, deduplicates, and returns structured output."""

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, ReflectionNode)

        model = node.reflection_model or context.config.llm_default_model
        provider = resolve_completion_provider(context, model)
        if provider is None:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=(
                    "ReflectionExecutor requires a provider_registry on the "
                    "execution context (no LLM provider available)."
                ),
            )

        source_data = self._gather_source_data(node, inputs)

        if not source_data.strip():
            return NodeResult(
                outputs={
                    "principles": [],
                    "principle_count": 0,
                    "source": node.source,
                    "text": "No error data found in inputs; nothing to reflect on.",
                },
                status=NodeStatus.COMPLETED,
                metadata={"model": model, "skipped": True},
            )

        messages = self._build_messages(node, source_data, model)

        try:
            async with context.llm_slot():
                result = await provider.complete(
                    messages=messages,
                    model=model,
                    temperature=0.3,
                    max_tokens=2000,
                )
            raw_text = result.text
            usage = result.usage
        except Exception as exc:
            logger.warning("Reflection LLM call failed: %s", exc)
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Reflection LLM call failed: {exc}",
                metadata={"model": model},
            )

        if context.cost_tracker is not None and usage:
            context.cost_tracker.record(node.id, model, usage)

        principles = self._parse_principles(raw_text, node)

        principles = [
            p for p in principles if p["confidence"] >= node.min_confidence
        ]

        principles = principles[: node.max_principles]

        run_id = getattr(context, "_run_id", "")
        for p in principles:
            if run_id and run_id not in p.get("source_run_ids", []):
                p.setdefault("source_run_ids", []).append(run_id)

        existing = inputs.get("existing_principles", [])
        if existing and node.dedup_strategy != "none":
            principles = self._deduplicate(
                principles, existing, node.dedup_strategy
            )

        text_summary = self._format_summary(principles, node.source)

        return NodeResult(
            outputs={
                "principles": principles,
                "principle_count": len(principles),
                "source": node.source,
                "text": text_summary,
            },
            status=NodeStatus.COMPLETED,
            metadata={
                "model": model,
                "usage": usage or {},
                "principles": principles,
            },
        )

    # ------------------------------------------------------------------
    # Source data gathering
    # ------------------------------------------------------------------

    def _gather_source_data(
        self, node: ReflectionNode, inputs: dict[str, Any]
    ) -> str:
        """Build a text summary of error data from inputs.

        The ReflectionNode receives its source data via upstream edges.
        When triggered by RunManager._schedule_reflection(), inputs contain
        run_errors, run_events, run_id, etc.  When used as a standalone node,
        inputs come from whatever upstream edges are wired.
        """
        if node.source == "last_run":
            return self._gather_last_run(inputs)
        if node.source == "last_n_runs":
            return self._gather_last_n_runs(inputs, node.source_config)
        if node.source == "error_index":
            return self._gather_from_error_index(inputs, node.source_config)
        return self._gather_last_run(inputs)

    @staticmethod
    def _gather_last_run(inputs: dict[str, Any]) -> str:
        """Extract error context from a single run's data in inputs."""
        parts: list[str] = []

        run_id = inputs.get("run_id", "unknown")
        parts.append(f"## Run: {run_id}\n")

        errors = inputs.get("run_errors") or inputs.get("errors")
        if isinstance(errors, list):
            parts.append(f"### Errors ({len(errors)} total)\n")
            for i, err in enumerate(errors, 1):
                if isinstance(err, dict):
                    parts.append(
                        f"**Error {i}** — node: `{err.get('node_id', '?')}`, "
                        f"type: `{err.get('node_type', '?')}`\n"
                        f"  message: {err.get('error', err.get('message', str(err)))}\n"
                    )
                    if err.get("input_snapshot"):
                        snap = json.dumps(err["input_snapshot"], default=str)
                        parts.append(f"  input_snapshot: {snap[:500]}\n")
                else:
                    parts.append(f"**Error {i}**: {err}\n")
        elif isinstance(errors, dict):
            parts.append(f"### Error\n{json.dumps(errors, default=str)[:1000]}\n")
        elif isinstance(errors, str) and errors:
            parts.append(f"### Error\n{errors}\n")

        events = inputs.get("run_events") or inputs.get("events")
        if isinstance(events, list) and events:
            failed_events = [
                e for e in events
                if isinstance(e, dict) and e.get("event_type") in (
                    "node_failed", "error", "retry_attempted",
                )
            ]
            if failed_events:
                parts.append(f"\n### Failure Events ({len(failed_events)} relevant)\n")
                for ev in failed_events[:20]:
                    parts.append(
                        f"- [{ev.get('event_type')}] node={ev.get('node_id', '?')}: "
                        f"{json.dumps(ev.get('data', {}), default=str)[:300]}\n"
                    )

        node_statuses = inputs.get("node_statuses")
        if isinstance(node_statuses, dict):
            failed_nodes = {
                k: v for k, v in node_statuses.items()
                if v in ("failed", "FAILED")
            }
            if failed_nodes:
                parts.append(f"\n### Failed Nodes: {', '.join(failed_nodes.keys())}\n")

        if inputs.get("error_text"):
            parts.append(f"\n### Raw Error Text\n{inputs['error_text'][:2000]}\n")

        runtime_repair_lineage = inputs.get("runtime_repair_lineage")
        runtime_repair_summaries = inputs.get("runtime_repair_summaries")
        if isinstance(runtime_repair_lineage, dict) and runtime_repair_lineage:
            parts.append("\n### Runtime Repair Lineage\n")
            for node_id, lineage in list(runtime_repair_lineage.items())[:20]:
                if not isinstance(lineage, dict):
                    continue
                last_summary = lineage.get("last_summary", {})
                attempts = lineage.get("attempts", [])
                if isinstance(last_summary, dict) and last_summary:
                    parts.append(
                        f"- node `{node_id}`: attempted `{last_summary.get('repair_attempted', '')}`; "
                        f"cause `{last_summary.get('cause', '')}`; "
                        f"next_step `{last_summary.get('next_step', '')}`; "
                        f"attempts={len(attempts) if isinstance(attempts, list) else 0}\n"
                    )
                else:
                    parts.append(
                        f"- node `{node_id}`: attempts={len(attempts) if isinstance(attempts, list) else 0}\n"
                    )

        if isinstance(runtime_repair_summaries, dict) and runtime_repair_summaries:
            parts.append("\n### Runtime Repair Summaries\n")
            for node_id, summary in list(runtime_repair_summaries.items())[:20]:
                if not isinstance(summary, dict):
                    continue
                parts.append(
                    f"- node `{node_id}`: {json.dumps(summary, default=str)[:500]}\n"
                )

        if len(parts) <= 1:
            raw_keys = [k for k in inputs if k not in ("existing_principles",)]
            if raw_keys:
                parts.append("\n### Available Input Keys\n")
                for k in raw_keys:
                    val = inputs[k]
                    preview = str(val)[:300]
                    parts.append(f"- `{k}`: {preview}\n")

        return "\n".join(parts)

    @staticmethod
    def _gather_last_n_runs(
        inputs: dict[str, Any], source_config: dict[str, Any]
    ) -> str:
        """Extract error patterns across multiple runs from inputs."""
        n = source_config.get("n", 5)
        runs = inputs.get("runs")
        if not isinstance(runs, list):
            return ReflectionExecutor._gather_last_run(inputs)

        parts: list[str] = [f"## Analysis of last {min(n, len(runs))} runs\n"]
        for i, run in enumerate(runs[:n], 1):
            if not isinstance(run, dict):
                continue
            rid = run.get("run_id", f"run_{i}")
            status = run.get("status", "unknown")
            parts.append(f"\n### Run {i}: {rid} (status: {status})\n")

            errs = run.get("errors") or run.get("run_errors")
            if isinstance(errs, list):
                for err in errs[:10]:
                    if isinstance(err, dict):
                        parts.append(
                            f"- node `{err.get('node_id', '?')}`: "
                            f"{err.get('error', err.get('message', ''))}\n"
                        )
                    else:
                        parts.append(f"- {err}\n")
            elif errs:
                parts.append(f"- {str(errs)[:500]}\n")

        return "\n".join(parts)

    @staticmethod
    def _gather_from_error_index(
        inputs: dict[str, Any], source_config: dict[str, Any]
    ) -> str:
        """Use pre-fetched error index results from inputs."""
        query = source_config.get("query", "")
        results = inputs.get("error_index_results") or inputs.get("similar_errors")
        if not isinstance(results, list):
            return ReflectionExecutor._gather_last_run(inputs)

        parts: list[str] = [f'## Error index query: "{query}"\n']
        for i, item in enumerate(results[:20], 1):
            if isinstance(item, dict):
                parts.append(
                    f"**{i}.** node=`{item.get('node_id', '?')}`, "
                    f"score={item.get('score', '?')}\n"
                    f"  error: {item.get('error', item.get('message', ''))}\n"
                )
            else:
                parts.append(f"**{i}.** {item}\n")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        node: ReflectionNode,
        source_data: str,
        model: str,
    ) -> list[dict[str, str]]:
        system_prompt = node.reflection_prompt or _DEFAULT_REFLECTION_PROMPT.format(
            max_principles=node.max_principles,
        )

        user_content = (
            f"Analyze the following error data and produce up to "
            f"{node.max_principles} causal principles.\n\n"
            f"Output format: {node.output_format}\n\n"
            f"---\n\n{source_data}"
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

    # ------------------------------------------------------------------
    # Output parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_principles(
        raw_text: str, node: ReflectionNode
    ) -> list[dict[str, Any]]:
        """Parse LLM output into a list of CausalPrinciple dicts.

        Tries direct JSON parse first, then looks for a JSON array within
        the text (```json fences, first [ ... ] block, etc.).
        """
        cleaned = raw_text.strip()
        attempts = [
            cleaned,
            _extract_json_block(cleaned),
            _extract_first_array(cleaned),
        ]

        for candidate in attempts:
            if not candidate:
                continue
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    return [
                        _normalize_principle(p)
                        for p in parsed
                        if isinstance(p, dict) and p.get("condition")
                    ]
                if isinstance(parsed, dict) and "principles" in parsed:
                    return [
                        _normalize_principle(p)
                        for p in parsed["principles"]
                        if isinstance(p, dict) and p.get("condition")
                    ]
            except (json.JSONDecodeError, TypeError, ValueError):
                continue

        logger.warning(
            "ReflectionExecutor: could not parse LLM output as JSON. "
            "Raw text (first 500 chars): %s",
            raw_text[:500],
        )
        return []

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def _deduplicate(
        new_principles: list[dict[str, Any]],
        existing: list[dict[str, Any]],
        strategy: str,
    ) -> list[dict[str, Any]]:
        if strategy == "none":
            return new_principles

        if strategy == "exact_key":
            return _dedup_exact_key(new_principles, existing)

        if strategy == "embedding_similarity":
            # TODO: implement embedding-based dedup when embedding provider
            # access is wired into ExecutionContext for non-RAG use.
            # Falls back to exact_key for now.
            return _dedup_exact_key(new_principles, existing)

        return new_principles

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_summary(
        principles: list[dict[str, Any]], source: str
    ) -> str:
        if not principles:
            return f"Reflection ({source}): no principles extracted."

        lines = [f"Reflection ({source}): {len(principles)} principle(s) extracted.\n"]
        for i, p in enumerate(principles, 1):
            lines.append(
                f"{i}. [{p.get('confidence', 0):.0%}] "
                f"When {p.get('condition', '?')}: {p.get('action', '?')}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers (module-private)
# ---------------------------------------------------------------------------


def _extract_json_block(text: str) -> str | None:
    """Extract content from ```json ... ``` fences."""
    markers = ["```json", "```JSON", "```"]
    for marker in markers:
        start = text.find(marker)
        if start == -1:
            continue
        content_start = text.index("\n", start) + 1 if "\n" in text[start:] else start + len(marker)
        end = text.find("```", content_start)
        if end == -1:
            continue
        candidate = text[content_start:end].strip()
        if candidate.startswith("[") or candidate.startswith("{"):
            return candidate
    return None


def _extract_first_array(text: str) -> str | None:
    """Extract the first [...] JSON array from text."""
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _normalize_principle(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw LLM-output dict into a CausalPrinciple."""
    valid_repair_levels = {"retry", "prompt_fix", "parameter_fix", "structural_fix", "redesign"}
    raw_level = str(raw.get("repair_level", "prompt_fix"))
    repair_level = raw_level if raw_level in valid_repair_levels else "prompt_fix"

    suggested_changes = raw.get("suggested_parameter_changes")
    if not isinstance(suggested_changes, dict):
        suggested_changes = None

    structural_desc = str(raw.get("structural_description", ""))

    return _make_principle(
        condition=str(raw.get("condition", "")),
        action=str(raw.get("action", "")),
        reason=str(raw.get("reason", "")),
        confidence=float(raw.get("confidence", 0.5)),
        tags=raw.get("tags") if isinstance(raw.get("tags"), list) else [],
        repair_level=repair_level,
        suggested_parameter_changes=suggested_changes,
        structural_description=structural_desc,
    )


def _dedup_exact_key(
    new_principles: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate by (condition, action) case-normalized key.

    When a match is found the existing principle is updated (higher
    confidence wins, source_run_ids are merged), and the new duplicate
    is dropped from the output.
    """
    existing_keys: dict[tuple[str, str], dict[str, Any]] = {}
    for p in existing:
        key = (
            p.get("condition", "").strip().lower(),
            p.get("action", "").strip().lower(),
        )
        existing_keys[key] = p

    deduplicated: list[dict[str, Any]] = []
    for p in new_principles:
        key = (
            p.get("condition", "").strip().lower(),
            p.get("action", "").strip().lower(),
        )
        if key in existing_keys:
            match = existing_keys[key]
            match["confidence"] = max(
                match.get("confidence", 0), p.get("confidence", 0)
            )
            match["source_run_ids"] = list(
                set(match.get("source_run_ids", []))
                | set(p.get("source_run_ids", []))
            )
            match["updated_at"] = time.time()
        else:
            existing_keys[key] = p
            deduplicated.append(p)

    return deduplicated
