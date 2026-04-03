"""Deterministic in-process benchmark for WorkerExecutor dispatch overhead.

Usage:
    PYTHONPATH=src python -m tests.eval.worker_dispatch_benchmark --repeats 200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dan.engine import Engine, EngineConfig
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.executor import ExecutorRegistry, NodeResult
from dan.engine.state import NodeStatus
from dan.executors.code import CodeExecutor
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.context import MergeStrategy
from dan.models.control_flow import CompositeNode, ParallelSubagentsNode
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator, LLMOperator, ToolOperator
from dan.models.ports import InputPort, OutputPort
from dan.worker.model import Worker
from dan.worker.executor import WorkerExecutor
from dan.worker.presets import convert_graph, validate_conversion

RESULTS_DIR = Path(__file__).parent / "results"


class _StubLLMExecutor:
    async def execute(self, node, inputs, context) -> NodeResult:
        value = inputs.get("input")
        if value is None:
            value = inputs.get("result")
        if value is None and inputs:
            value = next(iter(inputs.values()))
        return NodeResult(
            outputs={"text": f"{node.id}:{value}"},
            status=NodeStatus.COMPLETED,
        )


@dataclass(frozen=True)
class DispatchBenchmarkCase:
    case_id: str
    graph: Graph
    inputs: dict[str, Any]
    worker_graph: Graph | None = None


def _engine() -> Engine:
    tool_registry = ToolRegistry()

    async def prefix_tool(input: str = "", prefix: str = "") -> dict[str, str]:
        return {"result": f"{prefix}{input}"}

    tool_registry.register("prefix_tool", prefix_tool)

    llm_executor = _StubLLMExecutor()
    registry = ExecutorRegistry()
    registry.register("code_operator", CodeExecutor())
    registry.register("tool_operator", ToolExecutor(tool_registry))
    registry.register("llm_operator", llm_executor)
    registry.register(
        "worker",
        WorkerExecutor(
            llm_executor=llm_executor,
            tool_executor=ToolExecutor(tool_registry),
            code_executor=CodeExecutor(),
        ),
    )
    return Engine(
        config=EngineConfig(
            llm_api_key="test",
            llm_base_url="http://localhost:1",
            llm_default_model="stub-model",
            checkpoint_enabled=False,
        ),
        checkpoint_store=NullCheckpointStore(),
        executor_registry=registry,
    )


def _code_case() -> DispatchBenchmarkCase:
    graph = Graph(
        nodes=[
            CodeOperator(
                id="format",
                name="Format",
                code="result = input.upper()",
                input_ports=[InputPort(name="input", required=False)],
                output_ports=[OutputPort(name="result")],
            ),
        ],
        edges=[],
        entry_points=["format"],
        exit_points=["format"],
    )
    return DispatchBenchmarkCase("code_only", graph, {"input": "hello"})


def _tool_case() -> DispatchBenchmarkCase:
    graph = Graph(
        nodes=[
            ToolOperator(
                id="fetch",
                name="Fetch",
                tool_id="prefix_tool",
                tool_config={"prefix": "tool:"},
                input_ports=[InputPort(name="input", required=False)],
                output_ports=[OutputPort(name="result")],
            ),
        ],
        edges=[],
        entry_points=["fetch"],
        exit_points=["fetch"],
    )
    return DispatchBenchmarkCase("tool_only", graph, {"input": "hello"})


def _llm_case() -> DispatchBenchmarkCase:
    graph = Graph(
        nodes=[
            LLMOperator(
                id="draft",
                name="Draft",
                model="stub-model",
                prompt_template="Draft {input}",
                input_ports=[InputPort(name="input", required=False)],
                output_ports=[OutputPort(name="text")],
            ),
        ],
        edges=[],
        entry_points=["draft"],
        exit_points=["draft"],
    )
    return DispatchBenchmarkCase("llm_only", graph, {"input": "hello"})


def _chain_case() -> DispatchBenchmarkCase:
    graph = Graph(
        nodes=[
            CodeOperator(
                id="code",
                name="Code",
                code="result = input.upper()",
                input_ports=[InputPort(name="input", required=False)],
                output_ports=[OutputPort(name="result")],
            ),
            ToolOperator(
                id="tool",
                name="Tool",
                tool_id="prefix_tool",
                tool_config={"prefix": "tool:"},
                input_ports=[InputPort(name="input", required=False)],
                output_ports=[OutputPort(name="result")],
            ),
            LLMOperator(
                id="llm",
                name="LLM",
                model="stub-model",
                prompt_template="Draft {input}",
                input_ports=[InputPort(name="input", required=False)],
                output_ports=[OutputPort(name="text")],
            ),
        ],
        edges=[
            DataEdge(
                id="code_to_tool",
                source_node_id="code",
                source_port="result",
                target_node_id="tool",
                target_port="input",
            ),
            DataEdge(
                id="tool_to_llm",
                source_node_id="tool",
                source_port="result",
                target_node_id="llm",
                target_port="input",
            ),
        ],
        entry_points=["code"],
        exit_points=["llm"],
    )
    return DispatchBenchmarkCase("code_tool_llm_chain", graph, {"input": "hello"})


def _composite_mapping_case() -> DispatchBenchmarkCase:
    body_graph = Graph(
        nodes=[
            CodeOperator(
                id="rewrite",
                name="Rewrite",
                code="result = {'summary': body_draft.upper()}",
                input_ports=[InputPort(name="body_draft", required=False)],
                output_ports=[OutputPort(name="summary")],
            ),
        ],
        edges=[],
        entry_points=["rewrite"],
        exit_points=["rewrite"],
    )
    legacy_graph = Graph(
        nodes=[
            CompositeNode(
                id="compose",
                name="Compose",
                body_graph="compose_body",
                input_ports=[
                    InputPort(name="draft", required=False),
                    InputPort(name="context", required=False),
                ],
                output_ports=[OutputPort(name="result")],
                input_mappings={"draft": "rewrite::body_draft", "context": "shared_context"},
                output_mappings={"summary": "result"},
            ),
        ],
        edges=[],
        sub_graphs={"compose_body": body_graph},
        entry_points=["compose"],
        exit_points=["compose"],
    )
    worker_graph = Graph(
        nodes=[
            Worker(
                id="compose",
                name="Compose",
                body_graph="compose_body",
                input_ports=[
                    InputPort(name="draft", required=False),
                    InputPort(name="context", required=False),
                ],
                output_ports=[OutputPort(name="result")],
                input_mappings={"draft": "rewrite::body_draft", "context": "shared_context"},
                output_mappings={"summary": "result"},
            ),
        ],
        edges=[],
        sub_graphs={"compose_body": body_graph},
        entry_points=["compose"],
        exit_points=["compose"],
    )
    return DispatchBenchmarkCase(
        "composite_body_graph_mapping",
        legacy_graph,
        {"draft": "hello", "context": {"topic": "agents"}},
        worker_graph=worker_graph,
    )


def _subworker_merge_case() -> DispatchBenchmarkCase:
    left_graph = Graph(
        nodes=[
            CodeOperator(
                id="left",
                name="Left",
                code="result = {'summary': 'left', 'sources': 3}",
                output_ports=[OutputPort(name="summary"), OutputPort(name="sources")],
            ),
        ],
        edges=[],
        entry_points=["left"],
        exit_points=["left"],
    )
    right_graph = Graph(
        nodes=[
            CodeOperator(
                id="right",
                name="Right",
                code="result = {'summary': 'right', 'approved': True}",
                output_ports=[OutputPort(name="summary"), OutputPort(name="approved")],
            ),
        ],
        edges=[],
        entry_points=["right"],
        exit_points=["right"],
    )
    normalizer = CodeOperator(
        id="normalize",
        name="Normalize",
        code="result = {k: v for k, v in input.items() if k != 'result'}",
        input_ports=[InputPort(name="input", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    legacy_graph = Graph(
        nodes=[
            ParallelSubagentsNode(
                id="fan",
                name="Fan",
                branch_graphs=["fan_left", "fan_right"],
                parallelism=2,
                merge_strategy=MergeStrategy.LAST_WRITE_WINS,
                output_ports=[OutputPort(name="results")],
            ),
            normalizer.model_copy(deep=True),
        ],
        edges=[
            DataEdge(
                id="fan_to_normalize",
                source_node_id="fan",
                source_port="results",
                target_node_id="normalize",
                target_port="input",
            ),
        ],
        sub_graphs={"fan_left": left_graph, "fan_right": right_graph},
        entry_points=["fan"],
        exit_points=["normalize"],
    )
    worker_graph = Graph(
        nodes=[
            Worker(
                id="fan",
                name="Fan",
                authority="delegate",
                sub_workers={"left": "fan_left", "right": "fan_right"},
                authority_policy={"allow_delegate": True},
                parallelism=2,
                merge_strategy=MergeStrategy.LAST_WRITE_WINS,
                output_ports=[OutputPort(name="result")],
            ),
            normalizer.model_copy(deep=True),
        ],
        edges=[
            DataEdge(
                id="fan_to_normalize",
                source_node_id="fan",
                source_port="result",
                target_node_id="normalize",
                target_port="input",
            ),
        ],
        sub_graphs={"fan_left": left_graph, "fan_right": right_graph},
        entry_points=["fan"],
        exit_points=["normalize"],
    )
    return DispatchBenchmarkCase(
        "subworker_last_write_wins",
        legacy_graph,
        {"input": "hello"},
        worker_graph=worker_graph,
    )


def _benchmark_cases() -> list[DispatchBenchmarkCase]:
    return [
        _code_case(),
        _tool_case(),
        _llm_case(),
        _chain_case(),
        _composite_mapping_case(),
        _subworker_merge_case(),
    ]


def _summarize_samples(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    p95_index = max(0, int(len(ordered) * 0.95) - 1)
    return {
        "mean_ms": statistics.fmean(samples),
        "median_ms": statistics.median(samples),
        "min_ms": ordered[0],
        "p95_ms": ordered[p95_index],
        "max_ms": ordered[-1],
    }


async def _time_case_pair(
    legacy_graph: Graph,
    worker_graph: Graph,
    inputs: dict[str, Any],
    *,
    warmup: int,
    repeats: int,
) -> tuple[dict[str, float], dict[str, float]]:
    legacy_engine = _engine()
    worker_engine = _engine()

    for index in range(warmup):
        if index % 2 == 0:
            legacy_result = await legacy_engine.run(legacy_graph, inputs=inputs)
            worker_result = await worker_engine.run(worker_graph, inputs=inputs)
        else:
            worker_result = await worker_engine.run(worker_graph, inputs=inputs)
            legacy_result = await legacy_engine.run(legacy_graph, inputs=inputs)
        assert legacy_result.success is True
        assert worker_result.success is True

    legacy_samples: list[float] = []
    worker_samples: list[float] = []
    for index in range(repeats):
        if index % 2 == 0:
            started = time.perf_counter()
            legacy_result = await legacy_engine.run(legacy_graph, inputs=inputs)
            legacy_samples.append((time.perf_counter() - started) * 1000.0)

            started = time.perf_counter()
            worker_result = await worker_engine.run(worker_graph, inputs=inputs)
            worker_samples.append((time.perf_counter() - started) * 1000.0)
        else:
            started = time.perf_counter()
            worker_result = await worker_engine.run(worker_graph, inputs=inputs)
            worker_samples.append((time.perf_counter() - started) * 1000.0)

            started = time.perf_counter()
            legacy_result = await legacy_engine.run(legacy_graph, inputs=inputs)
            legacy_samples.append((time.perf_counter() - started) * 1000.0)

        assert legacy_result.success is True
        assert worker_result.success is True

    return _summarize_samples(legacy_samples), _summarize_samples(worker_samples)


async def _run_case(
    case: DispatchBenchmarkCase,
    *,
    warmup: int,
    repeats: int,
    max_overhead_ratio: float,
) -> dict[str, Any]:
    worker_graph = case.worker_graph
    conversion: list[str] = []
    if worker_graph is None:
        worker_graph = convert_graph(case.graph)
        conversion = validate_conversion(case.graph, worker_graph)
        assert conversion == []

    legacy_probe = await _engine().run(case.graph, inputs=case.inputs)
    worker_probe = await _engine().run(worker_graph, inputs=case.inputs)
    assert legacy_probe.success is True
    assert worker_probe.success is True
    assert legacy_probe.outputs == worker_probe.outputs

    legacy_stats, worker_stats = await _time_case_pair(
        case.graph,
        worker_graph,
        case.inputs,
        warmup=warmup,
        repeats=repeats,
    )

    median_ratio = worker_stats["median_ms"] / legacy_stats["median_ms"] if legacy_stats["median_ms"] else 0.0
    mean_ratio = worker_stats["mean_ms"] / legacy_stats["mean_ms"] if legacy_stats["mean_ms"] else 0.0

    return {
        "case_id": case.case_id,
        "legacy": legacy_stats,
        "worker": worker_stats,
        "median_overhead_ratio": median_ratio,
        "mean_overhead_ratio": mean_ratio,
        "passes_gate": median_ratio <= max_overhead_ratio,
        "output": legacy_probe.outputs,
        "conversion_validation_errors": conversion,
    }


async def _run_benchmark(
    *,
    warmup: int,
    repeats: int,
    max_overhead_ratio: float,
) -> dict[str, Any]:
    cases = []
    for case in _benchmark_cases():
        cases.append(
            await _run_case(
                case,
                warmup=warmup,
                repeats=repeats,
                max_overhead_ratio=max_overhead_ratio,
            )
        )
    overall_pass = all(case["passes_gate"] for case in cases)
    return {
        "kind": "worker_dispatch_benchmark",
        "generated_at": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "warmup": warmup,
        "repeats": repeats,
        "max_overhead_ratio": max_overhead_ratio,
        "measurement_strategy": "interleaved_pairwise",
        "overall_pass": overall_pass,
        "cases": cases,
    }


def _write_report(report: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output = RESULTS_DIR / f"{report['generated_at']}_worker_dispatch_benchmark.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.eval.worker_dispatch_benchmark",
        description="Measure deterministic WorkerExecutor dispatch overhead against legacy compute nodes.",
    )
    parser.add_argument("--warmup", type=int, default=20, help="Warmup runs per case before timing.")
    parser.add_argument("--repeats", type=int, default=200, help="Timed runs per case.")
    parser.add_argument(
        "--max-overhead-ratio",
        type=float,
        default=1.25,
        help="Maximum allowed Worker/legacy median runtime ratio for the gate.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any case exceeds --max-overhead-ratio.",
    )
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    report = await _run_benchmark(
        warmup=args.warmup,
        repeats=args.repeats,
        max_overhead_ratio=args.max_overhead_ratio,
    )
    output = _write_report(report)
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nSaved report to {output}")
    if args.strict and not report["overall_pass"]:
        return 1
    return 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
