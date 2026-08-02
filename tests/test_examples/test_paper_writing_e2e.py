"""End-to-end tests for the grounded paper-writing workflow."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from dan.engine import Engine, EngineConfig
from dan.engine.checkpoint import FileSystemCheckpointStore, NullCheckpointStore
from dan.engine.executor import ExecutorRegistry, NodeResult
from dan.engine.state import NodeStatus
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.graph import Graph
from dan.models.nodes import LLMOperator, NodeBase

# Import from examples/paper_writing.py
sys_path_inserted = False
examples_dir = os.path.join(os.path.dirname(__file__), "..", "..", "examples")
if examples_dir not in sys.path:
    sys.path.insert(0, examples_dir)
    sys_path_inserted = True

from paper_writing import (
    build,
    build_paper_workflow,
    compile_latex,
    package_submission,
    save_paper,
    search_papers,
)

if sys_path_inserted:
    sys.path.remove(examples_dir)


MOCK_LLM_RESPONSES: dict[str, dict[str, Any]] = {
    "idea_gen": {
        "text": "A study on resilient operations under climate uncertainty."
    },
    "aspect_planner": {
        "aspects": [
            {"name": "methodology", "search_queries": ["resilience optimization operations management"]},
            {"name": "applications", "search_queries": ["supply chain resilience empirical evidence"]},
        ]
    },
    "lit_synthesizer": {
        "literature_review": "Grounded review across methodology and applications.",
        "identified_gaps": ["Limited integration of empirical and analytical evidence."],
        "key_papers": [
            {
                "title": "Resilience in Operations Networks",
                "year": 2022,
                "venue": "Management Science",
                "doi": "10.1000/mockdoi",
                "paper_id": "paper-1",
            }
        ],
    },
    "claim_evidence_gate": {
        "gate_pass": True,
        "coverage_score": 0.92,
        "unsupported_claims": [],
    },
    "outline_planner": {
        "title": "Resilient Operations Under Climate Uncertainty",
        "abstract": "We develop and evaluate resilient operations policies.",
        "keywords": ["resilience", "operations", "supply chain"],
        "journal": "mnsc",
        "paper_type": "analytical",
        "sections": [
            {
                "id": "sec_1",
                "title": "Introduction",
                "purpose": "Motivate and position contribution.",
                "key_points": ["motivation", "gap", "contribution"],
            },
            {
                "id": "sec_2",
                "title": "Model",
                "purpose": "Define analytical setup.",
                "key_points": ["assumptions", "notation", "decisions"],
            },
        ],
    },
    "interviewer": {
        "question": "Any specific empirical setting you want emphasized?",
        "has_questions": False,
        "is_clarification": False,
    },
    "interview_refiner": {
        "refined_idea": "Refined resilient operations idea.",
        "refined_literature": "Refined grounded literature summary.",
        "refined_outline": {
            "title": "Resilient Operations Under Climate Uncertainty",
            "abstract": "Refined abstract.",
            "keywords": ["resilience", "operations"],
            "journal": "mnsc",
            "paper_type": "analytical",
            "sections": [
                {"id": "sec_1", "title": "Introduction", "purpose": "Motivate", "key_points": []},
                {"id": "sec_2", "title": "Model", "purpose": "Setup", "key_points": []},
            ],
        },
        "round_summary": "No further clarification needed.",
        "has_questions": False,
        "is_clarification": False,
    },
    "bibtex_builder": {
        "bibtex": (
            "@article{resilience2022,\n"
            "  title={Resilience in Operations Networks},\n"
            "  author={Doe, Jane},\n"
            "  journal={Management Science},\n"
            "  year={2022},\n"
            "  doi={10.1000/mockdoi}\n"
            "}\n"
        )
    },
    "method_reviewer": {
        "feedback": "Method is coherent.",
        "recommendation": "accept",
        "section_feedback": [],
    },
    "writing_reviewer": {
        "feedback": "Writing is clear.",
        "recommendation": "accept",
        "section_feedback": [],
    },
    "venue_reviewer": {
        "feedback": "Good INFORMS fit.",
        "recommendation": "accept",
        "section_feedback": [],
    },
    "review_merger": {
        "verdict": "accept",
        "overall_feedback": "Panel agrees this is acceptable.",
        "section_feedback": [],
        "revised_outline": {},
    },
    "review_claim_gate": {
        "gate_pass": True,
        "coverage_score": 0.95,
        "unsupported_claims": [],
    },
}


def _all_nodes(graph: Graph) -> list[NodeBase]:
    nodes = list(graph.nodes)
    for sub_graph in graph.sub_graphs.values():
        nodes.extend(_all_nodes(sub_graph))
    return nodes


class MockLLMExecutor:
    """Deterministic mock executor keyed by node.id."""

    def __init__(self, responses: dict[str, dict[str, Any]] | None = None) -> None:
        self._responses = responses or MOCK_LLM_RESPONSES

    async def execute(
        self, node: NodeBase, inputs: dict[str, Any], context: Any
    ) -> NodeResult:
        assert isinstance(node, LLMOperator)

        # Dynamic nodes used inside ForEach loops
        if node.id == "survey_aspect":
            response = {
                "aspect": str(inputs.get("aspect", "general")),
                "summary": "Grounded summary for this aspect.",
                "key_findings": ["finding_a", "finding_b"],
            }
            return NodeResult(outputs=response, status=NodeStatus.COMPLETED)

        if node.id == "write_section":
            sid = str(inputs.get("section_id", "sec_1"))
            title = str(inputs.get("section_title", "Section"))
            response = {
                "section_id": sid,
                "section_title": title,
                "latex": (
                    f"\\section{{{title}}}\n\n"
                    "This section presents grounded argumentation and cites prior work."
                ),
            }
            return NodeResult(outputs=response, status=NodeStatus.COMPLETED)

        if node.id == "revise_section":
            sid = str(inputs.get("section_id", "sec_1"))
            title = str(inputs.get("section_title", "Section"))
            action = str(inputs.get("action", "revise"))
            response = {
                "section_id": sid,
                "section_title": title,
                "action": action,
                "latex": (
                    f"\\section{{{title}}}\n\n"
                    "Revised content incorporating reviewer feedback."
                ),
            }
            return NodeResult(outputs=response, status=NodeStatus.COMPLETED)

        response = self._responses.get(node.id)
        if response is None:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"No mock response for node '{node.id}'",
            )

        if node.output_json_schema is not None:
            return NodeResult(outputs=dict(response), status=NodeStatus.COMPLETED)

        return NodeResult(
            outputs={"text": response.get("text", str(response))},
            status=NodeStatus.COMPLETED,
        )


async def _mock_check_latex_deps(**kwargs: Any) -> dict[str, Any]:
    return {
        "deps_ok": True,
        "missing_commands": [],
        "missing_templates": [],
        "dependency_message": "ready",
    }


async def _mock_search_papers(
    query: str,
    num_results: int = 8,
    aspect: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    return {
        "aspect": aspect,
        "query": query,
        "papers": [
            {
                "title": "Mock Paper",
                "year": 2021,
                "venue": "Operations Research",
                "abstract": "Mock abstract",
                "citation_count": 12,
                "paper_id": "mock-paper-id",
                "doi": "10.1000/mockpaper",
            }
        ],
        "paper_count": 1,
        "source": "semantic_scholar",
        "error": "",
        "fallback_answer": "",
        "fallback_citations": [],
    }


async def _mock_citation_verifier(
    key_papers: list[Any] | None = None, **kwargs: Any
) -> dict[str, Any]:
    key_papers = key_papers if isinstance(key_papers, list) else []
    verified = []
    for row in key_papers:
        if isinstance(row, dict) and row.get("title"):
            verified.append(
                {
                    "title": str(row.get("title")),
                    "year": row.get("year"),
                    "venue": str(row.get("venue", "")),
                    "doi": str(row.get("doi", "")),
                    "paper_id": str(row.get("paper_id", "")),
                }
            )
    return {
        "verified_papers": verified,
        "invalid_citations": [],
        "verification_notes": f"Verified {len(verified)} papers.",
    }


async def _mock_compile_latex(
    content: str,
    title: str,
    bibtex: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    return {
        "compile_success": True,
        "compile_attempted": True,
        "compile_blocked": False,
        "artifact_stage": "compiled_pdf",
        "degraded_reasons": [],
        "pdf_path": "output/mock.pdf",
        "compile_log": "mock compile ok",
        "tex_path": "output/mock.tex",
        "bib_path": "output/mock.bib",
    }


async def _mock_save_paper(
    content: str = "",
    title: str = "",
    bibtex: str = "",
    pdf_path: str = "",
    compile_log: str = "",
    verdict: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    slug = title.lower().replace(" ", "-")
    degraded = not bool(content and title)
    return {
        "saved_path": f"output/{slug}.tex",
        "title": title,
        "tex_path": f"output/{slug}.tex",
        "bib_path": f"output/{slug}.bib",
        "pdf_path": "",
        "compile_log_path": f"output/{slug}.compile.log.txt",
        "summary_path": f"output/{slug}.summary.json",
        "verdict": verdict,
        "artifact_status": "degraded" if degraded else "ready",
        "artifact_stage": "latex_source" if degraded else "compiled_pdf",
        "submission_ready": not degraded,
        "degraded_reasons": ["upstream_prerequisite_missing"] if degraded else [],
    }


async def _mock_package_submission(
    title: str = "",
    tex_path: str = "",
    bib_path: str = "",
    pdf_path: str = "",
    compile_log_path: str = "",
    summary_path: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    slug = title.lower().replace(" ", "-")
    degraded = not bool(title and tex_path and bib_path)
    return {
        "bundle_path": f"output/{slug}-{'artifacts' if degraded else 'submission'}.zip",
        "saved_path": f"output/{slug}-{'artifacts' if degraded else 'submission'}.zip",
        "title": title,
        "bundle_kind": "artifacts" if degraded else "submission",
        "package_status": "degraded" if degraded else "ready",
        "submission_ready": not degraded,
        "manifest_path": f"output/{slug}.manifest.json",
        "degraded_reasons": ["upstream_prerequisite_missing"] if degraded else [],
    }


def _build_mock_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("check_latex_deps", _mock_check_latex_deps)
    registry.register("search_papers", _mock_search_papers)
    registry.register("citation_verifier", _mock_citation_verifier)
    registry.register("compile_latex", _mock_compile_latex)
    registry.register("save_paper", _mock_save_paper)
    registry.register("package_submission", _mock_package_submission)
    return registry


def _build_mock_engine(
    tool_registry: ToolRegistry | None = None,
    checkpoint_store: Any = None,
    mock_llm: MockLLMExecutor | None = None,
) -> Engine:
    from dan.worker.executor import WorkerExecutor

    config = EngineConfig(checkpoint_enabled=False)
    tool_registry = tool_registry or _build_mock_tool_registry()
    llm_executor = mock_llm or MockLLMExecutor()
    tool_executor = ToolExecutor(tool_registry)
    exec_registry = ExecutorRegistry()
    exec_registry.register("llm_operator", llm_executor)
    exec_registry.register("tool_operator", tool_executor)
    exec_registry.register(
        "worker",
        WorkerExecutor(llm_executor=llm_executor, tool_executor=tool_executor),
    )
    return Engine(
        config=config,
        executor_registry=exec_registry,
        checkpoint_store=checkpoint_store or NullCheckpointStore(),
    )


class TestPaperWritingGraphShape:
    def test_build_entrypoint_returns_graph(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DAN_PAPER_WRITING_MODEL_PROFILE", "smoke")
        graph = build()
        assert graph.version == "dan_graph_v1"
        assert graph.metadata.name == "paper_writing"

    def test_graph_compiles_and_has_expected_structure(self):
        graph = build_paper_workflow(max_review_iterations=2)
        assert graph.version == "dan_graph_v1"
        node_ids = {n.id for n in graph.nodes}
        assert {
            "idea_gen",
            "aspect_planner",
            "lit_search",
            "interview_loop",
            "section_writers",
            "review_loop",
            "save_paper",
            "package_submission",
        }.issubset(node_ids)

        assert "lit_search_body" in graph.sub_graphs
        assert "interview_loop_body" in graph.sub_graphs
        assert "section_writers_body" in graph.sub_graphs
        assert "review_loop_body" in graph.sub_graphs

        review_sub = graph.sub_graphs["review_loop_body"]
        assert "section_revisers_body" in review_sub.sub_graphs

    def test_entry_exit_points(self):
        graph = build_paper_workflow()
        assert "idea_gen" in graph.entry_points
        assert "package_submission" in graph.exit_points

    def test_json_round_trip(self):
        graph = build_paper_workflow()
        restored = Graph.model_validate_json(graph.model_dump_json())
        assert restored.version == "dan_graph_v1"
        assert len(restored.nodes) == len(graph.nodes)
        assert set(restored.sub_graphs.keys()) == set(graph.sub_graphs.keys())

    def test_model_profile_can_target_roles_and_nodes(self):
        graph = build_paper_workflow(
            model_profile={
                "default": "base-model",
                "ideation": "idea-model",
                "research": "research-model",
                "reasoning": "reasoning-model",
                "planning": "planning-model",
                "draft": "draft-model",
                "review": "review-model",
            },
            node_model_overrides={"write_section": "section-model"},
        )
        node_models = {
            node.id: getattr(node, "model", "")
            for node in _all_nodes(graph)
            if getattr(node, "model", "")
        }
        assert node_models["idea_gen"] == "idea-model"
        assert node_models["claim_evidence_gate"] == "reasoning-model"
        assert node_models["method_reviewer"] == "review-model"
        assert node_models["write_section"] == "section-model"

    @pytest.mark.asyncio
    async def test_search_papers_uses_configured_web_search_model(self):
        async def _fail_fetch_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("offline")

        async def _fake_search_web(query: str, model: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "query": query,
                "source": "openrouter",
                "available": True,
                "answer": f"model={model}",
                "citations": [],
                "error": "",
            }

        with patch.dict(
            search_papers.__globals__,
            {
                "_fetch_json": _fail_fetch_json,
                "search_web": _fake_search_web,
            },
        ):
            result = await search_papers(
                query="resilient operations",
                allow_web_fallback=True,
                web_search_model="perplexity/sonar-small-online",
            )

        assert result["fallback_answer"] == "model=perplexity/sonar-small-online"


class TestPaperWritingHappyPath:
    @pytest.mark.asyncio
    async def test_full_run_mock(self):
        graph = build_paper_workflow(max_review_iterations=2)
        engine = _build_mock_engine()
        result = await engine.run(graph, inputs={"topic": "supply chain resilience"})

        assert result.success, f"Run failed: {result.errors}"
        assert result.outputs.get("bundle_path")
        assert result.outputs.get("saved_path")
        assert result.outputs.get("title")
        assert result.outputs.get("package_status") == "ready"
        assert result.outputs.get("bundle_kind") == "submission"
        assert result.outputs.get("submission_ready") is True

        completed = {nid for nid, s in result.node_statuses.items() if s == "completed"}
        assert "lit_search" in completed
        assert "interview_loop" in completed
        assert "section_writers" in completed
        assert "review_loop" in completed
        assert "save_paper" in completed
        assert "package_submission" in completed

    @pytest.mark.asyncio
    async def test_parallel_for_each_metadata_present(self):
        graph = build_paper_workflow(max_review_iterations=1)
        engine = _build_mock_engine()
        result = await engine.run(graph, inputs={"topic": "topic"})
        assert result.success
        lit_meta = result.metadata.get("lit_search", {})
        assert int(lit_meta.get("total_items", 0)) >= 1


class TestToolFailureRecovery:
    @pytest.mark.asyncio
    async def test_save_tool_failure_marks_failed(self):
        async def failing_save(**kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("Disk full - simulated failure")

        registry = _build_mock_tool_registry()
        registry.register("save_paper", failing_save)

        graph = build_paper_workflow(max_review_iterations=1)
        engine = _build_mock_engine(tool_registry=registry)
        result = await engine.run(graph, inputs={"topic": "failure path"})

        assert not result.success
        assert result.node_statuses.get("save_paper") == "failed"
        assert "save_paper" in result.errors
        assert "Disk full" in result.errors["save_paper"]

    @pytest.mark.asyncio
    async def test_unknown_tools_fail_gracefully(self):
        graph = build_paper_workflow(max_review_iterations=1)
        engine = _build_mock_engine(tool_registry=ToolRegistry())
        result = await engine.run(graph, inputs={"topic": "unknown tool"})
        assert not result.success
        # First tool entrypoint should fail fast when registry is empty.
        assert result.node_statuses.get("check_latex_deps") == "failed"

    @pytest.mark.asyncio
    async def test_failed_search_skips_downstream_but_keeps_artifact_contract(self):
        async def failing_search_papers(**kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("search backend offline")

        registry = _build_mock_tool_registry()
        registry.register("search_papers", failing_search_papers)

        graph = build_paper_workflow(max_review_iterations=1)
        engine = _build_mock_engine(tool_registry=registry)
        result = await engine.run(graph, inputs={"topic": "blocked search"})

        assert not result.success
        assert result.node_statuses.get("lit_search") == "completed"
        assert result.node_statuses.get("require_grounded_literature") == "failed"
        assert result.node_statuses.get("lit_synthesizer") == "skipped"
        assert result.node_statuses.get("section_writers") == "skipped"
        assert result.node_statuses.get("save_paper") == "completed"
        assert result.node_statuses.get("package_submission") == "completed"
        assert result.outputs.get("package_status") == "degraded"
        assert result.outputs.get("bundle_kind") == "artifacts"

    @pytest.mark.asyncio
    async def test_compile_latex_reports_blocked_dependencies(self, tmp_path: Path):
        result = await compile_latex(
            content="\\documentclass{article}\\begin{document}Hi\\end{document}",
            title="Blocked compile",
            output_dir=str(tmp_path),
            deps_ok=False,
            missing_commands=["pdflatex"],
            dependency_message="Missing commands: pdflatex",
        )

        assert result["compile_success"] is False
        assert result["compile_attempted"] is False
        assert result["compile_blocked"] is True
        assert result["artifact_stage"] == "latex_source"
        assert "missing_latex_dependencies" in result["degraded_reasons"]
        assert Path(result["tex_path"]).exists()
        assert "Missing commands: pdflatex" in result["compile_log"]

    @pytest.mark.asyncio
    async def test_save_and_package_degraded_artifacts(self, tmp_path: Path):
        saved = await save_paper(
            content="\\documentclass{article}\\begin{document}Hi\\end{document}",
            title="Degraded run",
            bibtex="",
            pdf_path="",
            compile_log="compile skipped",
            verdict="revise_sections",
            compile_success=False,
            compile_attempted=False,
            compile_blocked=True,
            artifact_stage="latex_source",
            degraded_reasons=["missing_latex_dependencies"],
            evidence_gate_pass=False,
            dependency_message="Missing commands: pdflatex",
            output_dir=str(tmp_path),
        )

        summary = json.loads(Path(saved["summary_path"]).read_text(encoding="utf-8"))
        assert summary["artifact_status"] == "degraded"
        assert summary["artifact_stage"] == "latex_source"
        assert summary["submission_ready"] is False
        assert summary["model_profile"] == ""
        assert summary["model_selection"] == {}
        assert "missing_latex_dependencies" in summary["degraded_reasons"]
        assert "review_not_accepted:revise_sections" in summary["degraded_reasons"]
        assert "evidence_gate_failed" in summary["degraded_reasons"]

        bundled = await package_submission(
            title="Degraded run",
            tex_path=saved["tex_path"],
            bib_path=saved["bib_path"],
            pdf_path=saved["pdf_path"],
            compile_log_path=saved["compile_log_path"],
            summary_path=saved["summary_path"],
            output_dir=str(tmp_path),
        )

        manifest = json.loads(Path(bundled["manifest_path"]).read_text(encoding="utf-8"))
        assert bundled["bundle_kind"] == "artifacts"
        assert bundled["package_status"] == "degraded"
        assert bundled["submission_ready"] is False
        assert "missing_latex_dependencies" in bundled["degraded_reasons"]
        assert manifest["bundle_kind"] == "artifacts"
        assert manifest["submission_ready"] is False


class TestCheckpointResume:
    @pytest.mark.asyncio
    async def test_resume_after_completed_run(self, tmp_path: Path):
        from dan.worker.executor import WorkerExecutor

        graph = build_paper_workflow(max_review_iterations=1)

        checkpoint_dir = tmp_path / "checkpoints"
        checkpoint_dir.mkdir()
        checkpoint_store = FileSystemCheckpointStore(str(checkpoint_dir))
        config = EngineConfig(checkpoint_enabled=True)

        llm_executor = MockLLMExecutor()
        tool_executor = ToolExecutor(_build_mock_tool_registry())
        exec_registry = ExecutorRegistry()
        exec_registry.register("llm_operator", llm_executor)
        exec_registry.register("tool_operator", tool_executor)
        exec_registry.register(
            "worker",
            WorkerExecutor(llm_executor=llm_executor, tool_executor=tool_executor),
        )

        engine = Engine(
            config=config,
            executor_registry=exec_registry,
            checkpoint_store=checkpoint_store,
        )

        result = await engine.run(graph, inputs={"topic": "checkpoint test"})
        assert result.success, f"Initial run failed: {result.errors}"
        assert list(checkpoint_dir.iterdir()), "No checkpoint files created"

        engine2 = Engine(
            config=config,
            executor_registry=exec_registry,
            checkpoint_store=checkpoint_store,
        )
        resumed = await engine2.resume(graph, run_id=result.run_id)
        assert resumed.success, f"Resume failed: {resumed.errors}"
