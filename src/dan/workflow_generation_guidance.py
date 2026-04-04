"""Shared workflow-generation guidance for chat, repair, and codegen prompts."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal, Sequence

WorkflowGenerationSurface = Literal["build", "mutate", "review", "repair", "codegen"]

WORKFLOW_GENERATION_CONTRACT_DETAIL_ID = "prompt:workflow_generation_contract:full"

_WORKFLOW_GENERATION_CONTRACT_ENABLED_OVERRIDE: ContextVar[bool | None] = ContextVar(
    "workflow_generation_contract_enabled_override",
    default=None,
)

_WORKFLOW_CONTEXT_RE = re.compile(
    r"\b(?:workflow|graph|node|edge|port|sub-graph|subgraph|for_each|loop|composite|builder)\b",
    re.IGNORECASE,
)
_WORKFLOW_AUTHORING_RE = re.compile(
    r"\b(?:build|create|add|remove|edit|modify|update|change|rewire|replace|generate|compose)\b",
    re.IGNORECASE,
)
_WORKFLOW_REVIEW_RE = re.compile(
    r"\b(?:review|audit|inspect|check|assess|analyze|explain|understand|look\s+over)\b",
    re.IGNORECASE,
)
_WORKFLOW_REPAIR_RE = re.compile(
    r"\b(?:fix|repair|debug|patch|heal|self[-\s]?heal|recover|retry|make\s+sure\s+it\s+runs|make\s+it\s+runnable)\b",
    re.IGNORECASE,
)


def workflow_generation_contract_enabled() -> bool:
    """Return whether workflow-generation contract injection is enabled."""
    override = _WORKFLOW_GENERATION_CONTRACT_ENABLED_OVERRIDE.get()
    if override is not None:
        return override
    raw = os.environ.get(
        "DAN_WORKFLOW_GENERATION_CONTRACT_ENABLED",
        os.environ.get("DAN_WORKFLOW_GENERATION_CONTRACT", "1"),
    )
    parsed = _coerce_optional_bool(raw)
    return True if parsed is None else parsed


@contextmanager
def workflow_generation_contract_override(enabled: bool | None):
    """Temporarily override workflow-generation contract injection for this context."""
    token = _WORKFLOW_GENERATION_CONTRACT_ENABLED_OVERRIDE.set(enabled)
    try:
        yield
    finally:
        _WORKFLOW_GENERATION_CONTRACT_ENABLED_OVERRIDE.reset(token)


def coerce_workflow_generation_contract_override(value: object) -> bool | None:
    """Parse an optional bool-like override value."""
    return _coerce_optional_bool(value)


def infer_workflow_generation_surface(
    *,
    mode: str,
    user_message: str,
    allow_mutation_tool: bool = False,
    required_action_hints: Sequence[str] | None = None,
    graph_is_empty: bool = False,
) -> WorkflowGenerationSurface | None:
    """Infer whether a turn should receive workflow-generation guidance."""
    if not workflow_generation_contract_enabled():
        return None
    normalized_mode = str(mode or "agent").strip().lower()
    if normalized_mode == "auto":
        normalized_mode = "agent"

    message = str(user_message or "")
    lower_message = message.lower()
    hint_set = {str(hint or "").strip() for hint in (required_action_hints or ()) if str(hint or "").strip()}

    has_workflow_context = bool(_WORKFLOW_CONTEXT_RE.search(message))
    has_authoring_cue = bool(_WORKFLOW_AUTHORING_RE.search(message))
    has_review_cue = bool(_WORKFLOW_REVIEW_RE.search(message))
    has_repair_cue = bool(_WORKFLOW_REPAIR_RE.search(message))

    workflow_turn = (
        normalized_mode in {"build", "mutate"}
        or "workflow_edit" in hint_set
        or "workflow_run" in hint_set
        or (
            has_workflow_context
            and (has_authoring_cue or has_review_cue or has_repair_cue or "workflow_run" in hint_set)
        )
    )
    if not workflow_turn:
        return None

    if has_repair_cue or (
        normalized_mode == "debug"
        and (allow_mutation_tool or has_workflow_context or "workflow_edit" in hint_set)
    ):
        return "repair"

    if has_review_cue and (
        has_workflow_context
        or "workflow_edit" in hint_set
        or "workflow_run" in hint_set
        or "run" in lower_message
    ):
        return "review"

    if normalized_mode == "build":
        return "build"

    if graph_is_empty and (
        normalized_mode == "build"
        or "workflow_edit" in hint_set
        or (allow_mutation_tool and has_workflow_context and has_authoring_cue)
    ):
        return "build"

    if (
        normalized_mode == "mutate"
        or "workflow_edit" in hint_set
        or (allow_mutation_tool and has_workflow_context and has_authoring_cue)
    ):
        return "mutate"

    return None


def render_workflow_generation_contract(
    surface: WorkflowGenerationSurface,
    *,
    tools_available: bool = True,
) -> str:
    """Render compact workflow-generation guidance for the requested surface."""
    if not workflow_generation_contract_enabled():
        return ""
    lines: list[str] = [
        "## Workflow Generation Contract",
        _surface_intro(surface),
    ]
    lines.extend(_surface_rules(surface))
    if tools_available and surface != "codegen":
        lines.append(
            "Additional prompt detail is available via `load_prompt_detail` with "
            f"detail_id=`{WORKFLOW_GENERATION_CONTRACT_DETAIL_ID}` if you need the longer contract."
        )
    return "\n".join(line.rstrip() for line in lines if line and line.strip())


def render_workflow_generation_contract_detail() -> str:
    """Return the long-form reference body for workflow authoring."""
    return """\
