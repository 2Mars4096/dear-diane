"""Workflow Planner — LLM-driven reuse-first workflow planning.

Implements Plan 19-2: receives a high-level goal, discovers available
building blocks and relevant past workflows, and produces a declarative
plan (REUSE / ADAPT / GENERATE) compiled into a valid workflow graph.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import PurePosixPath
import re
import uuid
from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from dan.models.node_taxonomy import GENERATE_SPEC_NODE_TYPES
from dan.meta.discovery import DiscoveryResult, DiscoveryService
from dan.meta.goal_contract import render_goal_contract_section
from dan.meta.tool_catalog import render_tool_catalog_markdown
from dan.meta.workflow_contract import (
    WorkflowBuildContractReport,
    classify_run_readiness_issues,
    describe_code_readiness_issue,
    validate_workflow_build_contract,
    workflow_build_provenance,
)
from dan.workflow_generation_guidance import (
    render_workflow_generation_contract,
    workflow_generation_contract_enabled,
)

if TYPE_CHECKING:
    from dan.meta.diagnosis import DiagnosisResult, GenerationError
    from dan.models.graph import Graph

logger = logging.getLogger(__name__)

_BUILDER_CODE_HARNESS = '''\
import json, sys, pathlib, traceback

_inputs = json.loads(pathlib.Path("_inputs.json").read_text(encoding="utf-8"))
if _inputs.get("src_path"):
    sys.path.insert(0, _inputs["src_path"])

_user_code = _inputs["user_code"]
_ns = {"__builtins__": __builtins__}

try:
    _compiled = compile(_user_code, "<builder>", "exec")
    exec(_compiled, _ns)
except SyntaxError as _exc:
    pathlib.Path("_result.json").write_text(json.dumps({
        "error": {
            "type": "SyntaxError",
            "message": str(_exc),
            "line": _exc.lineno,
        }
    }), encoding="utf-8")
    sys.exit(0)
except Exception as _exc:
    _line = None
    for _frame in reversed(traceback.extract_tb(_exc.__traceback__)):
        if _frame.filename == "<builder>":
            _line = _frame.lineno
            break
    pathlib.Path("_result.json").write_text(json.dumps({
        "error": {
            "type": type(_exc).__name__,
            "message": str(_exc),
            "line": _line,
        }
    }), encoding="utf-8")
    sys.exit(0)

_graph = None
for _name in ("graph", "wf", "workflow", "g"):
    _g = _ns.get(_name)
    if _g is not None and hasattr(_g, "model_dump"):
        _graph = _g
        break

if _graph is None:
    pathlib.Path("_result.json").write_text(json.dumps({
        "error": {
            "type": "NameError",
            "message": "Builder code must assign the result of .build() to a "
                       "variable named graph, wf, workflow, or g",
            "line": None,
        }
    }), encoding="utf-8")
    sys.exit(0)

pathlib.Path("_result.json").write_text(json.dumps({
    "graph": _graph.model_dump(mode="json"),
    "source_code": _user_code,
}, default=str), encoding="utf-8")
'''

__all__ = [
    "AdaptPlan",
    "CodegenDiagnostics",
    "CodegenPromptBuilder",
    "CodegenResult",
    "ExecutionReadinessError",
    "GenerateCodePlan",
    "GeneratePlan",
    "PlanAction",
    "PlanReview",
    "PlanResult",
    "PlannerOutput",
    "PlanningPromptBuilder",
    "ReusePlan",
    "ValidationResult",
    "WorkflowPlanner",
    "validate_codegen_output",
]

_GENERATE_SPEC_NODE_TYPES_LITERAL = '"|"'.join(GENERATE_SPEC_NODE_TYPES)


# ---------------------------------------------------------------------------
# Codegen result
# ---------------------------------------------------------------------------


@dataclass
class CodegenResult:
    """Structured outcome of running LLM-generated builder code in the sandbox."""

    success: bool
    graph: dict | None = None
    source_code: str = ""
    error_type: str | None = None
    error_message: str | None = None
    error_line: int | None = None


@dataclass
class ValidationResult:
    """Structured outcome of running the full validation pipeline on a generated graph."""

    success: bool
    graph: Graph | None = None
    errors: list[GenerationError] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)
    recoverable_errors: list[GenerationError] = dc_field(default_factory=list)
    fatal_errors: list[GenerationError] = dc_field(default_factory=list)
    run_ready: bool = True
    run_readiness_failure_mode: str | None = None
    contract_report: WorkflowBuildContractReport | None = None


@dataclass
class CodegenDiagnostics:
    """Structured diagnostics for a failed codegen attempt.

    Carries enough context for logging, UI display, and legacy fallback
    decisions without exposing raw exceptions.
    """

    attempts: int
    errors: list[GenerationError] = dc_field(default_factory=list)
    final_code: str = ""
    validation_result: ValidationResult | None = None
    diagnosis_result: DiagnosisResult | None = None


class ExecutionReadinessError(ValueError):
    """Raised when a graph is structurally valid but still not safe to execute."""


_LEGACY_GENERATE_FALLBACK = True

_EXPLICIT_PATH_RE = re.compile(
    r"""(?:(?<=^)|(?<=[\s"'`(]))(?P<path>(?:~|/|\.{1,2})?[\w./-]+(?:/|(?:\.[A-Za-z0-9]{1,8})))(?=$|[\s"'`,;:.)])"""
)
_TEXT_LIKE_SUFFIXES = {
    ".json",
    ".md",
    ".markdown",
    ".txt",
    ".py",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
    ".tsv",
}
_PATH_SUFFIXES_BY_TOOL: dict[str, set[str]] = {
    "csv_read": {".csv", ".tsv"},
    "spreadsheet_read": {".xlsx", ".xls", ".xlsm"},
    "pdf_read": {".pdf"},
    "file_read": _TEXT_LIKE_SUFFIXES,
}
_DIRECTORY_PATH_TOOLS = {"list_directory"}
_PATH_CONFIG_KEY_BY_TOOL: dict[str, str] = {
    "csv_read": "path",
    "file_read": "path",
    "file_write": "path",
    "list_directory": "path",
    "pdf_read": "path",
    "spreadsheet_read": "path",
}


def _extract_explicit_paths_from_text(text: str | None) -> list[str]:
    if not text:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _EXPLICIT_PATH_RE.finditer(text):
        raw = str(match.group("path") or "").strip()
        if not raw:
            continue
        path = raw.rstrip(".,;:")
        if "/" not in path and "." not in PurePosixPath(path).name:
            continue
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def _hint_tokens(*parts: str) -> set[str]:
    tokens: set[str] = set()
    for part in parts:
        for token in re.split(r"[^a-z0-9]+", str(part or "").lower()):
            if len(token) >= 3:
                tokens.add(token)
    return tokens


def _candidate_paths_for_tool(tool_id: str, paths: list[str]) -> list[str]:
    if tool_id in _DIRECTORY_PATH_TOOLS:
        return [path for path in paths if path.endswith("/")]

    suffixes = _PATH_SUFFIXES_BY_TOOL.get(tool_id)
    candidates: list[str] = []
    for path in paths:
        if path.endswith("/"):
            continue
        suffix = PurePosixPath(path).suffix.lower()
        if suffixes is None or suffix in suffixes:
            candidates.append(path)
    return candidates


def _score_path_candidate(node: dict[str, Any], tool_id: str, path: str) -> int:
    basename = PurePosixPath(path.rstrip("/")).name
    stem = PurePosixPath(basename).stem.lower()
    suffix = PurePosixPath(basename).suffix.lower()
    hint_tokens = _hint_tokens(
        str(node.get("id") or ""),
        str(node.get("name") or ""),
        str(node.get("description") or ""),
    )
    path_tokens = _hint_tokens(stem)

    score = 0
    if stem and stem in " ".join(sorted(hint_tokens)):
        score += 4
    overlap = hint_tokens & path_tokens
    score += 3 * len(overlap)
    preferred_suffixes = _PATH_SUFFIXES_BY_TOOL.get(tool_id)
    if preferred_suffixes and suffix in preferred_suffixes:
        score += 2
    if tool_id == "file_read" and suffix in {".json", ".md", ".txt", ".py"}:
        score += 1
    return score


def _bind_explicit_tool_paths(
    graph_data: dict[str, Any],
    *,
    user_text: str | None,
) -> dict[str, Any]:
    paths = _extract_explicit_paths_from_text(user_text)
    if not paths:
        return graph_data

    nodes = graph_data.get("nodes")
    if not isinstance(nodes, list):
        return graph_data

    for node in nodes:
        if not isinstance(node, dict) or node.get("node_type") != "tool_operator":
            continue
        tool_id = str(node.get("tool_id") or "").strip()
        config_key = _PATH_CONFIG_KEY_BY_TOOL.get(tool_id)
        if not config_key:
            continue

        tool_config = node.get("tool_config")
        if not isinstance(tool_config, dict):
            tool_config = {}
            node["tool_config"] = tool_config
        if str(tool_config.get(config_key) or "").strip():
            continue

        candidates = _candidate_paths_for_tool(tool_id, paths)
        if not candidates:
            continue
        if len(candidates) == 1:
            tool_config[config_key] = candidates[0]
            continue

        scored = [
            (_score_path_candidate(node, tool_id, candidate), candidate)
            for candidate in candidates
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_candidate = scored[0]
        if best_score <= 0:
            continue
        if len(scored) > 1 and scored[1][0] == best_score:
            continue
        tool_config[config_key] = best_candidate

    return graph_data


def _parse_codegen_result(
    result: Any,
    structured: Any,
    source_code: str = "",
) -> CodegenResult:
    """Module-level wrapper for ``WorkflowPlanner._parse_codegen_result``.

    Used by ``ChatManager._sandbox_exec_builder_code`` to avoid reaching
    into the class method directly.
    """
    return WorkflowPlanner._parse_codegen_result(result, structured, source_code)


def validate_codegen_output(graph_dict: dict) -> ValidationResult:
    """Run the full validation pipeline on a generated graph dict.

    Steps:
    1. Graph.model_validate() — schema compliance
    2. validate_graph() — design-time checks
    3. Classify errors as recoverable vs fatal
    4. Return structured ValidationResult
    """
    from dan.meta.diagnosis import (
        GenerationError,
        GenerationErrorType,
        GenerationStage,
    )

    contract_report = validate_workflow_build_contract(graph_dict, apply_repairs=True)
    errors: list[GenerationError] = []
    for issue in contract_report.errors:
        error_type = GenerationErrorType.build_error
        if issue.category == "schema":
            error_type = GenerationErrorType.schema_mismatch
        elif issue.category == "port_endpoint":
            error_type = GenerationErrorType.edge_endpoint
        elif issue.category == "node_identity":
            error_type = (
                GenerationErrorType.duplicate_node
                if "duplicate node id" in issue.message.lower()
                else GenerationErrorType.build_error
            )
        elif issue.category in {"entry_exit", "run_readiness"}:
            error_type = (
                GenerationErrorType.cycle
                if "cycle" in issue.message.lower()
                else GenerationErrorType.reachability
            )
        elif issue.category == "subgraph":
            error_type = GenerationErrorType.missing_edge_target
        errors.append(
            GenerationError(
                stage=GenerationStage.validation,
                error_type=error_type,
                message=issue.message,
                artifact_id=issue.artifact_id,
                recoverable=issue.severity != "fatal",
            )
        )

    recoverable = [e for e in errors if e.recoverable]
    fatal = [e for e in errors if not e.recoverable]

    return ValidationResult(
        success=len(fatal) == 0 and len(recoverable) == 0,
        graph=contract_report.graph if len(fatal) == 0 else None,
        errors=errors,
        warnings=list(contract_report.warnings),
        recoverable_errors=recoverable,
        fatal_errors=fatal,
        run_ready=contract_report.run_ready,
        run_readiness_failure_mode=classify_run_readiness_issues(
            contract_report.run_readiness_issues
        ),
        contract_report=contract_report,
    )


def _format_run_readiness_issues(
    issues: list[str],
    failure_mode: str | None = None,
) -> list[str]:
    if not failure_mode:
        return list(issues)
    return [f"[{failure_mode}] {issue}" for issue in issues]


def _intent_has_code_execution_stage(intent: Any | None) -> bool:
    if intent is None:
        return False
    for stage in list(getattr(intent, "stages", []) or []):
        stage_type = getattr(stage, "stage_type", None)
        stage_type_value = getattr(stage_type, "value", stage_type)
        if str(stage_type_value or "").strip() == "code_execution":
            return True
    return False


def _intent_code_execution_issues(intent: Any | None) -> list[str]:
    if intent is None:
        return []
    issues: list[str] = []
    for stage in list(getattr(intent, "stages", []) or []):
        stage_type = getattr(stage, "stage_type", None)
        stage_type_value = getattr(stage_type, "value", stage_type)
        if str(stage_type_value or "").strip() != "code_execution":
            continue
        config = getattr(stage, "config", {}) or {}
        code = config.get("code") if isinstance(config, dict) else ""
        stage_name = str(getattr(stage, "name", "") or "code_stage")
        issue = describe_code_readiness_issue(
            code,
            artifact_label=f"Code stage '{stage_name}'",
        )
        if issue:
            issues.append(issue)
    return issues


def _honest_codegen_failure_message(
    plan: "GenerateCodePlan",
    exc: BaseException,
) -> str | None:
    if not _intent_has_code_execution_stage(getattr(plan, "intent", None)):
        return None

    stage_issues = _intent_code_execution_issues(getattr(plan, "intent", None))
    if stage_issues:
        details = "; ".join(stage_issues[:5])
        return (
            "Code-heavy workflow could not be downgraded to a simplified fallback "
            f"because the required code stages remain unresolved or non-runnable: {details}"
        )

    stage_names = [
        str(getattr(stage, "name", "") or "code_stage")
        for stage in list(getattr(getattr(plan, "intent", None), "stages", []) or [])
        if str(getattr(getattr(stage, "stage_type", None), "value", getattr(stage, "stage_type", None)) or "").strip()
        == "code_execution"
    ]
    rendered_names = ", ".join(stage_names[:5]) or "code_execution"
    return (
        "Code-heavy workflow generation failed and cannot fall back to a "
        f"simplified non-code graph. Required code stage(s): {rendered_names}. "
        f"Upstream failure: {exc}"
    )


# ---------------------------------------------------------------------------
# Plan models
# ---------------------------------------------------------------------------


class PlanAction(str, Enum):
    REUSE = "REUSE"
    ADAPT = "ADAPT"
    GENERATE = "GENERATE"


class ReusePlan(BaseModel):
    """Use an existing workflow as-is with input substitution."""

    action: Literal["REUSE"] = "REUSE"
    workflow_id: str
    input_mapping: dict[str, str] = Field(default_factory=dict)


class AdaptPlan(BaseModel):
    """Modify an existing workflow via structured mutations."""

    action: Literal["ADAPT"] = "ADAPT"
    workflow_id: str
    mutations: list[dict[str, Any]] = Field(default_factory=list)
    input_mapping: dict[str, str] = Field(default_factory=dict)


class GeneratePlan(BaseModel):
    """Generate a new workflow from a structured specification."""

    action: Literal["GENERATE"] = "GENERATE"
    spec: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class GenerateCodePlan(BaseModel):
    """Generate a new workflow via executable builder DSL code.

    Only available when ``planner_allow_code_generation=True``.
    The code is executed in a subprocess sandbox.
    """

    action: Literal["GENERATE_CODE"] = "GENERATE_CODE"
    code: str = ""
    description: str = ""
    intent: Any | None = None


PlanResult = ReusePlan | AdaptPlan | GeneratePlan | GenerateCodePlan


class PlanReview(BaseModel):
    """Validation results and confidence assessment for a plan."""

    valid: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    estimated_cost: float | None = None
    confidence: float = 0.5


class PlannerOutput(BaseModel):
    """Combined plan result and review."""

    plan: ReusePlan | AdaptPlan | GeneratePlan | GenerateCodePlan
    review: PlanReview = Field(default_factory=PlanReview)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class PlanningPromptBuilder:
    """Builds system and user prompts for the planning LLM."""

    SYSTEM_TEMPLATE = """\
You are a workflow planner for the DAN (Deep Agent Network) system.

Your job is to take a user's goal and produce ONE of three actions as a JSON object:

1. REUSE — use an existing workflow as-is:
   {"action": "REUSE", "workflow_id": "<id>", "input_mapping": {"<src>": "<dst>"}}

2. ADAPT — modify an existing workflow with mutations:
   {"action": "ADAPT", "workflow_id": "<id>", "mutations": [...], "input_mapping": {...}}
   Mutations are GraphMutator operations:
   - {"op": "add_node", "node_type": "llm_operator", "name": "...", "config": {...}}
   - {"op": "edit_node", "node_id": "...", "updates": {...}}
   - {"op": "remove_node", "node_id": "..."}
   - {"op": "add_edge", "edge_type": "data", "source_id": "...", "source_port": "<real source port>", "target_id": "...", "target_port": "<real target port>"}
   - {"op": "remove_edge", "source_id": "...", "source_port": "...", "target_id": "...", "target_port": "..."}
   Common output ports: `llm_operator.text`, `tool_operator.result`, `code_operator.result`, `router.route`.
   For branching/control-flow edges, always specify explicit ports instead of assuming defaults.

3. GENERATE — create a new workflow from scratch:
   {"action": "GENERATE", "description": "...", "spec": {"nodes": [...], "edges": [...]}}
   Each node: {"node_type": "__GENERATE_SPEC_NODE_TYPES__", "name": "...", "config": {...}}
   Each edge: {"source": "<node_name>", "target": "<node_name>"} for simple linear flows, or {"source": "<node_name>", "source_port": "...", "target": "<node_name>", "target_port": "..."} when a control-flow node or non-default port is involved.
   GENERATE is intentionally the lightweight/simple path. Do not invent advanced runtime primitives such as `goal_loop`, `vote`, `reflection`, `agent_team`, or `orchestrator` inside this spec.

Decision policy:
- If a similar workflow exists with reuse_fit_score >= 0.8, prefer REUSE.
- If 0.4 <= reuse_fit_score < 0.8, prefer ADAPT.
- Otherwise, GENERATE.

## Examples

### Example 1 — REUSE
Goal: "Write a literature review on supply chain resilience"
Similar workflows: paper_writing (fit=0.92, success_rate=80%)
Output:
{"action": "REUSE", "workflow_id": "paper_writing", "input_mapping": {"topic": "supply chain resilience"}}

### Example 2 — ADAPT
Goal: "Analyze quarterly earnings and produce a beamer presentation"
Similar workflows: equity_research (fit=0.55, success_rate=75%)
Output:
{"action": "ADAPT", "workflow_id": "equity_research", "mutations": [{"op": "add_node", "node_type": "llm_operator", "name": "beamer_formatter", "config": {"system_prompt": "Convert analysis into beamer LaTeX slides"}}, {"op": "add_edge", "edge_type": "data", "source_id": "analysis", "source_port": "text", "target_id": "beamer_formatter", "target_port": "input"}], "input_mapping": {"ticker": "AAPL"}}

### Example 3 — GENERATE
Goal: "Build a RAG QA system for internal docs"
Similar workflows: none
Output:
{"action": "GENERATE", "description": "RAG-based question answering pipeline", "spec": {"nodes": [{"node_type": "tool_operator", "name": "load_docs", "config": {"tool_id": "file_read"}}, {"node_type": "llm_operator", "name": "retriever", "config": {"system_prompt": "Retrieve relevant passages for the question"}}, {"node_type": "llm_operator", "name": "answerer", "config": {"system_prompt": "Answer the question using retrieved passages"}}], "edges": [{"source": "load_docs", "target": "retriever"}, {"source": "retriever", "target": "answerer"}]}}

Output ONLY a single valid JSON object. No markdown, no explanation."""

    def build_system_prompt(self) -> str:
        """Return the base planning system prompt."""
        system_prompt = self.SYSTEM_TEMPLATE.replace(
            "__GENERATE_SPEC_NODE_TYPES__",
            _GENERATE_SPEC_NODE_TYPES_LITERAL,
        )
        if not workflow_generation_contract_enabled():
            return system_prompt
        return (
            f"{system_prompt}\n\n"
            f"{render_workflow_generation_contract('build', tools_available=False)}\n\n"
            f"{render_workflow_generation_contract('mutate', tools_available=False)}"
        )

    def build_user_prompt(
        self,
        goal: str,
        discoveries: DiscoveryResult,
        error_context: str | None = None,
        plan_context: dict[str, Any] | None = None,
    ) -> str:
        """Format the user's goal and discovery context into a prompt."""
        sections = [f"## Goal\n{goal}"]

        if discoveries.workflows:
            lines = []
            for w in discoveries.workflows:
                sr = f"{w.success_rate:.0%}" if w.success_rate is not None else "n/a"
                lines.append(
                    f"- {w.workflow_id} (name={w.name}, fit={w.reuse_fit_score:.2f}, "
                    f"success_rate={sr})"
                )
                if w.description:
                    lines.append(f"  {w.description[:200]}")
            sections.append("## Similar Past Workflows\n" + "\n".join(lines))

        if discoveries.tools:
            sections.append(
                "## Available Tools\n" + ", ".join(t.tool_id for t in discoveries.tools)
            )

        if discoveries.skills:
            sections.append(
                "## Available Skills\n" + ", ".join(s.name for s in discoveries.skills)
            )

        if discoveries.patterns:
            sections.append(
                "## Available Patterns\n"
                + ", ".join(f"{p.name}" for p in discoveries.patterns)
            )

        if error_context:
            sections.append(f"## Error Context from Prior Attempt\n{error_context}")

        if plan_context:
            goal_contract_section = render_goal_contract_section(
                plan_context.get("goal_contract"),
                preamble=(
                    "Use this contract when choosing REUSE vs ADAPT vs GENERATE and "
                    "when checking whether the planned workflow is actually complete."
                ),
            )
            if goal_contract_section:
                sections.append(goal_contract_section)

            ctx_lines: list[str] = []
            if plan_context.get("adapt_workflow_id"):
                ctx_lines.append(
                    f"ADAPT REQUIRED: You MUST use action ADAPT with workflow_id=\"{plan_context['adapt_workflow_id']}\". "
                    "The user chose to adapt this workflow. Produce mutations to bridge the gap between the "
                    "existing workflow and the goal."
                )
            if plan_context.get("required_tools"):
                ctx_lines.append(
                    f"Required tools: {', '.join(plan_context['required_tools'])}"
                )
            if plan_context.get("required_skills"):
                ctx_lines.append(
                    f"Required skills: {', '.join(plan_context['required_skills'])}"
                )
            if plan_context.get("inputs"):
                ctx_lines.append(
                    f"Inputs: {plan_context['inputs']}"
                )
            if plan_context.get("outputs"):
                ctx_lines.append(
                    f"Outputs: {plan_context['outputs']}"
                )
            if ctx_lines:
                sections.append("## Plan Constraints\n" + "\n".join(ctx_lines))
            calibration_hints = plan_context.get("calibration_hints")
            if calibration_hints:
                sections.append(
                    "## Experience-Based Calibration\n"
                    + "\n".join(f"- {h}" for h in calibration_hints)
                )

        if discoveries.self_knowledge_formatted:
            sections.append(discoveries.self_knowledge_formatted)
        elif discoveries.self_knowledge_chunks:
            sk_lines: list[str] = []
            for chunk in discoveries.self_knowledge_chunks:
                source = chunk.get("source_file", "unknown")
                section = chunk.get("section_title", "")
                header = f"[Source: {source}"
                if section:
                    header += f" > {section}"
                header += "]"
                sk_lines.append(f"{header}\n{chunk.get('text', '')}")
            sections.append(
                "## DAN API Reference (retrieved)\n" + "\n\n".join(sk_lines)
            )

        return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Codegen prompt construction
# ---------------------------------------------------------------------------


class CodegenPromptBuilder:
    """Builds prompts that teach an LLM to generate ``dan.builder`` Python code.

    Unlike ``PlanningPromptBuilder`` (which emits JSON mutation plans), this
    builder produces prompts for writing executable builder DSL scripts that
    call ``wf.build()`` and yield a validated ``Graph``.
    """

    _SYSTEM_PROMPT = """\
You are a workflow engineer for DAN (Deep Agent Network). \
Given a goal, write Python code using the ``dan.builder`` DSL that constructs \
a workflow and calls ``build()``.

## Builder DSL Reference

```python
from dan.builder import workflow
from dan.builder.refs import NodeRef
from dan.models.context import MergeStrategy, CompactionRule, CompactionStrategy, FailurePolicy

wf = workflow("name", description="...", tags=[...])
```

### Node creation methods

| Method | Purpose |
|--------|---------|
| `wf.llm(id, prompt=..., system_prompt=..., model=..., output_schema=..., input_ports=[...], output_ports=[...])` | LLM call. Default output port: `text` (or schema fields). |
| `wf.code(id, code=..., input_ports=[...], output_ports=[...])` | Python sandbox. Code must set `result`. |
| `wf.tool(id, tool_id=..., tool_config={}, input_ports=[...], output_ports=[...])` | Registered tool call. Default output: `result`. |
| `wf.gate(id, gate_mode="if_else"\\|"while", condition=..., max_iterations=...)` | Conditional/loop gate. |
| `wf.rag(id, collection=..., top_k=..., input_ports=[...])` | Vector retrieval. Output: `chunks`. |
| `wf.validator(id, rules=[...], on_invalid=...)` | Data validation with rule routing. Output: `valid`. |
| `wf.reduce(id, reducer=...)` | Fan-in aggregation. |
| `wf.router(id, route_descriptions={...})` | LLM-powered routing. |
| `wf.human(id, prompt=..., render_mode=..., output_schema=...)` | Canonical human interaction node. Legacy alias: `wf.human_in_the_loop(...)`. |
| `wf.vote(id, prompt=..., candidates=[...], strategy=...)` | Ensemble / winner-selection node. Alias: `wf.ensemble(...)`. |

### Sub-graph context managers

```python
# ForEach — parallel fan-out over a list
with wf.for_each("id", items=node["port"], parallelism=3,
                  merge_strategy=MergeStrategy.APPEND) as body:
    body.llm("step", prompt="Process: {item}")

# WhileLoop — iterate until condition is false
with wf.while_loop("id", condition="expr", max_iterations=5,
                    input_ports=[...], output_ports=[...]) as body:
    body.llm("step", prompt="...")

# Composite — reusable sub-graph
with wf.composite("id", input_mappings={...}, output_mappings={...}) as sub:
    a = sub.llm("a", prompt="...")
    b = sub.llm("b", prompt=f"Continue: {a}")
    a >> b

# ParallelSubagents — heterogeneous parallel branches
with wf.parallel_subagents("id", parallelism=2,
                           merge_strategy=MergeStrategy.APPEND) as parallel:
    with parallel.branch("b1") as sub:
        sub.llm("s1", prompt="...")
    with parallel.branch("b2") as sub:
        sub.llm("s2", prompt="...")

# Orchestrator — async coordinator with concurrent teams
with wf.orchestrator("id", completion_condition="all_done") as orch:
    with orch.team("t1") as sub:
        sub.llm("s1", prompt="...")

# Agent Team — peer conversation with named member sub-graphs
with wf.team("id", moderator_prompt="Coordinate the specialists") as team:
    with team.agent("researcher") as sub:
        sub.llm("s1", prompt="...")
    with team.agent("writer") as sub:
        sub.llm("s2", prompt="...")
```

### Convenience methods (prefer these for common patterns)

```python
# Chain — linear sequence of LLM nodes (auto-wires >> between steps)
result = wf.chain(("step1", "prompt1"), ("step2", "prompt2"), ("step3", "prompt3"))

# Review loop — writer + reviewer with while_loop in one call
result = wf.review_loop(
    writer_prompt="Write about {topic}",
    reviewer_prompt="Review for quality",
    name="review", max_rounds=3,
    # Optional: condition="citations_valid == true",
    # review_fields={"citations_valid": {"type": "boolean"}, "issues": {"type": "string"}},
    # feedback_key="issues",
)

# Map-reduce — fan-out + reduce
result = wf.map_reduce(
    items_expr=gen["items"],   # PortRef to items
    map_prompt="Process each item",
    reduce_prompt="Synthesize all results",
    parallelism=3,
)

# Tool chain — mixed LLM and tool nodes
result = wf.tool_chain(
    ("search", "web_search", {}),        # tool node
    ("analyze", None, "Analyze results"), # LLM node (tool_id=None)
    ("save", "file_write", {"path": "out.md"}),
)

# Branch — conditional if/else with two LLM branches
gate_ref, then_ref, else_ref = wf.branch(
    condition="sentiment > 0.5",
    then_prompt="Summarize the positive findings",
    else_prompt="Draft an alert about negative sentiment",
    name="sentiment_check",
)
# Wire upstream into gate_ref; downstream from then_ref / else_ref

# Pipeline operator (alias for >>)
a | b | c  # equivalent to a >> b >> c
```

### Edge wiring

```python
a >> b >> c                           # chain operator (default ports)
a | b | c                             # pipeline operator (same as >>)
b = wf.llm("b", prompt=f"Use: {a}")  # f-string magic (auto-creates edge)
wf.edge(a["port"], b["port"])         # explicit port wiring
```

### Referencing composite outputs

```python
ref = NodeRef("for_each_id", "for_each", wf)
wf.edge(ref["results"], downstream["input"])
```

### Finalize

```python
graph = wf.build()  # -> validated Graph
```

## Rules

1. Always call ``wf.build()`` and assign to ``graph``.
2. Prefer convenience methods (``chain``, ``review_loop``, ``map_reduce``, ``tool_chain``, ``branch``) \
for common patterns. Use low-level ``wf.llm()`` + ``>>`` only for non-linear topologies.
3. Use explicit ``input_ports`` and ``output_ports`` on nodes.
4. Wire edges explicitly with ``wf.edge()`` for non-trivial data flow.
5. Use ``NodeRef`` to reference outputs of ``for_each``, ``while_loop``, and other composite nodes.
6. Prompt templates use ``{variable}`` placeholders matching input port names.
7. For ``wf.code()`` nodes, write real runnable Python that derives outputs from inputs. Do not use placeholder status payloads such as ``{"status": "placeholder"}`` or fabricated markers like ``"computed"`` in place of real logic.
8. When the goal includes a concrete local file or directory path, pass that literal path via ``tool_config`` (for example ``{"path": "tests/data.csv"}``) instead of leaving required tool arguments unwired.
9. Output ONLY Python code. No markdown fences, no explanation.

## Default-wiring guidance

Unless the user explicitly asks for a minimal/simple workflow:
- Add retry policies to LLM nodes for production workflows (use node config).
- For workflows with a clear final output, consider adding a validation step.
- For long-form content (reports, papers, memos), wrap in a review loop.
- Recognize suppression signals: "simple", "minimal", "without review", "no validation" → skip those defaults.

## Available Tools

When creating tool nodes with `wf.tool(id, tool_id=...)`, use ONLY these registered tool_ids:

__AVAILABLE_TOOLS__

Do NOT invent tool_ids not in this list. If no registered tool matches the needed capability, use `wf.code()` with inline Python instead of `wf.tool()` with a made-up tool_id.

__DOMAIN_CONTEXT__"""

    _FEW_SHOT_EXAMPLES = '''\
## Example 1: 3-node chain (LLM → LLM → Code)
```
from dan.builder import workflow

wf = workflow("simple_chain")
ideas = wf.llm("idea_gen", prompt="Generate ideas about: {topic}",
                input_ports=[{"name": "topic"}])
expand = wf.llm("expand", prompt="Pick the best idea and summarize:\\n{text}",
                 output_schema={"type": "object",
                     "properties": {"best_idea": {"type": "string"}, "summary": {"type": "string"}},
                     "required": ["best_idea", "summary"]},
                 input_ports=[{"name": "text"}])
analyze = wf.code("analyze",
                   code="words = summary.split()\\nresult = {\\"word_count\\": len(words)}",
                   input_ports=[{"name": "summary"}],
                   output_ports=[{"name": "word_count"}])
wf.edge(ideas["text"], expand["text"])
wf.edge(expand["summary"], analyze["summary"])
graph = wf.build()
```

## Example 2: Review loop (WhileLoop)
```
from dan.builder import workflow
from dan.builder.refs import NodeRef
from dan.models.context import CompactionRule, CompactionStrategy, FailurePolicy

wf = workflow("review_revise")
draft = wf.llm("draft", prompt="Write a paragraph about: {topic}",
                input_ports=[{"name": "topic"}])
init = wf.code("init_state",
               code='result = {"draft": text, "quality_score": 0, "feedback": "Initial."}',
               input_ports=[{"name": "text"}],
               output_ports=[{"name": "draft"}, {"name": "quality_score"}, {"name": "feedback"}])
wf.edge(draft["text"], init["text"])

with wf.while_loop("review_loop", condition="quality_score < 8", max_iterations=3,
                    compaction=CompactionRule(strategy=CompactionStrategy.SLIDING_WINDOW, window_size=2),
                    failure_policy=FailurePolicy(max_iterations=3, stagnation_threshold=2),
                    input_ports=[{"name": "draft"}, {"name": "quality_score"}, {"name": "feedback"}],
                    output_ports=[{"name": "draft"}, {"name": "quality_score"}, {"name": "feedback"}]) as body:
    review = body.llm("review", prompt="Review:\\n{draft}\\nRate 1-10 and give feedback.",
                       output_schema={"type": "object",
                           "properties": {"quality_score": {"type": "integer"}, "feedback": {"type": "string"}},
                           "required": ["quality_score", "feedback"]},
                       input_ports=[{"name": "draft"}])
    revise = body.llm("revise", prompt="Revise draft:\\n{draft}\\nFeedback: {feedback}",
                       input_ports=[{"name": "draft"}, {"name": "feedback"}])
    body.edge(review["feedback"], revise["feedback"])

loop_ref = NodeRef("review_loop", "while_loop", wf)
wf.edge(init["draft"], loop_ref["draft"])
wf.edge(init["quality_score"], loop_ref["quality_score"])
wf.edge(init["feedback"], loop_ref["feedback"])
graph = wf.build()
```

## Example 3: Fan-out (ForEach)
```
from dan.builder import workflow
from dan.builder.refs import NodeRef
from dan.models.context import MergeStrategy

wf = workflow("fan_out")
gen = wf.llm("gen_subtopics", prompt="List 4 subtopics for: {topic}",
             output_schema={"type": "object",
                 "properties": {"subtopics": {"type": "array", "items": {"type": "string"}}},
                 "required": ["subtopics"]},
             input_ports=[{"name": "topic"}])

with wf.for_each("research", items=gen["subtopics"], parallelism=3,
                  merge_strategy=MergeStrategy.APPEND) as body:
    body.llm("summarize", prompt="Summarize: {item}",
             input_ports=[{"name": "item"}, {"name": "index"}])

ref = NodeRef("research", "for_each", wf)
aggregate = wf.code("aggregate",
                     code='result = {"report": "\\n".join(str(r) for r in results)}',
                     input_ports=[{"name": "results"}],
                     output_ports=[{"name": "report"}])
wf.edge(ref["results"], aggregate["results"])
graph = wf.build()
```

## Example 4: RAG Q&A (Tool → Tool → LLM)
```
from dan.builder import workflow

wf = workflow("rag_qa")
read = wf.tool("read_file", tool_id="file_read",
               input_ports=[{"name": "path"}],
               output_ports=[{"name": "content"}])
chunk = wf.tool("chunk", tool_id="text_chunk",
                input_ports=[{"name": "text"}, {"name": "chunk_size"}],
                output_ports=[{"name": "chunks"}])
wf.edge(read["content"], chunk["text"])

answer = wf.llm("answer",
                 prompt="Answer based on context:\\nQuestion: {question}\\nContext: {chunks}",
                 output_schema={"type": "object",
                     "properties": {"answer": {"type": "string"}, "confidence": {"type": "string"}},
                     "required": ["answer", "confidence"]},
                 input_ports=[{"name": "question"}, {"name": "chunks"}])
wf.edge(chunk["chunks"], answer["chunks"])
graph = wf.build()
```

## Example 5: While loop with gate condition
```
from dan.builder import workflow
from dan.builder.refs import NodeRef

wf = workflow("iterative_improve")
seed = wf.llm("seed", prompt="Draft an outline for: {topic}",
              input_ports=[{"name": "topic"}])
init = wf.code("init",
               code='result = {"draft": text, "score": 0.0}',
               input_ports=[{"name": "text"}],
               output_ports=[{"name": "draft"}, {"name": "score"}])
wf.edge(seed["text"], init["text"])

with wf.while_loop("improve", condition="score < 0.9", max_iterations=5,
                    input_ports=[{"name": "draft"}, {"name": "score"}],
                    output_ports=[{"name": "draft"}, {"name": "score"}]) as body:
    body.llm("refine", prompt="Improve draft:\\n{draft}\\nReturn JSON with draft and score.",
             output_schema={"type": "object",
                 "properties": {"draft": {"type": "string"}, "score": {"type": "number"}},
                 "required": ["draft", "score"]},
             input_ports=[{"name": "draft"}])

ref = NodeRef("improve", "while_loop", wf)
wf.edge(init["draft"], ref["draft"])
wf.edge(init["score"], ref["score"])
graph = wf.build()
```

## Example 6: Composite sub-graph
```
from dan.builder import workflow

wf = workflow("with_composite")
source = wf.llm("source", prompt="Describe topic: {topic}",
                 input_ports=[{"name": "topic"}])

with wf.composite("research_block",
                   input_mappings={"topic": "inner_topic"},
                   output_mappings={"inner_result": "result"}) as sub:
    s1 = sub.llm("search", prompt="Research: {inner_topic}",
                  input_ports=[{"name": "inner_topic"}])
    s2 = sub.llm("synthesize", prompt=f"Synthesize findings: {s1}",
                  input_ports=[{"name": "text"}])
    s1 >> s2

wf.edge(source["text"], wf._nodes["research_block"]["topic"])
graph = wf.build()
```

## Example 7: Convenience methods (preferred for common patterns)
```
from dan.builder import workflow

# Chain + review loop using convenience methods
wf = workflow("research_with_review")
result = wf.chain(
    ("research", "Research the topic: {topic}"),
    ("outline", "Create an outline from findings"),
    ("draft", "Write a draft from the outline"),
)
final = wf.review_loop(
    writer_prompt="Improve the draft based on feedback",
    reviewer_prompt="Review for clarity and completeness",
    max_rounds=3,
)
result >> final
graph = wf.build()
```

## Example 8: Tool chain + map-reduce
```
from dan.builder import workflow

wf = workflow("parallel_analysis")
search = wf.tool("search", tool_id="web_search",
                  input_ports=[{"name": "query"}])
result = wf.map_reduce(
    items_expr=search["result"],
    map_prompt="Summarize this source",
    reduce_prompt="Synthesize all summaries into a unified report",
    parallelism=3,
)
graph = wf.build()
```'''

    def build_system_prompt(self, *, domain_context: str = "") -> str:
        """Return the system prompt that teaches the builder DSL."""
        system_prompt = (
            self._SYSTEM_PROMPT
            .replace("__AVAILABLE_TOOLS__", render_tool_catalog_markdown())
            .replace("__DOMAIN_CONTEXT__", domain_context)
        )
        if not workflow_generation_contract_enabled():
            return system_prompt
        return (
            f"{system_prompt}\n\n"
            f"{render_workflow_generation_contract('codegen', tools_available=False)}"
        )

    def build_few_shot_examples(self) -> str:
        """Return compact few-shot examples covering 6 key workflow patterns."""
        return self._FEW_SHOT_EXAMPLES

    def build_user_prompt(
        self,
        goal: str,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        error_context: str | None = None,
        constraints: dict | None = None,
        self_knowledge_chunks: str | None = None,
    ) -> str:
        """Format the user request into a structured prompt.

        Parameters
        ----------
        goal:
            Natural-language description of the desired workflow.
        tools:
            Available tool IDs the workflow may use.
        skills:
            Available skill names.
        error_context:
            Structured error from a prior failed attempt (for retry).
        constraints:
            Dict with optional ``inputs``, ``outputs``, or other requirements.
        self_knowledge_chunks:
            Pre-formatted RAG chunks from ``SelfKnowledgeIndex``.
        """
        sections: list[str] = [f"## Goal\n{goal}"]

        if tools:
            sections.append(f"## Available Tools\n{', '.join(tools)}")

        if skills:
            sections.append(f"## Available Skills\n{', '.join(skills)}")

        if constraints:
            constraint_lines: list[str] = []
            if constraints.get("inputs"):
                constraint_lines.append(f"Required inputs: {constraints['inputs']}")
            if constraints.get("outputs"):
                constraint_lines.append(f"Required outputs: {constraints['outputs']}")
            for key, val in constraints.items():
                if key not in ("inputs", "outputs"):
                    constraint_lines.append(f"{key}: {val}")
            if constraint_lines:
                sections.append(
                    "## Constraints\n" + "\n".join(constraint_lines)
                )

        if error_context:
            sections.append(
                "## Error from Prior Attempt\n"
                "Fix the following error in your generated code:\n"
                f"{error_context}"
            )

        if self_knowledge_chunks:
            sections.append(
                f"## Relevant API Reference\n{self_knowledge_chunks}"
            )

        sections.append(
            "Write Python code using ``dan.builder`` that implements this workflow. "
            "Output ONLY executable Python code."
        )

        return "\n\n".join(sections)

    def build_full_prompt(
        self,
        goal: str,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        error_context: str | None = None,
        constraints: dict | None = None,
        self_knowledge_chunks: str | None = None,
        domain: str | None = None,
        graph_summary: str | None = None,
        complexity_hint: str | None = None,
    ) -> tuple[str, str]:
        """Build the complete (system_prompt, user_prompt) pair.

        The system prompt includes the DSL reference and few-shot examples.
        The user prompt includes the goal and optional context.

        When *complexity_hint* is provided it is appended to the user prompt
        so the LLM has an explicit node-count target.
        """
        domain_context = ""
        if domain:
            try:
                from dan.meta.generation_defaults import (
                    build_domain_prompt_context,
                    get_domain_profile,
                )
                profile = get_domain_profile(domain)
                if profile:
                    domain_context = build_domain_prompt_context(profile)
            except Exception:
                logger.warning("Domain profile loading failed for %s", domain, exc_info=True)

        system = (
            self.build_system_prompt(domain_context=domain_context)
            + "\n\n"
            + self.build_few_shot_examples()
        )

        user = self.build_user_prompt(
            goal,
            tools=tools,
            skills=skills,
            error_context=error_context,
            constraints=constraints,
            self_knowledge_chunks=self_knowledge_chunks,
        )

        if graph_summary:
            user = (
                f"## Existing Workflow (modify, don't rebuild from scratch)\n"
                f"{graph_summary}\n\n{user}"
            )

        if complexity_hint:
            user += f"\n\n[Complexity guidance: {complexity_hint}]"

        return system, user


# ---------------------------------------------------------------------------
# WorkflowPlanner
# ---------------------------------------------------------------------------


class WorkflowPlanner:
    """LLM-powered reuse-first workflow planner."""

    def __init__(
        self,
        discovery: DiscoveryService,
        graph_store: Any | None = None,
        llm_call: Callable[..., Awaitable[str]] | None = None,
        model: str | None = None,
        max_retries: int = 3,
        temperature: float = 0.3,
        discovery_top_k: int = 5,
    ) -> None:
        self._discovery = discovery
        self._graph_store = graph_store
        self._llm_call = llm_call
        self._model = model
        self._max_retries = max_retries
        self._temperature = temperature
        self._discovery_top_k = discovery_top_k

    async def plan(
        self,
        goal: str,
        error_context: str | None = None,
        plan_context: dict[str, Any] | None = None,
    ) -> PlannerOutput:
        """Discover → prompt → LLM → parse → validate.

        Planning-time calibration (31-15 §7-4): when available, injects
        duration estimates, failure hotspot warnings, and model preference
        hints from ``planning_calibration`` into the LLM planning context.
        """
        discoveries = await self._discovery.discover_all(goal, self._discovery_top_k)

        plan_context = dict(plan_context or {})
        plan_context = self._inject_calibration(goal, plan_context)

        builder = PlanningPromptBuilder()
        system_prompt = builder.build_system_prompt()
        user_prompt = builder.build_user_prompt(
            goal, discoveries, error_context, plan_context
        )

        last_error: str | None = None
        for attempt in range(self._max_retries + 1):
            if last_error and attempt > 0:
                user_prompt += f"\n\nPrevious attempt was invalid: {last_error}. Please fix."

            raw = await self._call_llm(system_prompt, user_prompt)
            try:
                plan_result = self._parse_plan(raw)
            except ValueError as exc:
                last_error = str(exc)
                continue

            review = self._validate_plan(plan_result)
            if review.errors and attempt < self._max_retries:
                last_error = "; ".join(review.errors)
                continue

            return PlannerOutput(plan=plan_result, review=review)

        return PlannerOutput(
            plan=GeneratePlan(description=goal, spec={}),
            review=PlanReview(valid=False, errors=[last_error or "Planning failed"]),
        )

    def _inject_calibration(
        self,
        goal: str,
        plan_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Inject planning-time calibration data from experience (31-15 §7-4)."""
        try:
            from dan.engine.planning_calibration import (
                DurationEstimator,
                FailureHotspotPredictor,
                ModelPreference,
            )

            calibration_hints: list[str] = []

            estimator = DurationEstimator()
            median, ci = estimator.estimate(goal)
            calibration_hints.append(
                f"Estimated duration: ~{median:.0f} min (±{ci:.0f} min)"
            )

            predictor = FailureHotspotPredictor()
            for ntype in ("code_operator", "tool_operator", "llm_operator"):
                prob, advice = predictor.predict(ntype)
                if prob > 0.10:
                    calibration_hints.append(f"Failure warning ({ntype}): {advice}")

            model_pref = ModelPreference()
            rec = model_pref.recommend("llm_operator", task_pattern=goal)
            if rec:
                calibration_hints.append(
                    f"Model tier recommendation: {rec}"
                )

            if calibration_hints:
                plan_context["calibration_hints"] = calibration_hints

        except Exception:
            logger.debug("Planning calibration injection skipped", exc_info=True)

        return plan_context

    async def execute_plan(
        self,
        plan: PlanResult,
        *,
        domain: str | None = None,
        user_text: str | None = None,
    ) -> dict[str, Any]:
        """Execute a PlanResult: load, adapt, or compile a graph.

        For ``GENERATE_CODE`` plans: runs the full codegen pipeline
        (sandbox → validate → diagnose).  On failure, falls back to the
        legacy ``GENERATE`` declarative path when ``_LEGACY_GENERATE_FALLBACK``
        is enabled.
        """
        if isinstance(plan, ReusePlan):
            return self._execute_reuse(plan)
        if isinstance(plan, AdaptPlan):
            return self._execute_adapt(plan)
        if isinstance(plan, GenerateCodePlan) and getattr(plan, "intent", None) is not None:
            from dan.meta.intent_compiler import DirectBuildError

            direct_build_mode = os.environ.get("DAN_DIRECT_BUILD", "on").lower()
            if direct_build_mode != "off":
                try:
                    return await self.execute_plan_direct(plan, domain=domain, user_text=user_text)
                except DirectBuildError as exc:
                    message = str(exc)
                    if "execution-readiness validation" in message:
                        raise ExecutionReadinessError(message) from exc
                    if direct_build_mode == "only":
                        raise
                except Exception as exc:
                    if direct_build_mode == "only":
                        raise
                    logger.warning("Direct build failed, falling back to codegen: %s", exc)
        if isinstance(plan, GenerateCodePlan):
            try:
                return await self._execute_generate_code(plan, domain=domain, user_text=user_text)
            except ExecutionReadinessError:
                raise
            except (ValueError, RuntimeError) as exc:
                honest_failure = _honest_codegen_failure_message(plan, exc)
                if honest_failure is not None:
                    readiness_exc = ExecutionReadinessError(honest_failure)
                    diagnostics = getattr(exc, "diagnostics", None)
                    if diagnostics is not None:
                        readiness_exc.diagnostics = diagnostics  # type: ignore[attr-defined]
                    raise readiness_exc from exc
                if _LEGACY_GENERATE_FALLBACK:
                    logger.warning(
                        "GENERATE_CODE failed (%s); falling back to legacy GENERATE",
                        exc,
                    )
                    try:
                        fallback_spec = {
                            "name": plan.description or "Generated Workflow",
                            "nodes": [
                                {
                                    "node_type": "llm_operator",
                                    "name": "main",
                                    "config": {
                                        "prompt_template": plan.description,
                                    },
                                }
                            ],
                            "edges": [],
                        }
                        graph_data = self._compile_generate_spec(fallback_spec)
                        workflow_id = f"meta-fallback-{uuid.uuid4().hex[:10]}"
                        warning = (
                            "Workflow generation fell back to a simplified single-node workflow "
                            "after builder code execution failed."
                        )
                        return {
                            "workflow_id": workflow_id,
                            "graph": graph_data,
                            "description": plan.description,
                            "generated": True,
                            "legacy_fallback": True,
                            "warning": warning,
                            "legacy_fallback_message": warning,
                        }
                    except Exception:
                        logger.exception("Legacy GENERATE fallback also failed")
                raise
        if isinstance(plan, GeneratePlan):
            return self._execute_generate(plan)
        raise TypeError(f"Unknown plan type: {type(plan)}")

    async def execute_plan_direct(
        self,
        plan: GenerateCodePlan,
        *,
        domain: str | None = None,
        user_text: str | None = None,
    ) -> dict[str, Any]:
        """Build a graph directly from intent without LLM codegen."""
        from dan.meta.intent_compiler import DirectBuildError, IntentCompiler
        from dan.meta.intent_schema import WorkflowIntent

        if not isinstance(getattr(plan, "intent", None), WorkflowIntent):
            raise ValueError("No intent attached to plan")

        graph = IntentCompiler().build_graph(plan.intent, domain=domain)
        graph_data = graph.model_dump(mode="json")

        validation = validate_codegen_output(graph_data)
        if not validation.success and validation.fatal_errors:
            raise DirectBuildError(
                stage_name="validation",
                stage_type="validate",
                underlying=ValueError(
                    "; ".join(e.message for e in validation.fatal_errors)
                ),
            )

        graph_data = self._enrich_graph(graph_data, domain, user_text)
        graph_data = self._ensure_execution_ready_graph(
            graph_data,
            failure_message="Enriched direct-build graph failed execution-readiness validation",
            direct_build=True,
        )

        return {
            "workflow_id": f"direct-{uuid.uuid4().hex[:10]}",
            "graph": graph_data,
            "success": True,
            "description": plan.description,
            "direct_build": True,
        }

    # -- Internal helpers --------------------------------------------------

    def _enrich_graph(
        self,
        graph_data: dict,
        domain: str | None,
        user_text: str | None,
    ) -> dict:
        """Apply post-generation enrichment defaults to a graph dict."""
        try:
            from dan.meta.generation_defaults import (
                DefaultProfile,
                DefaultsEnricher,
                GenerationDefaults,
                detect_suppressions,
                get_domain_profile,
            )

            gen_defaults = GenerationDefaults()
            if domain:
                dp = get_domain_profile(domain)
                if dp and dp.default_profile:
                    gen_defaults = GenerationDefaults.from_profile(
                        DefaultProfile(dp.default_profile)
                    )
            if user_text:
                for field, value in detect_suppressions(user_text).items():
                    setattr(gen_defaults, field, value)

            enricher = DefaultsEnricher(gen_defaults)
            graph_data = enricher.enrich(graph_data)
        except Exception:
            logger.warning("DefaultsEnricher failed", exc_info=True)
        graph_data = _bind_explicit_tool_paths(graph_data, user_text=user_text)
        return graph_data

    @staticmethod
    def _ensure_execution_ready_graph(
        graph_data: dict[str, Any],
        *,
        failure_message: str,
        direct_build: bool = False,
    ) -> dict[str, Any]:
        """Re-validate post-enrichment graphs before returning them to callers."""
        validation = validate_codegen_output(graph_data)
        if validation.success and validation.run_ready:
            return (
                validation.graph.model_dump(mode="json")
                if validation.graph is not None
                else graph_data
            )

        readiness_details: list[str] = []
        if validation.contract_report is not None:
            readiness_details.extend(
                _format_run_readiness_issues(
                    validation.contract_report.run_readiness_issues,
                    validation.run_readiness_failure_mode,
                )[:5]
            )
        details = "; ".join(
            [*(error.message for error in validation.errors[:5]), *readiness_details]
        ) or "unknown validation failure"
        if direct_build:
            from dan.meta.intent_compiler import DirectBuildError

            raise DirectBuildError(
                f"{failure_message}: {details}",
                stage_name="post_enrichment_validation",
                stage_type="validate",
                underlying=ValueError(f"{failure_message}: {details}"),
            )
        raise ExecutionReadinessError(f"{failure_message}: {details}")

    @staticmethod
    def _diagnosis_validation_errors(
        graph_data: dict[str, Any],
    ) -> "DiagnosisValidationFeedback":
        """Return generation-style errors and contract context for diagnosis."""
        from dan.meta.diagnosis import DiagnosisValidationFeedback

        validation = validate_codegen_output(graph_data)
        if validation.success and validation.run_ready:
            return DiagnosisValidationFeedback()
        return DiagnosisValidationFeedback(
            errors=WorkflowPlanner._diagnosis_handoff_errors(validation),
            contract_report=WorkflowPlanner._diagnosis_contract_report(validation),
        )

    @staticmethod
    def _diagnosis_contract_report(
        validation: ValidationResult,
    ) -> dict[str, Any] | None:
        """Serialize validation state into structured diagnosis handoff input."""
        report = validation.contract_report
        if report is None:
            return None

        provenance = workflow_build_provenance(report)
        if validation.fatal_errors:
            handoff_reason = "The workflow build contract reported fatal failures."
        elif validation.recoverable_errors and not validation.run_ready:
            handoff_reason = (
                "The workflow still has repairable contract failures and "
                "run-readiness gaps after bounded mechanical repair."
            )
        elif validation.recoverable_errors:
            handoff_reason = (
                "The workflow still has repairable contract failures after "
                "bounded mechanical repair."
            )
        elif not validation.run_ready:
            handoff_reason = (
                "The workflow passed structural validation but is not run-ready."
            )
        else:
            handoff_reason = "The workflow build contract is satisfied."

        return {
            "workflow_id": report.workflow_id,
            "normalized_workflow_id": report.normalized_workflow_id,
            "display_name": report.display_name,
            "validated": report.validated,
            "run_ready": report.run_ready,
            "build_status": provenance.get("build_status"),
            "failure_bucket": provenance.get("failure_bucket"),
            "handoff_reason": handoff_reason,
            "build_provenance": provenance,
            "build_summary": {
                "error_count": len(report.errors),
                "warning_count": len(report.warnings),
                "auto_fix_count": len(report.auto_fixes_applied),
                "run_readiness_issue_count": len(report.run_readiness_issues),
            },
            "run_readiness_failure_mode": validation.run_readiness_failure_mode,
            "errors": [
                {
                    "category": issue.category,
                    "message": issue.message,
                    "severity": issue.severity,
                    "artifact_id": issue.artifact_id,
                }
                for issue in report.errors
            ],
            "warnings": list(report.warnings),
            "auto_fixes_applied": list(report.auto_fixes_applied),
            "run_readiness_issues": list(report.run_readiness_issues),
        }

    @staticmethod
    def _diagnosis_handoff_errors(
        validation: ValidationResult,
    ) -> list["GenerationError"]:
        """Collect recoverable validation and run-readiness issues for diagnosis."""
        from dan.meta.diagnosis import (
            GenerationError,
            GenerationErrorType,
            GenerationStage,
        )

        errors = list(validation.recoverable_errors)
        report = validation.contract_report
        if report is not None and not validation.run_ready:
            for issue in report.run_readiness_issues:
                lower = issue.lower()
                error_type = GenerationErrorType.build_error
                if (
                    "reachable" in lower
                    or "entry point" in lower
                    or "exit point" in lower
                ):
                    error_type = GenerationErrorType.reachability
                elif "required input port" in lower:
                    error_type = GenerationErrorType.missing_port
                elif "port" in lower:
                    error_type = GenerationErrorType.edge_endpoint
                artifact_match = re.search(r"'([^']+)'", issue)
                errors.append(
                    GenerationError(
                        stage=GenerationStage.validation,
                        error_type=error_type,
                        message=issue,
                        artifact_id=artifact_match.group(1) if artifact_match else None,
                        recoverable=True,
                    )
                )

        deduped: list[GenerationError] = []
        seen: set[tuple[Any, ...]] = set()
        for error in errors:
            key = (
                error.stage,
                error.error_type,
                error.message,
                error.source_line,
                error.artifact_id,
                error.recoverable,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(error)
        return deduped

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        if self._llm_call is None:
            raise RuntimeError("No LLM callable configured for WorkflowPlanner")
        return await self._llm_call(
            system_prompt, user_prompt, self._model, self._temperature,
        )

    @staticmethod
    def _parse_plan(raw: str) -> PlanResult:
        """Parse LLM JSON output into a typed PlanResult."""
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        text = match.group(1) if match else raw.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc

        action = data.get("action", "").upper()
        if action == "REUSE":
            return ReusePlan.model_validate(data)
        if action == "ADAPT":
            return AdaptPlan.model_validate(data)
        if action == "GENERATE":
            return GeneratePlan.model_validate(data)
        if action == "GENERATE_CODE":
            return GenerateCodePlan.model_validate(data)
        raise ValueError(f"Unknown action: {data.get('action')}")

    def _validate_plan(self, plan: PlanResult) -> PlanReview:
        """Structural validation of a plan result."""
        review = PlanReview()

        if isinstance(plan, ReusePlan):
            if self._graph_store:
                graph = self._graph_store.get_graph(plan.workflow_id)
                if graph is None:
                    review.errors.append(f"Workflow '{plan.workflow_id}' not found")
                    review.valid = False
            review.confidence = 0.9

        elif isinstance(plan, AdaptPlan):
            if self._graph_store:
                graph = self._graph_store.get_graph(plan.workflow_id)
                if graph is None:
                    review.errors.append(f"Base workflow '{plan.workflow_id}' not found")
                    review.valid = False
            if not plan.mutations:
                review.warnings.append("ADAPT plan has no mutations")
            review.confidence = 0.7

        elif isinstance(plan, GeneratePlan):
            if not plan.spec:
                review.warnings.append("GENERATE plan has empty spec")
            spec_nodes = plan.spec.get("nodes", [])
            if not spec_nodes:
                review.warnings.append("GENERATE spec has no nodes")
            else:
                try:
                    graph_data = self._compile_generate_spec(plan.spec)
                    from dan.models.graph import Graph
                    from dan.validation.graph import validate_graph
                    graph = Graph.model_validate(graph_data)
                    if not getattr(graph, "version", ""):
                        review.errors.append("Compiled graph missing version")
                    for msg in validate_graph(graph):
                        if "warning" in msg.lower():
                            review.warnings.append(msg)
                        else:
                            review.errors.append(msg)
                except Exception as exc:
                    review.errors.append(f"Generate spec compile failed: {exc}")
            review.confidence = 0.5

        elif isinstance(plan, GenerateCodePlan):
            if not plan.code.strip():
                review.errors.append("GENERATE_CODE plan has empty code")
            elif "build()" not in plan.code and ".build()" not in plan.code:
                review.warnings.append(
                    "GENERATE_CODE code does not call build() — "
                    "may not produce a valid graph"
                )
            review.confidence = 0.4

        review.valid = review.valid and not review.errors
        return review

    def _execute_reuse(self, plan: ReusePlan) -> dict[str, Any]:
        if not self._graph_store:
            raise RuntimeError("GraphStore required for REUSE plans")
        graph_data = self._graph_store.get_graph(plan.workflow_id)
        if not graph_data:
            raise ValueError(f"Workflow {plan.workflow_id} not found")
        return {"workflow_id": plan.workflow_id, "graph": graph_data}

    def _execute_adapt(self, plan: AdaptPlan) -> dict[str, Any]:
        if not self._graph_store:
            raise RuntimeError("GraphStore required for ADAPT plans")
        graph_data = self._graph_store.get_graph(plan.workflow_id)
        if not graph_data:
            raise ValueError(f"Workflow {plan.workflow_id} not found")
        from dan.graph_mutator import GraphMutator, MutationPlan

        mutation_plan = MutationPlan(
            operations=plan.mutations,
            description="Planner adaptation",
        )
        mutator = GraphMutator()
        result = mutator.apply(graph_data, mutation_plan)
        if not result.success or result.new_graph is None:
            error_msgs = [e.message for e in result.errors]
            raise ValueError(f"Mutation failed: {error_msgs}")
        adapted_graph = self._ensure_execution_ready_graph(
            result.new_graph,
            failure_message="Adapted workflow failed execution-readiness validation",
        )
        return {
            "workflow_id": plan.workflow_id,
            "graph": adapted_graph,
            "mutations_applied": len(plan.mutations),
        }

    def _execute_generate(self, plan: GeneratePlan) -> dict[str, Any]:
        """Compile a generate spec into a runnable graph dict."""
        graph_data = self._compile_generate_spec(plan.spec)
        graph_data = self._ensure_execution_ready_graph(
            graph_data,
            failure_message="Generated workflow failed execution-readiness validation",
        )
        workflow_id = f"meta-{uuid.uuid4().hex[:10]}"
        return {
            "workflow_id": workflow_id,
            "graph": graph_data,
            "description": plan.description,
            "generated": True,
        }

    async def _execute_generate_code(
        self,
        plan: GenerateCodePlan,
        *,
        domain: str | None = None,
        user_text: str | None = None,
    ) -> dict[str, Any]:
        """Execute builder DSL code in a subprocess sandbox with full
        validation and bounded diagnosis.

        Pipeline: sandbox → parse → validate → (diagnosis if recoverable) → return.
        On unrecoverable failure, raises ``ValueError`` with ``CodegenDiagnostics``
        attached as the ``diagnostics`` attribute.
        """
        from dan.models.graph import Graph
        from dan.sandbox import SandboxConfig
        from dan.sandbox.runner import SandboxRunner

        sandbox_config = SandboxConfig(
            mode="subprocess",
            timeout_seconds=30,
            memory_mb=256,
            language="python",
            max_output_bytes=1_000_000,
        )

        import pathlib as _pathlib
        src_path = str(_pathlib.Path(__file__).resolve().parents[2])

        runner = SandboxRunner()
        result, structured = await runner.run(
            _BUILDER_CODE_HARNESS,
            sandbox_config,
            {"src_path": src_path, "user_code": plan.code},
        )

        codegen = self._parse_codegen_result(result, structured, plan.code)

        # Sandbox execution failed (syntax error, import error, etc.)
        if not codegen.success:
            from dan.meta.diagnosis import (
                DiagnosisLoop,
                ErrorClassifier,
                GenerationError,
                GenerationErrorType,
                GenerationStage,
                generation_repair_attempt_budget,
            )

            sandbox_errors = [
                GenerationError(
                    stage=GenerationStage.sandbox,
                    error_type=GenerationErrorType(
                        ErrorClassifier._match_sandbox_error_type(codegen.error_type)
                    ) if codegen.error_type else GenerationErrorType.runtime_error,
                    message=codegen.error_message or "Unknown sandbox error",
                    source_line=codegen.error_line,
                    recoverable=codegen.error_type in ("SyntaxError", "ImportError", "NameError"),
                )
            ]

            recoverable = any(e.recoverable for e in sandbox_errors)
            if recoverable:
                diagnosis = DiagnosisLoop(
                    max_attempts=generation_repair_attempt_budget()
                )
                diag_result = await diagnosis.diagnose_and_repair(
                    goal=plan.description,
                    generated_code=plan.code,
                    errors=sandbox_errors,
                    llm_complete=self._llm_complete_for_repair,
                    graph_validator=self._diagnosis_validation_errors,
                )
                if diag_result.success and diag_result.final_graph:
                    graph_data = self._enrich_graph(
                        diag_result.final_graph,
                        domain,
                        user_text,
                    )
                    graph_data = self._ensure_execution_ready_graph(
                        graph_data,
                        failure_message=(
                            "Diagnosis-repaired codegen graph failed "
                            "execution-readiness validation"
                        ),
                    )
                    workflow_id = f"meta-code-{uuid.uuid4().hex[:10]}"
                    return {
                        "workflow_id": workflow_id,
                        "graph": graph_data,
                        "description": plan.description,
                        "generated": True,
                        "code_generated": True,
                        "source_code": diag_result.final_code,
                        "repair_attempts": len(diag_result.attempts),
                    }

                diagnostics = CodegenDiagnostics(
                    attempts=max(1, len(diag_result.attempts)),
                    errors=sandbox_errors,
                    final_code=plan.code,
                    diagnosis_result=diag_result,
                )
                exc = ValueError(
                    f"Builder code execution failed ({codegen.error_type}): "
                    f"{codegen.error_message}"
                )
                exc.diagnostics = diagnostics  # type: ignore[attr-defined]
                raise exc

            diagnostics = CodegenDiagnostics(
                attempts=1,
                errors=sandbox_errors,
                final_code=plan.code,
            )
            msg = f"Builder code execution failed ({codegen.error_type}): {codegen.error_message}"
            if codegen.error_line is not None:
                msg += f" at line {codegen.error_line}"
            exc = ValueError(msg)
            exc.diagnostics = diagnostics  # type: ignore[attr-defined]
            raise exc

        # Sandbox succeeded — run validation pipeline
        validation = validate_codegen_output(codegen.graph)

        if validation.run_readiness_failure_mode in {
            "unresolved_code",
            "non_runnable_code",
        }:
            readiness_details: list[str] = []
            if validation.contract_report is not None:
                readiness_details.extend(
                    _format_run_readiness_issues(
                        validation.contract_report.run_readiness_issues,
                        validation.run_readiness_failure_mode,
                    )[:5]
                )
            details = "; ".join(
                [
                    *(error.message for error in validation.errors[:5]),
                    *readiness_details,
                ]
            ) or "unknown validation failure"
            raise ExecutionReadinessError(
                f"Generated code graph failed execution-readiness validation: {details}"
            )

        if validation.success and validation.run_ready:
            graph_data = (
                validation.graph.model_dump(mode="json")
                if validation.graph
                else codegen.graph
            )

            # 32-3: Post-generation enrichment (safety net for missing defaults)
            graph_data = self._enrich_graph(graph_data, domain, user_text)
            graph_data = self._ensure_execution_ready_graph(
                graph_data,
                failure_message="Enriched codegen graph failed execution-readiness validation",
            )

            workflow_id = f"meta-code-{uuid.uuid4().hex[:10]}"
            return {
                "workflow_id": workflow_id,
                "graph": graph_data,
                "description": plan.description,
                "generated": True,
                "code_generated": True,
                "source_code": codegen.source_code,
            }

        handoff_errors = self._diagnosis_handoff_errors(validation)

        # Validation/run-readiness handoff — try bounded diagnosis when fatal
        # contract failures are absent and the remaining gap is semantic.
        if handoff_errors and not validation.fatal_errors:
            from dan.meta.diagnosis import (
                DiagnosisLoop,
                generation_repair_attempt_budget,
            )

            diagnosis = DiagnosisLoop(
                max_attempts=generation_repair_attempt_budget()
            )
            diag_result = await diagnosis.diagnose_and_repair(
                goal=plan.description,
                generated_code=codegen.source_code,
                errors=handoff_errors,
                llm_complete=self._llm_complete_for_repair,
                graph_validator=self._diagnosis_validation_errors,
                contract_report=self._diagnosis_contract_report(validation),
            )
            if diag_result.success and diag_result.final_graph:
                graph_data = self._enrich_graph(
                    diag_result.final_graph,
                    domain,
                    user_text,
                )
                graph_data = self._ensure_execution_ready_graph(
                    graph_data,
                    failure_message=(
                        "Diagnosis-repaired codegen graph failed "
                        "execution-readiness validation"
                    ),
                )
                workflow_id = f"meta-code-{uuid.uuid4().hex[:10]}"
                return {
                    "workflow_id": workflow_id,
                    "graph": graph_data,
                    "description": plan.description,
                    "generated": True,
                    "code_generated": True,
                    "source_code": diag_result.final_code,
                    "repair_attempts": len(diag_result.attempts),
                }

            diagnostics = CodegenDiagnostics(
                attempts=max(1, len(diag_result.attempts)),
                errors=handoff_errors,
                final_code=codegen.source_code,
                validation_result=validation,
                diagnosis_result=diag_result,
            )
            exc = ValueError(
                f"Codegen validation handoff failed with {len(handoff_errors)} issue(s); "
                f"diagnosis repair unsuccessful"
            )
            exc.diagnostics = diagnostics  # type: ignore[attr-defined]
            raise exc

        # Fatal errors — no diagnosis possible
        diagnostics = CodegenDiagnostics(
            attempts=1,
            errors=list(validation.errors),
            final_code=codegen.source_code,
            validation_result=validation,
        )
        exc = ValueError(
            f"Codegen validation failed with {len(validation.fatal_errors)} fatal error(s)"
        )
        exc.diagnostics = diagnostics  # type: ignore[attr-defined]
        raise exc

    async def _llm_complete_for_repair(self, system: str, user: str) -> str:
        """Simple completion wrapper for DiagnosisLoop re-prompts.

        No function calling, no streaming — just a plain text completion.
        """
        return await self._call_llm(system, user)

    @staticmethod
    def _parse_codegen_result(
        result: Any,
        structured: Any,
        source_code: str,
    ) -> CodegenResult:
        """Parse sandbox output into a ``CodegenResult``."""
        if structured is None:
            return CodegenResult(
                success=False,
                source_code=source_code,
                error_type="SandboxError",
                error_message=result.stderr or f"Sandbox exited with code {result.exit_code}",
            )

        if not isinstance(structured, dict):
            return CodegenResult(
                success=False,
                source_code=source_code,
                error_type="SandboxError",
                error_message=f"Invalid _result.json: expected dict, got {type(structured).__name__}",
            )

        if "error" in structured:
            err = structured["error"]
            return CodegenResult(
                success=False,
                source_code=source_code,
                error_type=err.get("type"),
                error_message=err.get("message"),
                error_line=err.get("line"),
            )

        if "graph" in structured:
            return CodegenResult(
                success=True,
                graph=structured["graph"],
                source_code=structured.get("source_code", source_code),
            )

        return CodegenResult(
            success=False,
            source_code=source_code,
            error_type="SandboxError",
            error_message="No 'graph' or 'error' key in _result.json",
        )

    @staticmethod
    def _compile_generate_spec(spec: dict[str, Any]) -> dict[str, Any]:
        """Compile a lightweight PlanIR spec to dan_graph_v1 JSON."""
        from dan.meta.generation_defaults import normalize_task_tier, resolve_default_llm_model

        if "version" in spec and "nodes" in spec and "edges" in spec:
            return spec

        raw_nodes = spec.get("nodes", [])
        raw_edges = spec.get("edges", [])
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise ValueError("spec.nodes must be a non-empty list")

        def _slug(text: str) -> str:
            return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_") or "node"

        def _default_source_port(node_type: str) -> str | None:
            if node_type == "llm_operator":
                return "text"
            if node_type in {"tool_operator", "code_operator"}:
                return "result"
            if node_type == "gate":
                return None
            return "output"

        node_id_by_name: dict[str, str] = {}
        node_type_by_id: dict[str, str] = {}
        compiled_nodes: list[dict[str, Any]] = []
        for idx, node in enumerate(raw_nodes):
            if not isinstance(node, dict):
                raise ValueError("Each spec node must be an object")
            node_type = str(node.get("node_type", "llm_operator"))
            name = str(node.get("name", f"Node {idx + 1}"))
            node_id = node.get("id") or f"{_slug(name)}_{idx+1}"
            config = node.get("config") or {}
            if not isinstance(config, dict):
                raise ValueError("Node config must be an object")

            base: dict[str, Any] = {"id": node_id, "name": name, "node_type": node_type}
            if node_type == "llm_operator":
                task_tier = normalize_task_tier(
                    config.get("task_tier", config.get("model_tier"))
                )
                model_policy = config.get("model_policy")
                base.update({
                    "model": config.get(
                        "model",
                        resolve_default_llm_model(tier=task_tier or "routine"),
                    ),
                    "prompt_template": config.get("prompt_template", config.get("prompt", "")),
                    "system_prompt": config.get("system_prompt", ""),
                    "temperature": config.get("temperature", 0.3),
                })
                if task_tier is not None:
                    base["task_tier"] = task_tier
                if model_policy is not None:
                    base["model_policy"] = model_policy
                elif task_tier is not None:
                    base["model_policy"] = {"strategy": "tier"}
            elif node_type == "tool_operator":
                base.update({
                    "tool_id": config.get("tool_id", config.get("tool_name", "run_python")),
                    "tool_config": config.get("tool_config", {}),
                })
            elif node_type == "code_operator":
                base.update({
                    "code": config.get("code", ""),
                    "language": config.get("language", "python"),
                    "sandbox_config": config.get("sandbox_config", {}),
                })
            elif node_type == "gate":
                base.update({
                    "condition": config.get("condition", "True"),
                    "gate_mode": config.get("gate_mode", "if_else"),
                    "max_iterations": config.get("max_iterations", 10),
                })
            else:
                raise ValueError(
                    "Unsupported generated node_type: "
                    f"{node_type}. Supported generated types: "
                    + ", ".join(GENERATE_SPEC_NODE_TYPES)
                )

            compiled_nodes.append(base)
            node_id_by_name[name] = node_id
            node_type_by_id[node_id] = node_type

        compiled_edges: list[dict[str, Any]] = []
        for idx, edge in enumerate(raw_edges):
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("source", edge.get("source_id", "")))
            target = str(edge.get("target", edge.get("target_id", "")))
            source_id = node_id_by_name.get(source, source)
            target_id = node_id_by_name.get(target, target)
            if not source_id or not target_id:
                continue
            source_port = edge.get("source_port")
            if not source_port:
                source_node_type = node_type_by_id.get(source_id, "")
                source_port = _default_source_port(source_node_type)
                if source_port is None:
                    raise ValueError(
                        f"Edges from generated node type '{source_node_type}' "
                        "must specify source_port explicitly"
                    )
            compiled_edges.append({
                "id": edge.get("id", f"e{idx+1}"),
                "edge_type": edge.get("edge_type", "data"),
                "source_node_id": source_id,
                "source_port": source_port,
                "target_node_id": target_id,
                "target_port": edge.get("target_port", "input"),
            })

        return {
            "version": "dan_graph_v1",
            "metadata": {
                "name": str(spec.get("name", "Generated Workflow")),
                "description": str(spec.get("description", "")),
            },
            "nodes": compiled_nodes,
            "edges": compiled_edges,
            "entry_points": [compiled_nodes[0]["id"]] if compiled_nodes else [],
            "exit_points": [compiled_nodes[-1]["id"]] if compiled_nodes else [],
            "hyperedges": [],
            "sub_graphs": {},
            "shared_context": [],
            "artifact_refs": [],
        }
