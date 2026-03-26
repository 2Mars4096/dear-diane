"""workflow_runtime — surface-agnostic workflow execution facade.

This module provides the explicit runtime-facing workflow surface extracted by
the 41-* restructuring work. It intentionally re-exports the stable execution
contracts from ``dan.engine`` so surfaces can depend on one workflow runtime
entrypoint instead of reaching into ``engine/`` directly.

Import boundary: workflow_runtime may depend on ``llm_core``, ``models``,
``builder``, ``loader``, ``engine``, and ``executors``. It must NOT depend on
``server``, ``server.concierge``, or ``cli``.
"""

from dan.engine import (
    ArtifactStore,
    CheckpointStore,
    Engine,
    EngineConfig,
    EventCallback,
    ExecutionContext,
    ExecutionState,
    ExecutorRegistry,
    FileSystemCheckpointStore,
    LocalStateManager,
    NodeResult,
    NodeStatus,
    NullCheckpointStore,
    RunResult,
    SharedContextStore,
)
from dan.executor_defaults import register_default_executors

__all__ = [
    "ArtifactStore",
    "CheckpointStore",
    "Engine",
    "EngineConfig",
    "EventCallback",
    "ExecutionContext",
    "ExecutionState",
    "ExecutorRegistry",
    "FileSystemCheckpointStore",
    "LocalStateManager",
    "NodeResult",
    "NodeStatus",
    "NullCheckpointStore",
    "RunResult",
    "SharedContextStore",
    "register_default_executors",
]
