from __future__ import annotations

import importlib.util
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from dan.engine import (
    Engine,
    EngineConfig,
    EngineEvent,
    EventType,
    NodeResult,
    NodeStatus,
    NullCheckpointStore,
    ProgrammaticRenderer,
)
from dan.engine.executor import ExecutorRegistry
from dan.executor_defaults import register_default_executors
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.control_flow import CompositeNode, ForEachNode, InputNode, InputVariable
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator, LLMOperator, NodeBase, ToolOperator
from dan.providers import CompletionResult
from dan.providers.registry import ProviderRegistry
from dan.worker.executor import LegacyWorkerAdapterExecutor, WorkerExecutor
from dan.worker.presets import convert_graph


class _SequenceProvider:
    supports_tool_calls = True

    def __init__(self, responses: list[CompletionResult]) -> None:
        self._responses = list(responses)

    async def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
        if not self._responses:
            raise AssertionError("Unexpected provider.complete() call")
        return self._responses.pop(0)

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("stream disabled in workflow equivalence tests")


@dataclass
class WorkflowRun:
    result: Any
    events: list[dict[str, Any]]


def _make_provider_registry(responses: list[CompletionResult]) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register("default", _SequenceProvider(responses))
    return registry


def _make_executor_registry(tool_registry: ToolRegistry | None = None) -> ExecutorRegistry:
    registry = ExecutorRegistry()
    register_default_executors(registry)
    if tool_registry is not None:
        tool_executor = ToolExecutor(tool_registry)
        registry.register("tool_operator", tool_executor)
        registry.register("worker", WorkerExecutor(tool_executor=tool_executor))
    return registry


def test_default_executor_registry_routes_bridged_legacy_compute_types_through_worker_executor() -> None:
    registry = ExecutorRegistry()
    register_default_executors(registry)

    worker_executor = registry.get("worker")

    assert isinstance(worker_executor, WorkerExecutor)
    for node_type in {
        "llm_operator",
        "tool_operator",
        "code_operator",
        "rag_operator",
        "input",
        "router",
        "human",
        "human_in_the_loop",
        "validator",
        "vote",
        "reflection",
    }:
        adapter = registry.get(node_type)
        assert isinstance(adapter, LegacyWorkerAdapterExecutor)
        assert adapter.worker_executor is worker_executor


async def _run_graph(
    graph: Graph,
    *,
    inputs: dict[str, Any],
    provider_responses: list[CompletionResult],
    tool_registry: ToolRegistry | None = None,
    human_renderer: Any | None = None,
    patch_factory: Any | None = None,
) -> WorkflowRun:
    events: list[EngineEvent] = []

    async def _event_callback(event: EngineEvent) -> None:
        events.append(event)

    with ExitStack() as stack:
        if patch_factory is not None:
            stack.enter_context(patch_factory())

        engine = Engine(
            config=EngineConfig(
                checkpoint_enabled=False,
                memory_enabled=False,
                prompt_caching_enabled=False,
                cache_enabled=False,
                llm_default_model="test-model",
            ),
            checkpoint_store=NullCheckpointStore(),
            executor_registry=_make_executor_registry(tool_registry),
            provider_registry=_make_provider_registry(provider_responses),
            event_callback=_event_callback,
            human_renderer=human_renderer,
        )
        result = await engine.run(graph, inputs=inputs, run_id="workflow-equivalence-run")
    return WorkflowRun(result=result, events=_stable_event_log(events))


def _stable_event_log(events: list[EngineEvent]) -> list[dict[str, Any]]:
    stable: list[dict[str, Any]] = []
    for event in events:
        if event.event_type in {
            EventType.RUN_STARTED,
            EventType.RUN_COMPLETED,
            EventType.RUN_PROGRESS,
            EventType.OPTIMIZATION_REPORT_READY,
        }:
            continue

        data = dict(event.data)
        data.pop("elapsed_ms", None)
        if event.event_type == EventType.NODE_COMPLETED:
            metadata = dict(data.get("metadata", {}))
            metadata.pop("elapsed", None)
            metadata.pop("elapsed_seconds", None)
            metadata.pop("runtime_repair_summary", None)
            data = {"metadata": metadata}

        stable.append({
            "event_type": event.event_type.value,
            "node_id": event.node_id,
            "data": data,
        })
    return stable