### Workflow generation contract
1. Choose the right operating mode.
- Build: produce the smallest complete runnable workflow that satisfies the request.
- Mutate: preserve existing behavior and prefer the narrowest valid edit.
- Review: identify contract violations and run-readiness risks before suggesting changes.
- Repair: preserve requested behavior and fix only the mechanical issues needed for a valid runnable workflow.
- Codegen: emit builder DSL that obeys the same runtime contract as graph mutations.

2. Respect exact engine contracts.
- Use exact runtime node types, input ports, output ports, tool manifests, and schema-supported mutation operations.
- Do not invent pseudo-operations such as `dry_run`, `validate`, `inspect`, `repair`, or `connect`.
- Prompt and query templates use single-brace `{variable}` placeholders matching real input port names. Do not introduce new `{{variable}}` placeholders.
- `input` nodes expose named source ports only when `config.variables` declares them. If later edges use ports like `watchlist_path`, declare those variables on the input node first instead of assuming they exist.

3. Handle control-flow bodies correctly.
- After adding control-flow or composite nodes in graph-mutation mode, define the body with `replace_body_graph`.
- `for_each` exposes top-level `items` and `results`. Loop-local values such as `item` and `index` belong on body-graph entry nodes, not on the outer node.
- Inside a body graph, edges may connect only body-local nodes. Do not reference the enclosing `for_each` or `composite` node ID from inside `replace_body_graph`.

4. Keep code and tool nodes honest.
- Inline Python for `code_operator` or `wf.code(...)` must assign outputs through `result = ...`; do not rely on bare top-level `return`.
- Tool nodes may use only registered tool ids / known tool manifests. If no registered tool fits, use code instead of inventing a tool name.
- For brittle LLM or external-tool steps, add retry or failure-handling structure when the workflow is expected to run, rather than relying on downstream manual repair.

5. Be explicit about state and evidence.
- Say whether a workflow is only proposed, previewed, applied, tested, or known to run.
- Do not claim the workflow runs unless you actually ran it or observed a successful validation/run result.
- When reviewing, findings come first: focus on node I/O wiring, port names, control-flow body contracts, retries, and repairability.
""".strip()


def render_workflow_clarification_guidance() -> str:
    """Return a small workflow-build supplement for clarification turns."""
    if not workflow_generation_contract_enabled():
        return ""
    return """\
