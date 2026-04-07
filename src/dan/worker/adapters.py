"""DAN-specific worker adapter wiring.

The reusable worker core stays dependency-light; this module owns the concrete
executor/tool bindings needed by the DAN runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dan.executors.code import CodeExecutor
from dan.executors.control_flow import GateExecutor, HumanNodeExecutor, ReduceExecutor, RouterExecutor, VoteExecutor
from dan.executors.rag import RAGExecutor
from dan.executors.reflection import ReflectionExecutor
from dan.executors.tool import ToolExecutor
from dan.executors.validator import ValidatorExecutor
from dan.worker.executor import LegacyWorkerAdapterExecutor, WorkerExecutor

if TYPE_CHECKING:
    from dan.executors.llm import LLMExecutor
else:
    LLMExecutor = Any


@dataclass(frozen=True)
class DANWorkerExecutorBundle:
    """Shared executor instances used by DAN's default worker registration."""

    worker_executor: WorkerExecutor
    legacy_worker_adapter: LegacyWorkerAdapterExecutor
    llm_executor: LLMExecutor | None
    tool_executor: ToolExecutor
    code_executor: CodeExecutor
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

    code_executor = CodeExecutor()
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
        llm_executor=llm_executor,
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
    return DANWorkerExecutorBundle(
        worker_executor=worker_executor,
        legacy_worker_adapter=legacy_worker_adapter,
        llm_executor=llm_executor,
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
