"""Public Worker API."""

from dan.worker.model import (
    AuthorityPolicy,
    ControlFlowConfig,
    ContextBindings,
    ExecutionSemantics,
    LLMHints,
    Worker,
    WorkerAuthority,
    WorkerConfig,
)
from dan.worker.presets import (
    BRIDGED_LEGACY_NODE_TYPES,
    EXPLICIT_NON_BRIDGED_LEGACY_NODE_TYPES,
    convert_graph,
    legacy_to_worker,
    supports_legacy_conversion,
    validate_conversion,
    worker_to_legacy,
)
from dan.worker.roles import gate, llm_agent, manager, observer, reviewer, role, router, script, tool_runner, validator

__all__ = [
    "AuthorityPolicy",
    "BRIDGED_LEGACY_NODE_TYPES",
    "ControlFlowConfig",
    "ContextBindings",
    "EXPLICIT_NON_BRIDGED_LEGACY_NODE_TYPES",
    "ExecutionSemantics",
    "LLMHints",
    "Worker",
    "WorkerAuthority",
    "WorkerConfig",
    "convert_graph",
    "gate",
    "legacy_to_worker",
    "llm_agent",
    "manager",
    "observer",
    "reviewer",
    "role",
    "router",
    "script",
    "supports_legacy_conversion",
    "tool_runner",
    "validator",
    "validate_conversion",
    "worker_to_legacy",
]


def __getattr__(name: str):
    if name == "WorkerExecutor":
        from dan.worker.executor import WorkerExecutor

        return WorkerExecutor
    raise AttributeError(name)