## Workflow Clarification Focus
- Ask only for the missing inputs, external resources or systems, and final output needed to build the smallest runnable workflow.
- Preserve any loops, approvals, reviews, or retries the user already requested instead of silently dropping them.
- Do not guess missing workflow structure.
- Do not emit a plan, mutation, or full workflow yet.
""".strip()


def render_workflow_mutation_tool_guidance(*, include_heading: bool = False) -> str:
    """Return shared usage guidance for workflow mutation/runtime tools."""
    lines: list[str] = []
    if include_heading:
        lines.append("## Workflow mutation tool")
    lines.extend([
        "`plan_graph_mutations` is the workflow-building/editing tool for the current workflow. "
        "Use it to create a workflow from scratch or add/remove/rewire/configure workflow structure. "
        "Do not claim you need primitive `create_node`, `add_edge`, or similar workflow-edit tools.",
        "When the user asks to build AND run/test the workflow in the same request, set `auto_apply: true` "
        "on `plan_graph_mutations`. If the user only asks to build or preview, omit `auto_apply` so the changes stay proposed.",
        "When the user later says to apply a previously proposed workflow preview from this chat, "
        "use `apply_last_mutation` instead of re-planning the same mutation. "
        "Do NOT use `apply_pending_overlay` for workflow previews; that tool only patches live runs.",
        "When the user wants to run or test the workflow itself, use `start_run`. "
        "For the current workflow you usually do not need to guess a workflow_id. "
        "Do NOT use `http_request` or Furnace endpoints for ordinary workflow execution.",
        "Be explicit about workflow status: `plan_graph_mutations` prepares a proposed preview/diff. "
        "Say whether the workflow is only proposed, already applied, or actually tested.",
        "For workflow deletion, first inspect the current inventory (`list_graphs` / `search_workflows`), "
        "then call `delete_graph` only for exact `graph_id` values from that fresh result. "
        "Do not batch speculative `delete_graph` calls with the inventory request.",
    ])
    return "\n".join(lines)


def _coerce_optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return None


def _surface_intro(surface: WorkflowGenerationSurface) -> str:
    return {
        "build": "- Build the smallest complete runnable workflow that satisfies the request.",
        "mutate": "- Preserve the requested behavior and prefer the narrowest valid workflow edit.",
        "review": "- Review the workflow against DAN engine contracts and state findings before patch ideas.",
        "repair": "- Preserve requested behavior and fix only the mechanical issues needed for a valid runnable result.",
        "codegen": "- Generate or repair builder DSL code that obeys the DAN runtime contract exactly.",
    }[surface]


def _surface_rules(surface: WorkflowGenerationSurface) -> list[str]:
    common = [
        "- Use exact runtime node types, port names, and schema-supported configuration. Do not invent missing ports or pseudo-capabilities.",
        "- Prompt and query templates use single-brace `{variable}` placeholders matching real input port names. Do not introduce new `{{variable}}` placeholders.",
        "- Be explicit about state: say whether the workflow is proposed, applied, tested, or only expected to run.",
        "- Add retry or failure-handling structure for brittle LLM or external-tool steps when the workflow is meant to run.",
    ]
    if surface == "codegen":
        return [
            *common,
            "- Inline Python in `wf.code(...)` must assign outputs through `result = ...`; do not rely on bare top-level `return`.",
            "- `wf.tool(..., tool_id=...)` may use only registered tool ids. If no registered tool fits, use `wf.code(...)` instead of inventing a tool id.",
            "- For composite outputs and loop outputs, use the builder DSL correctly (for example `NodeRef`) instead of inventing mutation-style body edits.",
        ]
    mutation_rules = [
        "- Use only schema-supported mutation ops such as `add_node`, `edit_node`, `add_edge`, and `replace_body_graph`. Never invent pseudo-ops such as `dry_run`, `validate`, `inspect`, `repair`, or `connect`.",
        "- If an `input` node will feed named ports such as `watchlist_path` or `topic`, declare them in `config.variables`; otherwise only the aggregate `input` port is guaranteed.",
        "- After adding `for_each`, `while_loop`, `composite`, or similar body-owning nodes, define the body with `replace_body_graph`.",
        "- `for_each` exposes top-level ports `items` and `results`; loop-local values like `item` and `index` belong on body entry nodes.",
        "- Inside `replace_body_graph`, edges may reference only nodes that exist inside that body graph; never wire body edges back to the enclosing loop/composite node id.",
    ]
    review_rules = [
        "- When reviewing, prioritize wiring correctness, missing ports, invalid body-graph edges, placeholder mistakes, retry gaps, and misleading status claims.",
    ]
    repair_rules = [
        "- Keep the requested outcome intact and do not broaden scope unless a minimal structural adjustment is required for validity.",
    ]
    if surface == "review":
        return [*common, *mutation_rules, *review_rules]
    if surface == "repair":
        return [*common, *mutation_rules, *repair_rules]
    return [*common, *mutation_rules]
