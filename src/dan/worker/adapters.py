"""DAN-specific worker adapter wiring.

The reusable worker core stays dependency-light; this module owns the concrete
executor/tool bindings needed by the DAN runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.executors.code import CodeExecutor
from dan.executors.control_flow import GateExecutor, HumanNodeExecutor, ReduceExecutor, RouterExecutor, VoteExecutor
from dan.executors.input import InputExecutor
from dan.executors.rag import RAGExecutor
from dan.executors.reflection import ReflectionExecutor
from dan.executors.tool import ToolExecutor
from dan.executors.validator import ValidatorExecutor
from dan.models.nodes import NodeBase
from dan.worker.executor import WorkerExecutor
from dan.worker.model import Worker

if TYPE_CHECKING:
    from dan.executors.llm import LLMExecutor
else:
    LLMExecutor = Any


class LazyLLMExecutor:
    """Instantiate ``LLMExecutor`` only when an LLM node actually runs."""

    def __init__(self, executor: Any | None = None) -> None:
        self._executor = executor

    def _ensure(self) -> Any:
        if self._executor is None:
            from dan.executors.llm import LLMExecutor

            self._executor = LLMExecutor()
        return self._executor

    async def execute(self, node: Any, inputs: dict[str, Any], context: Any):
        return await self._ensure().execute(node, inputs, context)


class WorkerBackedLegacyComputeExecutor:
    """Execute selected legacy compute nodes through ``WorkerExecutor``."""

    def __init__(
        self,
        worker_executor: WorkerExecutor,
        *,
        supported_node_types: frozenset[str] | None = None,
        registry: Any | None = None,
    ) -> None:
        self.worker_executor = worker_executor
        self.supported_node_types = supported_node_types
        self.registry = registry

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        if isinstance(node, Worker):
            return await self.worker_executor.execute(node, inputs, context)

        node_type = getattr(node, "node_type", type(node).__name__)
        if self.supported_node_types is not None and node_type not in self.supported_node_types:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=(
                    f"Legacy node type {node_type!r} is outside the supported "
                    "Worker-backed runtime families"
                ),
            )

        from dan.worker.presets import legacy_to_worker

        worker = legacy_to_worker(node)
        if worker is None:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=(
                    f"Legacy node type {node_type!r} "
                    "does not have a Worker bridge"
                ),
            )
        return await self.worker_executor.execute(worker, inputs, context)


class LegacyWorkerAdapterExecutor:
    """Execute bridged legacy compute nodes by routing through WorkerExecutor."""

    def __init__(self, worker_executor: WorkerExecutor) -> None:
        self._delegate = WorkerBackedLegacyComputeExecutor(worker_executor)
        self.worker_executor = worker_executor

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        return await self._delegate.execute(node, inputs, context)


@dataclass(frozen=True)
class DANWorkerExecutorBundle:
    """Shared executor instances used by DAN's default worker registration."""

    worker_executor: WorkerExecutor
    legacy_worker_adapter: LegacyWorkerAdapterExecutor
    llm_worker_runtime: WorkerBackedLegacyComputeExecutor
    tool_worker_runtime: WorkerBackedLegacyComputeExecutor
    code_worker_runtime: WorkerBackedLegacyComputeExecutor
    input_worker_runtime: WorkerBackedLegacyComputeExecutor
    llm_executor: Any
    tool_executor: ToolExecutor
    code_executor: CodeExecutor
    input_executor: InputExecutor
    gate_executor: GateExecutor
    router_executor: RouterExecutor
    validator_executor: ValidatorExecutor
    reflection_executor: ReflectionExecutor
    rag_executor: RAGExecutor
    human_executor: HumanNodeExecutor
    vote_executor: VoteExecutor
    reduce_executor: ReduceExecutor


def build_default_worker_executors(
    *,
    tool_registry: Any | None = None,
    llm_executor: LLMExecutor | None = None,
) -> DANWorkerExecutorBundle:
    """Create the shared DAN worker executor bundle used by default registration."""

    llm_runtime = llm_executor or LazyLLMExecutor()
    code_executor = CodeExecutor()
    input_executor = InputExecutor()
    gate_executor = GateExecutor()
    router_executor = RouterExecutor()
    validator_executor = ValidatorExecutor()
    reflection_executor = ReflectionExecutor()
    rag_executor = RAGExecutor()
    human_executor = HumanNodeExecutor()
    vote_executor = VoteExecutor()
    reduce_executor = ReduceExecutor()
    tool_executor = ToolExecutor(tool_registry)

    worker_executor = WorkerExecutor(
        llm_executor=llm_runtime,
        tool_executor=tool_executor,
        code_executor=code_executor,
        gate_executor=gate_executor,
        router_executor=router_executor,
        validator_executor=validator_executor,
        reflection_executor=reflection_executor,
        rag_executor=rag_executor,
        human_executor=human_executor,
        vote_executor=vote_executor,
        reduce_executor=reduce_executor,
    )
    legacy_worker_adapter = LegacyWorkerAdapterExecutor(worker_executor)
    llm_worker_runtime = WorkerBackedLegacyComputeExecutor(
        worker_executor,
        supported_node_types=frozenset({"llm_operator"}),
    )
    tool_worker_runtime = WorkerBackedLegacyComputeExecutor(
        worker_executor,
        supported_node_types=frozenset({"tool_operator"}),
        registry=tool_executor.registry,
    )
    code_worker_runtime = WorkerBackedLegacyComputeExecutor(
        worker_executor,
        supported_node_types=frozenset({"code_operator"}),
    )
    input_worker_runtime = WorkerBackedLegacyComputeExecutor(
        worker_executor,
        supported_node_types=frozenset({"input"}),
    )
    return DANWorkerExecutorBundle(
        worker_executor=worker_executor,
        legacy_worker_adapter=legacy_worker_adapter,
        llm_worker_runtime=llm_worker_runtime,
        tool_worker_runtime=tool_worker_runtime,
        code_worker_runtime=code_worker_runtime,
        input_worker_runtime=input_worker_runtime,
        llm_executor=llm_runtime,
        tool_executor=tool_executor,
        code_executor=code_executor,
        input_executor=input_executor,
        gate_executor=gate_executor,
        router_executor=router_executor,
        validator_executor=validator_executor,
        reflection_executor=reflection_executor,
        rag_executor=rag_executor,
        human_executor=human_executor,
        vote_executor=vote_executor,
        reduce_executor=reduce_executor,
    )