def _normalize_behavior_events(events: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
    interesting = {
        EventType.NODE_STARTED.value,
        EventType.NODE_OUTPUT.value,
        EventType.NODE_COMPLETED.value,
    }
    return [
        (event["event_type"], event["node_id"])
        for event in events
        if event["event_type"] in interesting
    ]


def _all_nodes(graph: Graph) -> list[NodeBase]:
    nodes = list(graph.nodes)
    for sub_graph in graph.sub_graphs.values():
        nodes.extend(_all_nodes(sub_graph))
    return nodes


def _assert_compute_nodes_workerized(graph: Graph) -> None:
    retained = {"for_each", "while_loop", "composite", "goal_loop", "gate", "if_else"}
    converted_compute = {"worker"}
    legacy_compute = {"llm_operator", "tool_operator", "code_operator", "input"}
    node_types = {node.node_type for node in _all_nodes(graph)}

    assert legacy_compute.isdisjoint(node_types)
    assert converted_compute & node_types
    assert retained & node_types


def _strip_redundant_result_key(outputs: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(outputs)
    result_value = normalized.get("result")
    if isinstance(result_value, dict):
        explicit = {key: value for key, value in normalized.items() if key != "result"}
        if all(result_value.get(key) == value for key, value in explicit.items()):
            normalized.pop("result", None)
    return normalized


def _paper_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def lookup_reference(section_title: str, brief: str) -> dict[str, Any]:
        return {
            "evidence": f"evidence:{section_title.lower()}",
            "citation": f"cite:{brief.split()[0].lower()}",
        }

    registry.register("lookup_reference", lookup_reference)
    return registry


def _paper_workflow_provider_responses() -> list[CompletionResult]:
    return [
        CompletionResult(
            text="1. Introduction\n2. Managerial Implications",
            usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        ),
        CompletionResult(
            text="Introduction draft about worker contracts.",
            usage={"prompt_tokens": 11, "completion_tokens": 6, "total_tokens": 17},
        ),
        CompletionResult(
            text="Implications draft about applying worker contracts.",
            usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        ),
        CompletionResult(
            text="approved: coherent draft with grounded section coverage",
            usage={"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17},
        ),
    ]


def _build_paper_style_workflow() -> Graph:
    draft_section_body = Graph(
        nodes=[
            CodeOperator(
                id="unpack_task",
                name="Unpack Section Task",
                code=(
                    "result = {\n"
                    "    'section_id': item['section_id'],\n"
                    "    'section_title': item['section_title'],\n"
                    "    'brief': item['brief'],\n"
                    "    'outline': item['outline'],\n"
                    "}"
                ),
                input_ports=[{"name": "item"}],
                output_ports=[
                    {"name": "section_id"},
                    {"name": "section_title"},
                    {"name": "brief"},
                    {"name": "outline"},
                    {"name": "result"},
                ],
            ),
            ToolOperator(
                id="lookup_evidence",
                name="Lookup Evidence",
                tool_id="lookup_reference",
                input_ports=[{"name": "section_title"}, {"name": "brief"}],
                output_ports=[{"name": "evidence"}, {"name": "citation"}, {"name": "result"}],
            ),
            LLMOperator(
                id="draft_section",
                name="Draft Section",
                model="test-model",
                prompt_template=(
                    "Write the {section_title} section for a paper.\n"
                    "Brief: {brief}\n"
                    "Evidence: {evidence}\n"
                    "Outline: {outline}"
                ),
                system_prompt="Write concise academic prose.",
                input_ports=[
                    {"name": "section_title"},
                    {"name": "brief"},
                    {"name": "evidence"},
                    {"name": "outline"},
                ],
                output_ports=[{"name": "text"}],
            ),
            CodeOperator(
                id="package_section",
                name="Package Section",
                code=(
                    "result = {\n"
                    "    'section_id': section_id,\n"
                    "    'section_title': section_title,\n"
                    "    'section_text': text,\n"
                    "    'citations': [citation],\n"
                    "}"
                ),
                input_ports=[
                    {"name": "section_id"},
                    {"name": "section_title"},
                    {"name": "text"},
                    {"name": "citation"},
                ],
                output_ports=[
                    {"name": "section_id"},
                    {"name": "section_title"},
                    {"name": "section_text"},
                    {"name": "citations"},
                    {"name": "result"},
                ],
            ),
        ],
        edges=[
            DataEdge(id="draft-e1", source_node_id="unpack_task", source_port="section_title", target_node_id="lookup_evidence", target_port="section_title"),
            DataEdge(id="draft-e2", source_node_id="unpack_task", source_port="brief", target_node_id="lookup_evidence", target_port="brief"),
            DataEdge(id="draft-e3", source_node_id="unpack_task", source_port="section_title", target_node_id="draft_section", target_port="section_title"),
            DataEdge(id="draft-e4", source_node_id="unpack_task", source_port="brief", target_node_id="draft_section", target_port="brief"),
            DataEdge(id="draft-e5", source_node_id="unpack_task", source_port="outline", target_node_id="draft_section", target_port="outline"),
            DataEdge(id="draft-e6", source_node_id="lookup_evidence", source_port="evidence", target_node_id="draft_section", target_port="evidence"),
            DataEdge(id="draft-e7", source_node_id="unpack_task", source_port="section_id", target_node_id="package_section", target_port="section_id"),
            DataEdge(id="draft-e8", source_node_id="unpack_task", source_port="section_title", target_node_id="package_section", target_port="section_title"),
            DataEdge(id="draft-e9", source_node_id="draft_section", source_port="text", target_node_id="package_section", target_port="text"),
            DataEdge(id="draft-e10", source_node_id="lookup_evidence", source_port="citation", target_node_id="package_section", target_port="citation"),
        ],
        entry_points=["unpack_task"],
        exit_points=["package_section"],
    )

    review_compile_body = Graph(
        nodes=[
            CodeOperator(
                id="assemble_draft",
                name="Assemble Draft",
                code=(
                    "sections = [\n"
                    "    f\"## {section['section_title']}\\n{section['section_text']}\"\n"
                    "    for section in section_results\n"
                    "]\n"
                    "result = {\n"
                    "    'draft': f\"# {topic}\\n\\nOutline:\\n{outline}\\n\\n\" + \"\\n\\n\".join(sections),\n"
                    "    'section_count': len(section_results),\n"
                    "}"
                ),
                input_ports=[{"name": "topic"}, {"name": "outline"}, {"name": "section_results"}],
                output_ports=[{"name": "draft"}, {"name": "section_count"}, {"name": "result"}],
            ),
            LLMOperator(
                id="review_draft",
                name="Review Draft",
                model="test-model",
                prompt_template=(
                    "Review this draft for coherence.\n"
                    "Topic: {topic}\n"
                    "Draft:\n{draft}"
                ),
                system_prompt="Return a short final review verdict.",
                input_ports=[{"name": "topic"}, {"name": "draft"}],
                output_ports=[{"name": "text"}],
            ),
            CodeOperator(
                id="finalize_paper",
                name="Finalize Paper",
                code=(
                    "result = {\n"
                    "    'paper': draft,\n"
                    "    'review_summary': review,\n"
                    "    'section_count': section_count,\n"
                    "    'approved': 'approved' in review.lower(),\n"
                    "}"
                ),
                input_ports=[{"name": "draft"}, {"name": "review"}, {"name": "section_count"}],
                output_ports=[
                    {"name": "paper"},
                    {"name": "review_summary"},
                    {"name": "section_count"},
                    {"name": "approved"},
                    {"name": "result"},
                ],
            ),
        ],
        edges=[
            DataEdge(id="review-e1", source_node_id="assemble_draft", source_port="draft", target_node_id="review_draft", target_port="draft"),
            DataEdge(id="review-e2", source_node_id="assemble_draft", source_port="section_count", target_node_id="finalize_paper", target_port="section_count"),
            DataEdge(id="review-e3", source_node_id="assemble_draft", source_port="draft", target_node_id="finalize_paper", target_port="draft"),
            DataEdge(id="review-e4", source_node_id="review_draft", source_port="text", target_node_id="finalize_paper", target_port="review"),
        ],
        entry_points=["assemble_draft"],
        exit_points=["finalize_paper"],
    )

    return Graph(
        nodes=[
            InputNode(
                id="paper_input",
                name="Paper Inputs",
                variables=[
                    InputVariable(name="topic", type="string", description="Research topic"),
                    InputVariable(name="audience", type="string", description="Target audience"),
                ],
                output_ports=[{"name": "input"}, {"name": "topic"}, {"name": "audience"}],
            ),
            LLMOperator(
                id="outline_paper",
                name="Outline Paper",
                model="test-model",
                prompt_template="Draft an outline for {topic} aimed at {audience}.",
                system_prompt="Return a concise outline.",
                input_ports=[{"name": "topic"}, {"name": "audience"}],
                output_ports=[{"name": "text"}],
            ),
            CodeOperator(
                id="build_section_tasks",
                name="Build Section Tasks",
                code=(
                    "result = {\n"
                    "    'items': [\n"
                    "        {\n"
                    "            'section_id': 'intro',\n"
                    "            'section_title': 'Introduction',\n"
                    "            'brief': f'Introduce {topic} for {audience}',\n"
                    "            'outline': outline,\n"
                    "        },\n"
                    "        {\n"
                    "            'section_id': 'implications',\n"
                    "            'section_title': 'Managerial Implications',\n"
                    "            'brief': f'Explain why {topic} matters for {audience}',\n"
                    "            'outline': outline,\n"
                    "        },\n"
                    "    ]\n"
                    "}"
                ),
                input_ports=[{"name": "outline"}, {"name": "topic"}, {"name": "audience"}],
                output_ports=[{"name": "items"}, {"name": "result"}],
            ),
            ForEachNode(
                id="draft_sections",
                name="Draft Sections",
                body_graph="draft_section_body",
                parallelism=1,
                input_ports=[{"name": "items"}],
                output_ports=[{"name": "results"}],
            ),
            CompositeNode(
                id="review_compile",
                name="Review Compile",
                body_graph="review_compile_body",
                input_ports=[{"name": "topic"}, {"name": "outline"}, {"name": "section_results"}],
                output_ports=[
                    {"name": "paper"},
                    {"name": "review_summary"},
                    {"name": "section_count"},
                    {"name": "approved"},
                    {"name": "result"},
                ],
                input_mappings={"topic": "topic", "outline": "outline", "section_results": "section_results"},
                output_mappings={
                    "paper": "paper",
                    "review_summary": "review_summary",
                    "section_count": "section_count",
                    "approved": "approved",
                    "result": "result",
                },
            ),
        ],
        edges=[
            DataEdge(id="paper-e1", source_node_id="paper_input", source_port="topic", target_node_id="outline_paper", target_port="topic"),
            DataEdge(id="paper-e2", source_node_id="paper_input", source_port="audience", target_node_id="outline_paper", target_port="audience"),
            DataEdge(id="paper-e3", source_node_id="outline_paper", source_port="text", target_node_id="build_section_tasks", target_port="outline"),
            DataEdge(id="paper-e4", source_node_id="paper_input", source_port="topic", target_node_id="build_section_tasks", target_port="topic"),
            DataEdge(id="paper-e5", source_node_id="paper_input", source_port="audience", target_node_id="build_section_tasks", target_port="audience"),
            DataEdge(id="paper-e6", source_node_id="build_section_tasks", source_port="items", target_node_id="draft_sections", target_port="items"),
            DataEdge(id="paper-e7", source_node_id="paper_input", source_port="topic", target_node_id="review_compile", target_port="topic"),
            DataEdge(id="paper-e8", source_node_id="outline_paper", source_port="text", target_node_id="review_compile", target_port="outline"),
            DataEdge(id="paper-e9", source_node_id="draft_sections", source_port="results", target_node_id="review_compile", target_port="section_results"),
        ],
        sub_graphs={
            "draft_section_body": draft_section_body,
            "review_compile_body": review_compile_body,
        },
        entry_points=["paper_input"],
        exit_points=["review_compile"],
    )


def _load_paper_writing_graph() -> Graph:
    root = Path(__file__).resolve().parents[2]
    module_name = "paper_writing_worker_test"
    spec = importlib.util.spec_from_file_location(module_name, root / "examples" / "paper_writing.py")
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load examples/paper_writing.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.build_paper_workflow(max_review_iterations=1, output_dir="output/test-worker")


def _paper_example_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def check_latex_deps(**kwargs: Any) -> dict[str, Any]:
        return {
            "deps_ok": True,
            "missing_commands": [],
            "missing_templates": [],
            "dependency_message": "",
        }

    async def search_papers(
        query: str = "",
        num_results: int = 8,
        aspect: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        paper = {
            "title": f"{aspect or 'Worker'} Evidence Paper",
            "year": 2024,
            "venue": "Management Science",
            "doi": "10.1000/example",
            "paper_id": f"paper-{(aspect or 'worker').lower().replace(' ', '-')}",
        }
        return {
            "aspect": aspect,
            "query": query,
            "papers": [paper],
            "paper_count": 1,
            "source": "mock",
            "error": "",
            "fallback_answer": "",
            "fallback_citations": [],
        }

    async def citation_verifier(
        key_papers: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "verified_papers": list(key_papers or []),
            "invalid_citations": [],
            "verification_notes": "all citations verified",
        }

    async def compile_latex(
        content: str = "",
        title: str = "",
        bibtex: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        slug = title.lower().replace(" ", "_") or "paper"
        return {
            "compile_success": True,
            "compile_attempted": True,
            "compile_blocked": False,
            "artifact_stage": "compiled_pdf",
            "degraded_reasons": [],
            "pdf_path": f"/tmp/{slug}.pdf",
            "compile_log": "Compilation succeeded.",
            "tex_path": f"/tmp/{slug}.tex",
            "bib_path": f"/tmp/{slug}.bib",
        }

    async def save_paper(title: str = "", verdict: str = "", **kwargs: Any) -> dict[str, Any]:
        slug = title.lower().replace(" ", "_") or "paper"
        return {
            "saved_path": f"/tmp/{slug}.tex",
            "title": title,
            "tex_path": f"/tmp/{slug}.tex",
            "bib_path": f"/tmp/{slug}.bib",
            "pdf_path": f"/tmp/{slug}.pdf",
            "compile_log_path": f"/tmp/{slug}.compile.log.txt",
            "summary_path": f"/tmp/{slug}.summary.json",
            "verdict": verdict,
            "artifact_status": "ready",
            "artifact_stage": "compiled_pdf",
            "submission_ready": True,
            "degraded_reasons": [],
        }

    async def package_submission(title: str = "", **kwargs: Any) -> dict[str, Any]:
        slug = title.lower().replace(" ", "_") or "paper"
        return {
            "bundle_path": f"/tmp/{slug}-submission.zip",
            "saved_path": f"/tmp/{slug}-submission.zip",
            "title": title,
            "bundle_kind": "submission",
            "package_status": "ready",
            "submission_ready": True,
            "manifest_path": f"/tmp/{slug}.manifest.json",
            "degraded_reasons": [],
        }

    registry.register("check_latex_deps", check_latex_deps)
    registry.register("search_papers", search_papers)
    registry.register("citation_verifier", citation_verifier)
    registry.register("compile_latex", compile_latex)
    registry.register("save_paper", save_paper)
    registry.register("package_submission", package_submission)
    return registry


def _llm_outputs_for_paper_example(node: LLMOperator, inputs: dict[str, Any]) -> dict[str, Any]:
    if node.id == "idea_gen":
        return {"text": f"Research idea about {inputs.get('topic', 'worker contracts')}"}
    if node.id == "aspect_planner":
        return {
            "aspects": [
                {"name": "coordination", "search_queries": ["worker contract coordination"]},
                {
                    "name": "managerial implications",
                    "search_queries": ["managerial implications of worker contracts"],
                },
            ],
        }
    if node.id == "survey_aspect":
        aspect = str(inputs.get("aspect", "aspect"))
        return {
            "aspect": aspect,
            "summary": f"Grounded survey for {aspect}.",
            "key_findings": [f"{aspect} finding"],
        }
    if node.id == "lit_synthesizer":
        return {
            "literature_review": "Grounded literature review on worker contracts.",
            "identified_gaps": ["Need stronger operational benchmarks."],
            "key_papers": [
                {
                    "title": "Worker Contracts in Multi-Agent Systems",
                    "year": 2024,
                    "venue": "Management Science",
                    "doi": "10.1000/example",
                    "paper_id": "worker-contracts-2024",
                },
            ],
        }
    if node.id in {"claim_evidence_gate", "review_claim_gate"}:
        return {"gate_pass": True, "coverage_score": 0.93, "unsupported_claims": []}
    if node.id == "outline_planner":
        return {
            "title": "Worker Contracts for Deep Agent Networks",
            "abstract": "A compact workflow study of worker contracts.",
            "keywords": ["worker contracts", "deep agent networks"],
            "journal": "mnsc",
            "paper_type": "analytical",
            "sections": [
                {
                    "id": "intro",
                    "title": "Introduction",
                    "purpose": "Frame the worker contract problem.",
                    "key_points": ["Motivation", "Scope"],
                },
                {
                    "id": "implications",
                    "title": "Managerial Implications",
                    "purpose": "Explain operational value.",
                    "key_points": ["Tradeoffs", "Adoption"],
                },
            ],
        }
    if node.id == "interviewer":
        return {
            "question": "What practical constraint should the paper emphasize?",
            "has_questions": True,
            "is_clarification": False,
        }
    if node.id == "interview_refiner":
        return {
            "refined_idea": f"{inputs.get('refined_idea', '')} with practical emphasis".strip(),
            "refined_literature": inputs.get("refined_literature", ""),
            "refined_outline": inputs.get("refined_outline", {}),
            "round_summary": "Captured one practical constraint.",
            "has_questions": False,
            "is_clarification": False,
        }
    if node.id == "write_section":
        section_title = str(inputs.get("section_title", "Section"))
        section_id = str(inputs.get("section_id", section_title.lower().replace(" ", "_")))
        return {
            "section_id": section_id,
            "section_title": section_title,
            "latex": f"\\section{{{section_title}}}\nDrafted content for {section_title}.",
        }
    if node.id == "bibtex_builder":
        return {
            "bibtex": (
                "@article{worker_contracts_2024,\n"
                "  title={Worker Contracts in Deep Agent Networks},\n"
                "  author={Doe, Jane},\n"
                "  journal={Management Science},\n"
                "  year={2024}\n"
                "}"
            ),
        }
    if node.id in {"method_reviewer", "writing_reviewer", "venue_reviewer"}:
        return {
            "feedback": f"{node.id} feedback: acceptable.",
            "recommendation": "accept",
            "section_feedback": [],
        }
    if node.id == "review_merger":
        return {
            "verdict": "accept",
            "overall_feedback": "Accept after one review cycle.",
            "section_feedback": [],
            "revised_outline": inputs.get("outline", {}),
        }
    if node.id == "revise_section":
        return {
            "section_id": str(inputs.get("section_id", "section")),
            "section_title": str(inputs.get("section_title", "Section")),
            "action": str(inputs.get("action", "revise")),
            "latex": str(inputs.get("current_content", "")) or "\\section{Section}\nRevised content.",
        }

    raise AssertionError(f"Unhandled mocked paper-writing LLM node: {node.id}")


async def _mock_paper_example_llm_execute(
    self: Any,
    node: NodeBase,
    inputs: dict[str, Any],
    context: Any,
) -> NodeResult:
    assert isinstance(node, LLMOperator)
    outputs = _llm_outputs_for_paper_example(node, inputs)
    if node.output_json_schema is not None:
        return NodeResult(
            outputs={**outputs, "result": outputs},
            status=NodeStatus.COMPLETED,
            metadata={"mocked": True, "node_id": node.id},
        )
    return NodeResult(
        outputs={"text": outputs["text"]},
        status=NodeStatus.COMPLETED,
        metadata={"mocked": True, "node_id": node.id},
    )


def _paper_example_patch_factory() -> Any:
    from dan.executors.llm import LLMExecutor

    return patch.object(LLMExecutor, "execute", _mock_paper_example_llm_execute)


@pytest.mark.asyncio
async def test_convert_graph_preserves_representative_paper_style_workflow_behavior() -> None:
    legacy_graph = _build_paper_style_workflow()
    worker_graph = convert_graph(legacy_graph)

    _assert_compute_nodes_workerized(worker_graph)

    inputs = {"topic": "worker contracts", "audience": "operators"}
    tool_registry = _paper_tool_registry()

    legacy_run = await _run_graph(
        legacy_graph,
        inputs=inputs,
        provider_responses=_paper_workflow_provider_responses(),
        tool_registry=tool_registry,
    )
    worker_run = await _run_graph(
        worker_graph,
        inputs=inputs,
        provider_responses=_paper_workflow_provider_responses(),
        tool_registry=tool_registry,
    )

    assert worker_run.result.success == legacy_run.result.success
    assert _strip_redundant_result_key(worker_run.result.outputs) == _strip_redundant_result_key(
        legacy_run.result.outputs,
    )
    assert worker_run.result.errors == legacy_run.result.errors
    assert worker_run.result.node_statuses == legacy_run.result.node_statuses
    assert _normalize_behavior_events(worker_run.events) == _normalize_behavior_events(legacy_run.events)


@pytest.mark.asyncio
async def test_convert_graph_preserves_full_paper_writing_example_outputs() -> None:
    legacy_graph = _load_paper_writing_graph()
    worker_graph = convert_graph(legacy_graph)

    _assert_compute_nodes_workerized(worker_graph)

    inputs = {"topic": "worker contracts"}
    tool_registry = _paper_example_tool_registry()
    human_renderer = ProgrammaticRenderer(
        {"human_interview": {"response": "Emphasize operational implications."}},
    )

    legacy_run = await _run_graph(
        legacy_graph,
        inputs=inputs,
        provider_responses=[],
        tool_registry=tool_registry,
        human_renderer=human_renderer,
        patch_factory=_paper_example_patch_factory,
    )
    worker_run = await _run_graph(
        worker_graph,
        inputs=inputs,
        provider_responses=[],
        tool_registry=tool_registry,
        human_renderer=human_renderer,
        patch_factory=_paper_example_patch_factory,
    )

    assert worker_run.result.success == legacy_run.result.success
    assert _strip_redundant_result_key(worker_run.result.outputs) == _strip_redundant_result_key(
        legacy_run.result.outputs,
    )
    assert worker_run.result.errors == legacy_run.result.errors
    assert worker_run.result.node_statuses == legacy_run.result.node_statuses
