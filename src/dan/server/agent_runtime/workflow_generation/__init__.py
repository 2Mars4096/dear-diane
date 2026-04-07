"""Workflow generation package."""

from dan.server.agent_runtime.workflow_generation import runtime as _runtime
from dan.server.agent_runtime.workflow_generation_acceptance import (
    WorkflowGenerationAcceptanceResult,
    accept_candidate_graph,
)
from dan.server.agent_runtime.workflow_generation_codegen import (
    WorkflowCodegenRequestResult,
    request_builder_code,
)
from dan.server.agent_runtime.workflow_generation_helpers import (
    exec_deterministic_builder_code,
    extract_code_from_response,
    parse_intent_from_result,
    sandbox_exec_builder_code,
)
from dan.server.agent_runtime.workflow_generation_stats import (
    get_generation_stats_hint,
    record_generation_outcome,
)
from dan.server.agent_runtime.workflow_handoff import (
    WorkflowGenerationAttemptOutcome,
    iter_workflow_generation_attempt,
    should_use_workflow_generation_fast_path,
)
from dan.server.agent_runtime.workflow_outcomes import (
    PreparedWorkflowTerminalReply,
    PreparedWorkflowSave,
    build_codegen_saved_message,
    build_persisted_workflow_reply,
    build_structural_macro_message,
    prepare_validated_workflow_save,
)

__all__ = [
    "PreparedWorkflowTerminalReply",
    "PreparedWorkflowSave",
    "WorkflowCodegenRequestResult",
    "WorkflowGenerationAcceptanceResult",
    "WorkflowGenerationAttemptOutcome",
    "WorkflowGenerationRuntime",
    "accept_candidate_graph",
    "build_codegen_saved_message",
    "build_persisted_workflow_reply",
    "build_structural_macro_message",
    "exec_deterministic_builder_code",
    "extract_code_from_response",
    "get_generation_stats_hint",
    "iter_workflow_generation_attempt",
    "parse_intent_from_result",
    "prepare_validated_workflow_save",
    "request_builder_code",
    "record_generation_outcome",
    "sandbox_exec_builder_code",
    "should_use_workflow_generation_fast_path",
]


class WorkflowGenerationRuntime:
    """Public thin coordinator for workflow generation."""

    async def generate(self, *args, **kwargs):
        runtime = _runtime.WorkflowGenerationRuntime()
        return await runtime.generate(*args, **kwargs)
