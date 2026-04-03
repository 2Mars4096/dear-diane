"""Deterministic benchmark harness for the 46/47 Worker + linter rollout.

Usage:
    PYTHONPATH=src python -m tests.eval.worker_lint_benchmark --repeats 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dan.builder import workflow
from dan.engine import Engine, EngineConfig
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.events import EngineEvent, EventType
from dan.engine.executor import ExecutorRegistry, NodeResult
from dan.engine.state import NodeStatus
from dan.executors.code import CodeExecutor
from dan.executors import control_flow as control_flow_executors
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.executor_defaults import register_default_executors
from dan.models.context import MergeStrategy
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.ports import InputPort, OutputPort
from dan.providers import CompletionResult
from dan.server.runtime_config import build_engine_config_from_env
from dan.worker.executor import WorkerExecutor
from dan.worker.model import Worker
from dan.worker.presets import convert_graph, validate_conversion

RESULTS_DIR = Path(__file__).parent / "results"
_LINT_EVENT_TYPES = {
    EventType.LINT_FAILED,
    EventType.LINT_PASSED,
    EventType.LINT_AUTO_FIXED,
}
_INTERESTING_EVENTS = {
    EventType.NODE_STARTED,
    EventType.NODE_OUTPUT,
    EventType.NODE_COMPLETED,
    *_LINT_EVENT_TYPES,
}


@dataclass(frozen=True)
class LiveProviderCapabilities:
    env_file: str | None
    provider_names: list[str]
    embedding_provider_names: list[str]
    llm_model: str
    embedding_model: str
    intent_available: bool
    semantic_available: bool
    intent_reason: str = ""
    semantic_reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "env_file": self.env_file,
            "provider_names": list(self.provider_names),
            "embedding_provider_names": list(self.embedding_provider_names),
            "llm_model": self.llm_model,
            "embedding_model": self.embedding_model,
            "intent_available": self.intent_available,
            "semantic_available": self.semantic_available,
            "intent_reason": self.intent_reason,
            "semantic_reason": self.semantic_reason,
        }


class _SequenceProvider:
    def __init__(self, responses: list[CompletionResult]) -> None:
        self._responses = list(responses)

    async def complete(self, *args, **kwargs) -> CompletionResult:
        assert self._responses, "provider responses exhausted"
        return self._responses.pop(0)


def _default_engine(callback=None) -> Engine:
    return Engine(
        config=EngineConfig(
            llm_api_key="test",
            llm_base_url="http://localhost:1",
            llm_default_model="stub-model",
            checkpoint_enabled=False,
        ),
        checkpoint_store=NullCheckpointStore(),
        event_callback=callback,
    )


def _env_engine(callback=None) -> Engine:
    config = build_engine_config_from_env()
    config.checkpoint_enabled = False
    config.memory_enabled = False
    config.memory_pipeline_enabled = False
    config.cache_enabled = False
    config.state_store_enabled = False
    return Engine(
        config=config,
        checkpoint_store=NullCheckpointStore(),
        event_callback=callback,
    )


def _tool_engine(callback=None) -> Engine:
    tool_registry = ToolRegistry()

    async def prefix_tool(input: Any = "", prefix: str = "") -> dict[str, str]:
        return {"result": f"{prefix}{input}"}

    tool_registry.register("prefix_tool", prefix_tool)

    registry = ExecutorRegistry()
    register_default_executors(registry)
    custom_tool_executor = ToolExecutor(tool_registry)
    registry.register("tool_operator", custom_tool_executor)
    registry.register(
        "worker",
        WorkerExecutor(
            tool_executor=custom_tool_executor,
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
        event_callback=callback,
    )


def _provider_engine(provider: Any, callback=None, *, llm_executor: Any | None = None) -> Engine:
    registry = ExecutorRegistry()
    register_default_executors(registry)
    registry.register(
        "worker",
        WorkerExecutor(
            llm_executor=llm_executor,
            code_executor=CodeExecutor(),
        ),
    )
    engine = Engine(
        config=EngineConfig(
            llm_api_key="test",
            llm_base_url="http://localhost:1",
            llm_default_model="stub-model",
            checkpoint_enabled=False,
        ),
        checkpoint_store=NullCheckpointStore(),
        executor_registry=registry,
        event_callback=callback,
    )
    engine.model_gateway = provider
    return engine


def _normalize_events(events: list[EngineEvent]) -> list[tuple[str, str]]:
    return [
        (event.event_type.value, event.node_id or "")
        for event in events
        if event.event_type in _INTERESTING_EVENTS
    ]


def _normalize_lint_events(events: list[EngineEvent]) -> list[dict[str, Any]]:
    return [
        {
            "event_type": event.event_type.value,
            "node_id": event.node_id or "",
            "data": _jsonable(event.data or {}),
        }
        for event in events
        if event.event_type in _LINT_EVENT_TYPES
    ]


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    return value


def _parse_env_assignment(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export "):].lstrip()
    if "=" not in stripped:
        return None
    key, raw = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    lexer = shlex.shlex(raw, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    tokens = list(lexer)
    return key, " ".join(tokens)


def _load_env_file(path: Path, *, override: bool = False) -> list[str]:
    loaded: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_assignment(line)
        if parsed is None:
            continue
        key, value = parsed
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded


def _detect_live_provider_capabilities(env_file: Path | None = None) -> LiveProviderCapabilities:
    config = build_engine_config_from_env()
    provider_names = sorted(config.providers) or (["default"] if (config.llm_api_key or "").strip() else [])
    embedding_provider_names = sorted(config.embedding_providers)
    intent_available = bool((config.llm_api_key or "").strip() or provider_names)
    semantic_available = bool(embedding_provider_names)
    return LiveProviderCapabilities(
        env_file=str(env_file) if env_file is not None else None,
        provider_names=provider_names,
        embedding_provider_names=embedding_provider_names,
        llm_model=config.llm_default_model,
        embedding_model=config.default_embedding_model,
        intent_available=intent_available,
        semantic_available=semantic_available,
        intent_reason="" if intent_available else "No LLM provider configured in environment",
        semantic_reason="" if semantic_available else "No embedding provider configured in environment",
    )


def _live_case_skipped(case_id: str, reason: str) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "kind": "lint-live",
        "summary": {
            "all_passed": True,
            "skipped": True,
            "skip_reason": reason,
            "pass_rate": None,
            "avg_elapsed_ms": None,
        },
        "repeats": [],
    }


def _live_case_counts_as_pass(case: dict[str, Any], *, live_provider_mode: str) -> bool:
    if case["summary"].get("skipped"):
        return live_provider_mode != "required"
    return bool(case["summary"]["all_passed"])


def _lint_event_seen(
    run: dict[str, Any],
    *,
    event_type: EventType,
    min_tier: int,
) -> bool:
    for event in run.get("lint_events", []):
        if event["event_type"] != event_type.value:
            continue
        if int(event.get("data", {}).get("tier_reached", 0) or 0) >= min_tier:
            return True
    return False


def _lint_event_has_score(
    run: dict[str, Any],
    *,
    score_key: str,
    min_tier: int,
) -> bool:
    for event in run.get("lint_events", []):
        if int(event.get("data", {}).get("tier_reached", 0) or 0) < min_tier:
            continue
        if event.get("data", {}).get(score_key) is not None:
            return True
    return False


async def _run_graph(graph: Graph, *, inputs: dict[str, Any], engine_factory) -> dict[str, Any]:
    events: list[EngineEvent] = []

    async def capture(event: EngineEvent) -> None:
        events.append(event)

    engine = engine_factory(capture)
    started = time.perf_counter()
    result = await engine.run(graph, inputs=inputs)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return {
        "success": result.success,
        "outputs": _jsonable(result.outputs),
        "node_statuses": _jsonable(result.node_statuses),
        "events": _normalize_events(events),
        "lint_events": _normalize_lint_events(events),
        "elapsed_ms": elapsed_ms,
    }


def _single_edge_probe_graph(
    *,
    source_code: str,
    lint: dict[str, Any],
    target_input_required: bool = False,
) -> Graph:
    return Graph(
        nodes=[
            Worker(
                id="source",
                name="Source",
                code=source_code,
                input_ports=[InputPort(name="input", required=False)],
                output_ports=[OutputPort(name="result")],
            ),
            Worker(
                id="target",
                name="Target",
                input_ports=[InputPort(name="input", required=target_input_required)],
                output_ports=[OutputPort(name="result")],
            ),
        ],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="source",
                source_port="result",
                target_node_id="target",
                target_port="input",
                lint=lint,
            )
        ],
        entry_points=["source"],
        exit_points=["target"],
    )


async def _benchmark_linear_tool_chain(repeats: int) -> dict[str, Any]:
    wf = workflow("worker_benchmark_linear_tool_chain")
    entry = wf.input_node(
        "entry",
        variables=[
            {"name": "left", "type": "number"},
            {"name": "right", "type": "number"},
        ],
    )
    add = wf.code(
        "add",
        code="result = left + right",
        input_ports=[{"name": "left"}, {"name": "right"}],
        output_ports=[{"name": "result"}],
    )
    fmt = wf.tool(
        "fmt",
        tool_id="prefix_tool",
        tool_config={"prefix": "sum:"},
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "result"}],
    )
    wf.edge(entry["left"], add["left"])
    wf.edge(entry["right"], add["right"])
    wf.edge(add["result"], fmt["input"])

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)
    conversion_errors = validate_conversion(legacy_graph, worker_graph)

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        legacy = await _run_graph(legacy_graph, inputs={"left": 2, "right": 3}, engine_factory=_tool_engine)
        worker = await _run_graph(worker_graph, inputs={"left": 2, "right": 3}, engine_factory=_tool_engine)
        repeats_data.append({
            "legacy": legacy,
            "worker": worker,
            "output_match": worker["outputs"] == legacy["outputs"],
            "status_match": worker["node_statuses"] == legacy["node_statuses"],
            "event_match": worker["events"] == legacy["events"],
        })

    return _summarize_parity_case("linear_tool_chain", conversion_errors, repeats_data)


async def _benchmark_foreach_square(repeats: int) -> dict[str, Any]:
    wf = workflow("worker_benchmark_foreach_square")
    with wf.for_each(
        "fan",
        parallelism=1,
        merge_strategy=MergeStrategy.APPEND,
        input_ports=[{"name": "items"}],
        output_ports=[{"name": "results"}],
    ) as body:
        body.code(
            "square",
            code="result = {'value': item * item}",
            input_ports=[{"name": "item"}, {"name": "index"}],
            output_ports=[{"name": "value"}],
        )

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)
    conversion_errors = validate_conversion(legacy_graph, worker_graph)

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        legacy = await _run_graph(legacy_graph, inputs={"items": [1, 2, 3, 4]}, engine_factory=_default_engine)
        worker = await _run_graph(worker_graph, inputs={"items": [1, 2, 3, 4]}, engine_factory=_default_engine)
        repeats_data.append({
            "legacy": legacy,
            "worker": worker,
            "output_match": worker["outputs"] == legacy["outputs"],
            "status_match": worker["node_statuses"] == legacy["node_statuses"],
            "event_match": worker["events"] == legacy["events"],
        })

    return _summarize_parity_case("foreach_square", conversion_errors, repeats_data)


async def _benchmark_goal_loop(repeats: int) -> dict[str, Any]:
    wf = workflow("worker_benchmark_goal_loop")
    with wf.goal_loop(
        "improve",
        goal_text="Reach target score",
        metric_name="score",
        target_value=0.85,
        comparison=">=",
        max_iterations=5,
        input_ports=[{"name": "draft"}, {"name": "score"}],
        output_ports=[
            {"name": "draft"},
            {"name": "score"},
            {"name": "goal_met"},
            {"name": "iterations"},
            {"name": "best_score"},
        ],
    ) as body:
        body.code(
            "advance",
            code=(
                "next_score = round(min(score + 0.3, 1.0), 2)\n"
                "result = {'draft': f'{draft}|{next_score}', 'score': next_score}"
            ),
            input_ports=[{"name": "draft"}, {"name": "score"}],
            output_ports=[{"name": "draft"}, {"name": "score"}],
        )

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)
    conversion_errors = validate_conversion(legacy_graph, worker_graph)

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        legacy = await _run_graph(legacy_graph, inputs={"draft": "seed", "score": 0.2}, engine_factory=_default_engine)
        worker = await _run_graph(worker_graph, inputs={"draft": "seed", "score": 0.2}, engine_factory=_default_engine)
        repeats_data.append({
            "legacy": legacy,
            "worker": worker,
            "output_match": worker["outputs"] == legacy["outputs"],
            "status_match": worker["node_statuses"] == legacy["node_statuses"],
            "event_match": worker["events"] == legacy["events"],
        })

    return _summarize_parity_case("goal_loop", conversion_errors, repeats_data)


async def _benchmark_parallel_subagents(repeats: int) -> dict[str, Any]:
    wf = workflow("worker_benchmark_parallel_subagents")
    with wf.parallel_subagents(
        "teams",
        parallelism=2,
        merge_strategy=MergeStrategy.APPEND,
        input_mappings={"input": "input"},
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "results"}],
    ) as parallel:
        with parallel.branch("alpha") as sub:
            alpha = sub.code(
                "alpha_task",
                code="result = {'branch': 'alpha', 'value': f'{input}-alpha'}",
                input_ports=[{"name": "input", "required": False}],
                output_ports=[{"name": "branch"}, {"name": "value"}],
            )
            sub.edge(sub.entry_input, alpha["input"])
        with parallel.branch("beta") as sub:
            beta = sub.code(
                "beta_task",
                code="result = {'branch': 'beta', 'value': f'{input}-beta'}",
                input_ports=[{"name": "input", "required": False}],
                output_ports=[{"name": "branch"}, {"name": "value"}],
            )
            sub.edge(sub.entry_input, beta["input"])

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)
    conversion_errors = validate_conversion(legacy_graph, worker_graph)

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        legacy = await _run_graph(legacy_graph, inputs={"input": "seed"}, engine_factory=_default_engine)
        worker = await _run_graph(worker_graph, inputs={"input": "seed"}, engine_factory=_default_engine)
        repeats_data.append({
            "legacy": legacy,
            "worker": worker,
            "output_match": worker["outputs"] == legacy["outputs"],
            "status_match": worker["node_statuses"] == legacy["node_statuses"],
            "event_match": worker["events"] == legacy["events"],
        })

    return _summarize_parity_case("parallel_subagents", conversion_errors, repeats_data)


async def _benchmark_agent_team(repeats: int) -> dict[str, Any]:
    wf = workflow("worker_benchmark_agent_team")
    with wf.team(
        "review_team",
        turn_strategy="sequential",
        completion_condition="all_responded",
        input_mappings={"input": "input"},
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "result"}],
    ) as team:
        with team.agent("researcher") as sub:
            research = sub.code(
                "research",
                code="result = f'research:{input}'",
                input_ports=[{"name": "input", "required": False}],
                output_ports=[{"name": "result"}],
            )
            sub.edge(sub.entry_input, research["input"])
        with team.agent("writer") as sub:
            write = sub.code(
                "write",
                code="result = f'write:{input}'",
                input_ports=[{"name": "input", "required": False}],
                output_ports=[{"name": "result"}],
            )
            sub.edge(sub.entry_input, write["input"])

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)
    conversion_errors = validate_conversion(legacy_graph, worker_graph)

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        legacy = await _run_graph(legacy_graph, inputs={"input": "topic"}, engine_factory=_default_engine)
        worker = await _run_graph(worker_graph, inputs={"input": "topic"}, engine_factory=_default_engine)
        repeats_data.append({
            "legacy": legacy,
            "worker": worker,
            "output_match": worker["outputs"] == legacy["outputs"],
            "status_match": worker["node_statuses"] == legacy["node_statuses"],
            "event_match": worker["events"] == legacy["events"],
        })

    return _summarize_parity_case("agent_team", conversion_errors, repeats_data)


async def _benchmark_static_orchestrator(repeats: int) -> dict[str, Any]:
    wf = workflow("worker_benchmark_static_orchestrator")
    with wf.orchestrator(
        "coord",
        completion_condition="all_done",
        input_mappings={"input": "input"},
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "results"}],
    ) as orch:
        with orch.team("research") as sub:
            research = sub.code(
                "research_task",
                code="result = {'team': 'research', 'value': f'{input}-research'}",
                input_ports=[{"name": "input", "required": False}],
                output_ports=[{"name": "team"}, {"name": "value"}],
            )
            sub.edge(sub.entry_input, research["input"])
        with orch.team("draft") as sub:
            draft = sub.code(
                "draft_task",
                code="result = {'team': 'draft', 'value': f'{input}-draft'}",
                input_ports=[{"name": "input", "required": False}],
                output_ports=[{"name": "team"}, {"name": "value"}],
            )
            sub.edge(sub.entry_input, draft["input"])

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)
    conversion_errors = validate_conversion(legacy_graph, worker_graph)

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        legacy = await _run_graph(legacy_graph, inputs={"input": "topic"}, engine_factory=_default_engine)
        worker = await _run_graph(worker_graph, inputs={"input": "topic"}, engine_factory=_default_engine)
        repeats_data.append({
            "legacy": legacy,
            "worker": worker,
            "output_match": worker["outputs"] == legacy["outputs"],
            "status_match": worker["node_statuses"] == legacy["node_statuses"],
            "event_match": worker["events"] == legacy["events"],
        })

    return _summarize_parity_case("static_orchestrator", conversion_errors, repeats_data)


async def _benchmark_llm_orchestrator(repeats: int) -> dict[str, Any]:
    wf = workflow("worker_benchmark_llm_orchestrator")
    with wf.orchestrator(
        "coord",
        orchestrator_prompt="Dispatch the right teams and then halt.",
        orchestrator_model="stub-orchestrator",
        completion_condition="orchestrator_halt",
        input_mappings={"input": "input"},
        input_ports=[{"name": "input"}],
        output_ports=[{"name": "results"}],
    ) as orch:
        with orch.team("research") as sub:
            research = sub.code(
                "research_task",
                code="result = {'team': 'research', 'value': f'{input}-research'}",
                input_ports=[{"name": "input", "required": False}],
                output_ports=[{"name": "team"}, {"name": "value"}],
            )
            sub.edge(sub.entry_input, research["input"])

    legacy_graph = wf.build()
    worker_graph = convert_graph(legacy_graph)
    conversion_errors = validate_conversion(legacy_graph, worker_graph)

    repeats_data: list[dict[str, Any]] = []
    previous_resolver = control_flow_executors.resolve_completion_provider
    try:
        for _ in range(repeats):
            provider = _SequenceProvider(
                [
                    CompletionResult(
                        text="Dispatch research",
                        tool_calls=[
                            {
                                "function": {
                                    "name": "dispatch_to_team",
                                    "arguments": '{"team_name": "research", "inputs": {}}',
                                }
                            }
                        ],
                    ),
                    CompletionResult(
                        text="Stop orchestration",
                        tool_calls=[
                            {
                                "function": {
                                    "name": "halt_orchestrator",
                                    "arguments": (
                                        '{"reason": "research complete", '
                                        '"final_result": {"winner": "research"}}'
                                    ),
                                }
                            }
                        ],
                    ),
                    CompletionResult(
                        text="Dispatch research",
                        tool_calls=[
                            {
                                "function": {
                                    "name": "dispatch_to_team",
                                    "arguments": '{"team_name": "research", "inputs": {}}',
                                }
                            }
                        ],
                    ),
                    CompletionResult(
                        text="Stop orchestration",
                        tool_calls=[
                            {
                                "function": {
                                    "name": "halt_orchestrator",
                                    "arguments": (
                                        '{"reason": "research complete", '
                                        '"final_result": {"winner": "research"}}'
                                    ),
                                }
                            }
                        ],
                    ),
                ]
            )
            control_flow_executors.resolve_completion_provider = lambda *args, **kwargs: provider
            legacy = await _run_graph(legacy_graph, inputs={"input": "topic"}, engine_factory=_default_engine)
            worker = await _run_graph(worker_graph, inputs={"input": "topic"}, engine_factory=_default_engine)
            repeats_data.append({
                "legacy": legacy,
                "worker": worker,
                "output_match": worker["outputs"] == legacy["outputs"],
                "status_match": worker["node_statuses"] == legacy["node_statuses"],
                "event_match": worker["events"] == legacy["events"],
            })
    finally:
        control_flow_executors.resolve_completion_provider = previous_resolver

    return _summarize_parity_case("llm_orchestrator", conversion_errors, repeats_data)


async def _benchmark_lint_block(repeats: int) -> dict[str, Any]:
    graph = _single_edge_probe_graph(
        source_code="result = {'summary': 'hello'}",
        lint={
            "structural": {"required_keys": ["missing_key"]},
            "severity": "error",
        },
        target_input_required=True,
    )

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        run = await _run_graph(graph, inputs={}, engine_factory=_default_engine)
        repeats_data.append({
            "run": run,
            "expected_failure": (run["success"] is False and run["node_statuses"].get("target") == "skipped"),
            "lint_failed_seen": any(event[0] == EventType.LINT_FAILED.value for event in run["events"]),
        })

    return _summarize_lint_case("lint_block", repeats_data)


async def _benchmark_lint_autofix(repeats: int) -> dict[str, Any]:
    graph = _single_edge_probe_graph(
        source_code="result = {'draft': 'abcdef'}",
        lint={
            "structural": {"string_max_lengths": {"draft": 3}},
            "autofix": ["truncate"],
            "severity": "error",
        },
    )

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        run = await _run_graph(graph, inputs={}, engine_factory=_default_engine)
        repeats_data.append({
            "run": run,
            "autofix_applied": run["outputs"].get("result", {}).get("draft") == "abc",
            "lint_fixed_seen": any(event[0] == EventType.LINT_AUTO_FIXED.value for event in run["events"]),
        })

    return _summarize_lint_case("lint_autofix", repeats_data)


async def _benchmark_semantic_backendless(repeats: int) -> dict[str, Any]:
    graph = _single_edge_probe_graph(
        source_code="result = 'finance summary'",
        lint={
            "semantic": {
                "reference_text": "finance summary for leadership",
                "min_similarity": 0.8,
            },
            "severity": "error",
        },
    )

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        run = await _run_graph(graph, inputs={}, engine_factory=_default_engine)
        repeats_data.append({
            "run": run,
            "graceful_skip": run["success"] is True and not any(
                event[0] == EventType.LINT_FAILED.value for event in run["events"]
            ),
        })

    return _summarize_lint_case("semantic_backendless", repeats_data)


async def _benchmark_semantic_contradiction(repeats: int) -> dict[str, Any]:
    graph = _single_edge_probe_graph(
        source_code="result = 'Acme revenue increased to 8% this quarter.'",
        lint={
            "semantic": {
                "contradiction_reference_text": "Acme revenue decreased to 8% this quarter.",
            },
            "severity": "error",
        },
        target_input_required=True,
    )

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        run = await _run_graph(graph, inputs={}, engine_factory=_default_engine)
        repeats_data.append({
            "run": run,
            "contradiction_detected": (
                run["success"] is False
                and run["node_statuses"].get("target") == "skipped"
                and _lint_event_seen(run, event_type=EventType.LINT_FAILED, min_tier=2)
                and _lint_event_has_score(run, score_key="semantic_score", min_tier=2)
            ),
        })

    return _summarize_boolean_lint_case("semantic_contradiction", repeats_data, "contradiction_detected")


async def _benchmark_intent_partial_warning(repeats: int) -> dict[str, Any]:
    graph = _single_edge_probe_graph(
        source_code="result = 'Executive finance briefing: revenue rose 12 percent.'",
        lint={
            "intent": {
                "intent": "Executive finance briefing with recommendation",
            },
            "severity": "error",
        },
    )

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        provider = _SequenceProvider(
            [
                CompletionResult(
                    text='{"verdict":"partial","score":0.55,"reason":"Missing recommendation","missing":["recommendation"]}',
                ),
                CompletionResult(
                    text='{"verdict":"partial","score":0.6,"reason":"Covers revenue but not the recommendation","missing":["recommendation"],"covered":["revenue"]}',
                ),
            ]
        )
        run = await _run_graph(
            graph,
            inputs={},
            engine_factory=lambda callback, provider=provider: _provider_engine(provider, callback),
        )
        repeats_data.append(
            {
                "run": run,
                "partial_warning_observed": (
                    run["success"] is True
                    and _lint_event_seen(run, event_type=EventType.LINT_PASSED, min_tier=3)
                    and any(
                        (event.get("data", {}).get("diagnostic_count", 0) or 0) >= 1
                        for event in run.get("lint_events", [])
                        if event["event_type"] == EventType.LINT_PASSED.value
                    )
                ),
            }
        )

    return _summarize_boolean_lint_case("intent_partial_warning", repeats_data, "partial_warning_observed")


async def _benchmark_intent_retry_feedback(repeats: int) -> dict[str, Any]:
    class _RetryAwareLLMExecutor:
        def __init__(self) -> None:
            self.feedbacks: list[str | None] = []

        async def execute(self, node, inputs, context) -> NodeResult:
            feedback = inputs.get("__lint_feedback__")
            self.feedbacks.append(feedback if isinstance(feedback, str) else None)
            if feedback:
                return NodeResult(
                    outputs={"result": "Executive finance briefing with repaired recommendation"},
                    status=NodeStatus.COMPLETED,
                )
            return NodeResult(
                outputs={"result": "Executive finance briefing without recommendation"},
                status=NodeStatus.COMPLETED,
            )

    graph = Graph(
        nodes=[
            Worker(
                id="source",
                name="Source",
                model="stub-model",
                output_ports=[OutputPort(name="result")],
            ),
            Worker(
                id="target",
                name="Target",
                input_ports=[InputPort(name="input", required=True)],
                output_ports=[OutputPort(name="result")],
            ),
        ],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="source",
                source_port="result",
                target_node_id="target",
                target_port="input",
                lint={
                    "intent": {
                        "intent": "Executive finance briefing with recommendation",
                    },
                    "severity": "error",
                    "autofix": ["retry_with_feedback"],
                    "max_retries": 1,
                },
            )
        ],
        entry_points=["source"],
        exit_points=["target"],
    )

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        llm_executor = _RetryAwareLLMExecutor()
        provider = _SequenceProvider(
            [
                CompletionResult(
                    text='{"verdict":"fail","score":0.1,"reason":"Missing recommendation","missing":["recommendation"]}',
                ),
                CompletionResult(
                    text='{"verdict":"pass","score":1.0,"message":"ok"}',
                ),
            ]
        )
        run = await _run_graph(
            graph,
            inputs={},
            engine_factory=lambda callback, provider=provider, llm_executor=llm_executor: _provider_engine(
                provider,
                callback,
                llm_executor=llm_executor,
            ),
        )
        retry_feedback = llm_executor.feedbacks[1] if len(llm_executor.feedbacks) > 1 else None
        repeats_data.append(
            {
                "run": run,
                "retry_feedback_observed": (
                    run["success"] is True
                    and _lint_event_seen(run, event_type=EventType.LINT_FAILED, min_tier=3)
                    and _lint_event_seen(run, event_type=EventType.LINT_PASSED, min_tier=3)
                    and isinstance(retry_feedback, str)
                    and "Still missing: recommendation" in retry_feedback
                    and "Previous output:" in retry_feedback
                ),
            }
        )

    return _summarize_boolean_lint_case("intent_retry_feedback", repeats_data, "retry_feedback_observed")


async def _benchmark_semantic_live_pass(
    repeats: int,
    capabilities: LiveProviderCapabilities,
) -> dict[str, Any]:
    if not capabilities.semantic_available:
        return _live_case_skipped("semantic_live_pass", capabilities.semantic_reason)

    graph = _single_edge_probe_graph(
        source_code=(
            "result = "
            "'Executive finance summary: quarterly revenue grew 12 percent and the main risk is supply-chain delay.'"
        ),
        lint={
            "semantic": {
                "reference_text": "Executive finance summary for quarterly revenue and supply-chain risk.",
                "min_similarity": 0.65,
            },
            "severity": "error",
        },
    )

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        run = await _run_graph(graph, inputs={}, engine_factory=_env_engine)
        repeats_data.append({
            "run": run,
            "provider_verified": (
                run["success"] is True
                and _lint_event_seen(run, event_type=EventType.LINT_PASSED, min_tier=2)
                and _lint_event_has_score(run, score_key="semantic_score", min_tier=2)
            ),
        })

    return _summarize_live_lint_case("semantic_live_pass", repeats_data)


async def _benchmark_semantic_live_fail(
    repeats: int,
    capabilities: LiveProviderCapabilities,
) -> dict[str, Any]:
    if not capabilities.semantic_available:
        return _live_case_skipped("semantic_live_fail", capabilities.semantic_reason)

    graph = _single_edge_probe_graph(
        source_code="result = 'Weekend hiking checklist: boots, snacks, sunscreen, and a mountain route.'",
        lint={
            "semantic": {
                "reference_text": "Executive finance summary for quarterly revenue and supply-chain risk.",
                "min_similarity": 0.7,
            },
            "severity": "error",
        },
    )

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        run = await _run_graph(graph, inputs={}, engine_factory=_env_engine)
        repeats_data.append({
            "run": run,
            "provider_verified": (
                run["success"] is False
                and _lint_event_seen(run, event_type=EventType.LINT_FAILED, min_tier=2)
                and _lint_event_has_score(run, score_key="semantic_score", min_tier=2)
            ),
        })

    return _summarize_live_lint_case("semantic_live_fail", repeats_data)


async def _benchmark_intent_live_pass(
    repeats: int,
    capabilities: LiveProviderCapabilities,
) -> dict[str, Any]:
    if not capabilities.intent_available:
        return _live_case_skipped("intent_live_pass", capabilities.intent_reason)

    graph = _single_edge_probe_graph(
        source_code=(
            "result = "
            "'Executive briefing: Q2 revenue rose 12 percent, margin held steady, and the main risk is vendor concentration.'"
        ),
        lint={
            "intent": {
                "intent": "A concise executive finance briefing covering quarterly revenue, margin, and major risk.",
                "required_keywords": ["executive", "revenue", "risk"],
            },
            "severity": "error",
        },
    )

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        run = await _run_graph(graph, inputs={}, engine_factory=_env_engine)
        repeats_data.append({
            "run": run,
            "provider_verified": (
                run["success"] is True
                and _lint_event_seen(run, event_type=EventType.LINT_PASSED, min_tier=3)
                and _lint_event_has_score(run, score_key="intent_score", min_tier=3)
            ),
        })

    return _summarize_live_lint_case("intent_live_pass", repeats_data)


async def _benchmark_intent_live_fail(
    repeats: int,
    capabilities: LiveProviderCapabilities,
) -> dict[str, Any]:
    if not capabilities.intent_available:
        return _live_case_skipped("intent_live_fail", capabilities.intent_reason)

    graph = _single_edge_probe_graph(
        source_code="result = 'My weekend plan is hiking, coffee, and reading a novel by the beach.'",
        lint={
            "intent": {
                "intent": "A concise executive finance briefing covering quarterly revenue, margin, and major risk.",
                "required_keywords": ["executive", "revenue", "risk"],
            },
            "severity": "error",
        },
    )

    repeats_data: list[dict[str, Any]] = []
    for _ in range(repeats):
        run = await _run_graph(graph, inputs={}, engine_factory=_env_engine)
        repeats_data.append({
            "run": run,
            "provider_verified": (
                run["success"] is False
                and _lint_event_seen(run, event_type=EventType.LINT_FAILED, min_tier=3)
                and _lint_event_has_score(run, score_key="intent_score", min_tier=3)
            ),
        })

    return _summarize_live_lint_case("intent_live_fail", repeats_data)


def _summarize_parity_case(case_id: str, conversion_errors: list[str], repeats_data: list[dict[str, Any]]) -> dict[str, Any]:
    legacy_times = [repeat["legacy"]["elapsed_ms"] for repeat in repeats_data]
    worker_times = [repeat["worker"]["elapsed_ms"] for repeat in repeats_data]
    return {
        "case_id": case_id,
        "kind": "parity",
        "conversion_errors": conversion_errors,
        "summary": {
            "all_passed": all(
                repeat["output_match"] and repeat["status_match"] and repeat["event_match"]
                for repeat in repeats_data
            ),
            "output_match_rate": sum(repeat["output_match"] for repeat in repeats_data) / len(repeats_data),
            "status_match_rate": sum(repeat["status_match"] for repeat in repeats_data) / len(repeats_data),
            "event_match_rate": sum(repeat["event_match"] for repeat in repeats_data) / len(repeats_data),
            "legacy_avg_ms": round(statistics.mean(legacy_times), 3),
            "worker_avg_ms": round(statistics.mean(worker_times), 3),
        },
        "repeats": repeats_data,
    }


def _summarize_lint_case(case_id: str, repeats_data: list[dict[str, Any]]) -> dict[str, Any]:
    run_times = [repeat["run"]["elapsed_ms"] for repeat in repeats_data]
    pass_flags: list[bool] = []
    if case_id == "lint_block":
        pass_flags = [repeat["expected_failure"] and repeat["lint_failed_seen"] for repeat in repeats_data]
    elif case_id == "lint_autofix":
        pass_flags = [repeat["autofix_applied"] and repeat["lint_fixed_seen"] for repeat in repeats_data]
    else:
        pass_flags = [repeat["graceful_skip"] for repeat in repeats_data]

    return {
        "case_id": case_id,
        "kind": "lint",
        "summary": {
            "all_passed": all(pass_flags),
            "skipped": False,
            "pass_rate": sum(pass_flags) / len(pass_flags),
            "avg_elapsed_ms": round(statistics.mean(run_times), 3),
        },
        "repeats": repeats_data,
    }


def _summarize_boolean_lint_case(case_id: str, repeats_data: list[dict[str, Any]], key: str) -> dict[str, Any]:
    run_times = [repeat["run"]["elapsed_ms"] for repeat in repeats_data]
    pass_flags = [bool(repeat[key]) for repeat in repeats_data]
    return {
        "case_id": case_id,
        "kind": "lint",
        "summary": {
            "all_passed": all(pass_flags),
            "skipped": False,
            "pass_rate": sum(pass_flags) / len(pass_flags),
            "avg_elapsed_ms": round(statistics.mean(run_times), 3),
        },
        "repeats": repeats_data,
    }


def _summarize_live_lint_case(case_id: str, repeats_data: list[dict[str, Any]]) -> dict[str, Any]:
    run_times = [repeat["run"]["elapsed_ms"] for repeat in repeats_data]
    pass_flags = [bool(repeat["provider_verified"]) for repeat in repeats_data]
    return {
        "case_id": case_id,
        "kind": "lint-live",
        "summary": {
            "all_passed": all(pass_flags),
            "skipped": False,
            "pass_rate": sum(pass_flags) / len(pass_flags),
            "avg_elapsed_ms": round(statistics.mean(run_times), 3),
        },
        "repeats": repeats_data,
    }


async def _run_benchmark(
    repeats: int,
    *,
    live_provider_mode: str = "off",
    env_file: Path | None = None,
) -> dict[str, Any]:
    capabilities = _detect_live_provider_capabilities(env_file) if live_provider_mode != "off" else None
    cases = [
        await _benchmark_linear_tool_chain(repeats),
        await _benchmark_foreach_square(repeats),
        await _benchmark_goal_loop(repeats),
        await _benchmark_parallel_subagents(repeats),
        await _benchmark_agent_team(repeats),
        await _benchmark_static_orchestrator(repeats),
        await _benchmark_llm_orchestrator(repeats),
        await _benchmark_lint_block(repeats),
        await _benchmark_lint_autofix(repeats),
        await _benchmark_semantic_backendless(repeats),
        await _benchmark_semantic_contradiction(repeats),
        await _benchmark_intent_partial_warning(repeats),
        await _benchmark_intent_retry_feedback(repeats),
    ]
    if capabilities is not None:
        cases.extend(
            [
                await _benchmark_semantic_live_pass(repeats, capabilities),
                await _benchmark_semantic_live_fail(repeats, capabilities),
                await _benchmark_intent_live_pass(repeats, capabilities),
                await _benchmark_intent_live_fail(repeats, capabilities),
            ]
        )
    skipped_case_count = sum(1 for case in cases if case["summary"].get("skipped"))
    return {
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repeats": repeats,
        "live_provider_mode": live_provider_mode,
        "provider_context": capabilities.to_json() if capabilities is not None else None,
        "executed_case_count": len(cases) - skipped_case_count,
        "skipped_case_count": skipped_case_count,
        "all_passed": all(_live_case_counts_as_pass(case, live_provider_mode=live_provider_mode) for case in cases),
        "cases": cases,
    }


def _output_path(results_dir: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return results_dir / f"{stamp}_worker_lint_benchmark.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic Worker/lint benchmark harness")
    parser.add_argument("--repeats", type=int, default=5, help="Number of repeats per case (default: 5)")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional .env file to load before provider-backed cases. Existing environment wins.",
    )
    parser.add_argument(
        "--live-provider",
        choices=["off", "auto", "required"],
        default="off",
        help=(
            "Provider-backed semantic/intent benchmark mode: "
            "off (default), auto (include live cases when configured, otherwise record skips), "
            "required (fail if live backends are unavailable or live cases do not pass)."
        ),
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=RESULTS_DIR,
        help=f"Results directory (default: {RESULTS_DIR})",
    )
    args = parser.parse_args()

    if args.env_file is not None:
        _load_env_file(args.env_file)

    report = asyncio.run(
        _run_benchmark(
            args.repeats,
            live_provider_mode=args.live_provider,
            env_file=args.env_file,
        )
    )
    args.results_dir.mkdir(parents=True, exist_ok=True)
    output_path = _output_path(args.results_dir)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(f"saved benchmark report: {output_path}")
    if report["provider_context"] is not None:
        print(
            "provider context: "
            f"intent_available={report['provider_context']['intent_available']} "
            f"semantic_available={report['provider_context']['semantic_available']}"
        )
    for case in report["cases"]:
        summary = case["summary"]
        if summary.get("skipped"):
            print(f"{case['case_id']}: skipped ({summary['skip_reason']})")
        else:
            print(f"{case['case_id']}: passed={summary['all_passed']}")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
