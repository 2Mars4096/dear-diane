"""Workflow Planner — LLM-driven reuse-first workflow planning.

Implements Plan 19-2: receives a high-level goal, discovers available
building blocks and relevant past workflows, and produces a declarative
plan (REUSE / ADAPT / GENERATE) compiled into a valid workflow graph.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from enum import Enum
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from dan.meta.discovery import DiscoveryResult, DiscoveryService

logger = logging.getLogger(__name__)

_BUILDER_CODE_HARNESS = '''\
import json, sys, pathlib

_inputs = json.loads(pathlib.Path("_inputs.json").read_text(encoding="utf-8"))
if _inputs.get("src_path"):
    sys.path.insert(0, _inputs["src_path"])

# --- user code starts ---
{{USER_CODE}}
# --- user code ends ---

# Expect the user code to assign ``graph`` or call ``build()`` at module level.
_graph = None
for _name in ("graph", "wf", "workflow", "g"):
    _g = locals().get(_name)
    if _g is not None and hasattr(_g, "model_dump"):
        _graph = _g
        break

if _graph is None:
    print("ERROR: builder code must assign the result of .build() to a variable "
          "named graph, wf, workflow, or g", file=sys.stderr)
    sys.exit(1)

pathlib.Path("_result.json").write_text(
    json.dumps(_graph.model_dump(mode="json"), default=str),
    encoding="utf-8",
)
'''

__all__ = [
    "AdaptPlan",
    "GenerateCodePlan",
    "GeneratePlan",
    "PlanAction",
    "PlanReview",
    "PlanResult",
    "PlannerOutput",
    "PlanningPromptBuilder",
    "ReusePlan",
    "WorkflowPlanner",
]


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
   - {"op": "add_edge", "edge_type": "data", "source_id": "...", "source_port": "output", "target_id": "...", "target_port": "input"}
   - {"op": "remove_edge", "edge_id": "..."}

3. GENERATE — create a new workflow from scratch:
   {"action": "GENERATE", "description": "...", "spec": {"nodes": [...], "edges": [...]}}
   Each node: {"node_type": "llm_operator"|"tool_operator"|"code_operator"|"gate"|"for_each", "name": "...", "config": {...}}
   Each edge: {"source": "<node_name>", "target": "<node_name>"}

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
{"action": "ADAPT", "workflow_id": "equity_research", "mutations": [{"op": "add_node", "node_type": "llm_operator", "name": "beamer_formatter", "config": {"system_prompt": "Convert analysis into beamer LaTeX slides"}}, {"op": "add_edge", "edge_type": "data", "source_id": "analysis", "source_port": "output", "target_id": "beamer_formatter", "target_port": "input"}], "input_mapping": {"ticker": "AAPL"}}

### Example 3 — GENERATE
Goal: "Build a RAG QA system for internal docs"
Similar workflows: none
Output:
{"action": "GENERATE", "description": "RAG-based question answering pipeline", "spec": {"nodes": [{"node_type": "tool_operator", "name": "doc_indexer", "config": {"tool_name": "index_documents"}}, {"node_type": "llm_operator", "name": "retriever", "config": {"system_prompt": "Retrieve relevant passages for the question"}}, {"node_type": "llm_operator", "name": "answerer", "config": {"system_prompt": "Answer the question using retrieved passages"}}], "edges": [{"source": "doc_indexer", "target": "retriever"}, {"source": "retriever", "target": "answerer"}]}}

Output ONLY a single valid JSON object. No markdown, no explanation."""

    def build_system_prompt(self) -> str:
        """Return the base planning system prompt."""
        return self.SYSTEM_TEMPLATE

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
            ctx_lines: list[str] = []
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
        """Discover → prompt → LLM → parse → validate."""
        discoveries = await self._discovery.discover_all(goal, self._discovery_top_k)

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

    async def execute_plan(self, plan: PlanResult) -> dict[str, Any]:
        """Execute a PlanResult: load, adapt, or compile a graph."""
        if isinstance(plan, ReusePlan):
            return self._execute_reuse(plan)
        if isinstance(plan, AdaptPlan):
            return self._execute_adapt(plan)
        if isinstance(plan, GenerateCodePlan):
            return await self._execute_generate_code(plan)
        if isinstance(plan, GeneratePlan):
            return self._execute_generate(plan)
        raise TypeError(f"Unknown plan type: {type(plan)}")

    # -- Internal helpers --------------------------------------------------

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
        from dan.server.graph_mutator import GraphMutator, MutationPlan

        mutation_plan = MutationPlan(
            operations=plan.mutations,
            description="Planner adaptation",
        )
        mutator = GraphMutator()
        result = mutator.apply(graph_data, mutation_plan)
        if not result.success or result.new_graph is None:
            error_msgs = [e.message for e in result.errors]
            raise ValueError(f"Mutation failed: {error_msgs}")
        return {
            "workflow_id": plan.workflow_id,
            "graph": result.new_graph,
            "mutations_applied": len(plan.mutations),
        }

    def _execute_generate(self, plan: GeneratePlan) -> dict[str, Any]:
        """Compile a generate spec into a runnable graph dict."""
        graph_data = self._compile_generate_spec(plan.spec)
        workflow_id = f"meta-{uuid.uuid4().hex[:10]}"
        return {
            "workflow_id": workflow_id,
            "graph": graph_data,
            "description": plan.description,
            "generated": True,
        }

    async def _execute_generate_code(self, plan: GenerateCodePlan) -> dict[str, Any]:
        """Execute builder DSL code in a subprocess sandbox.

        The code must use ``dan.builder`` to construct a workflow and call
        ``build()``.  A wrapper harness serialises the resulting ``Graph``
        to ``_result.json`` so ``SandboxRunner`` can capture it.
        """
        from dan.sandbox import SandboxConfig
        from dan.sandbox.runner import SandboxRunner

        harness = _BUILDER_CODE_HARNESS.replace("{{USER_CODE}}", plan.code)

        sandbox_config = SandboxConfig(
            mode="subprocess",
            timeout_seconds=30,
            language="python",
            max_output_bytes=1_000_000,
        )

        import pathlib as _pathlib
        src_path = str(_pathlib.Path(__file__).resolve().parents[2])

        runner = SandboxRunner()
        result, structured = await runner.run(
            harness, sandbox_config, {"src_path": src_path},
        )

        if result.exit_code != 0:
            raise ValueError(
                f"Builder code execution failed (exit {result.exit_code}):\n"
                f"{result.stderr or result.stdout}"
            )

        if not isinstance(structured, dict) or "version" not in structured:
            raise ValueError(
                "Builder code did not produce a valid graph in _result.json. "
                f"Got: {type(structured).__name__}"
            )

        from dan.models.graph import Graph
        Graph.model_validate(structured)

        workflow_id = f"meta-code-{uuid.uuid4().hex[:10]}"
        return {
            "workflow_id": workflow_id,
            "graph": structured,
            "description": plan.description,
            "generated": True,
            "code_generated": True,
        }

    @staticmethod
    def _compile_generate_spec(spec: dict[str, Any]) -> dict[str, Any]:
        """Compile a lightweight PlanIR spec to dan_graph_v1 JSON."""
        if "version" in spec and "nodes" in spec and "edges" in spec:
            return spec

        raw_nodes = spec.get("nodes", [])
        raw_edges = spec.get("edges", [])
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise ValueError("spec.nodes must be a non-empty list")

        def _slug(text: str) -> str:
            return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_") or "node"

        node_id_by_name: dict[str, str] = {}
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
                base.update({
                    "model": config.get("model", "claude-sonnet-4-6"),
                    "prompt_template": config.get("prompt_template", config.get("prompt", "")),
                    "system_prompt": config.get("system_prompt", ""),
                    "temperature": config.get("temperature", 0.3),
                })
            elif node_type == "tool_operator":
                base.update({
                    "tool_id": config.get("tool_id", "run_python"),
                    "tool_config": config.get("tool_config", {}),
                })
            elif node_type == "code_operator":
                base.update({
                    "code": config.get("code", "result = inputs"),
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
                raise ValueError(f"Unsupported generated node_type: {node_type}")

            compiled_nodes.append(base)
            node_id_by_name[name] = node_id

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
            compiled_edges.append({
                "id": edge.get("id", f"e{idx+1}"),
                "edge_type": edge.get("edge_type", "data"),
                "source_node_id": source_id,
                "source_port": edge.get("source_port", "output"),
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
