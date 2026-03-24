"""agent_runtime — reusable single-agent loop extracted from ChatManager.

This module owns the core agent turn contract: request types, the runtime
protocol, and the base implementation.  Surfaces (ChatManager, CLI,
concierge tier executors) compose the runtime rather than reimplementing
the agent loop themselves.

Import boundary: agent_runtime may depend on ``llm_core``, ``models``, and
neutral shared contracts such as ``chat_events``. It must NOT import from
``server``, ``concierge``, ``cli``, or ``engine``.
"""

from dan.agent_runtime.completion import (
    iter_guarded_completion,
)
from dan.agent_runtime.followup import (
    build_assistant_followup_message,
    build_post_tool_failure_content,
    prepare_tool_followup_request,
    run_tool_followup_attempt,
    ToolFollowupAttemptResolution,
)
from dan.agent_runtime.messages import (
    MentionResolution,
    PromptModuleResolution,
    build_runtime_messages,
)
from dan.agent_runtime.mutation_preview import (
    CompiledMutationPreview,
    PreparedMutationAutoApply,
    build_mutation_repair_messages,
    compile_mutation_preview,
    prepare_mutation_auto_apply,
)
from dan.agent_runtime.profiles import (
    ResolvedAgentProfile,
    normalize_agent_mode,
    resolve_agent_profile,
)
from dan.agent_runtime.graph_summary import (
    build_graph_summary,
    compute_graph_revision,
    serialize_for_prompt,
)
from dan.agent_runtime.orchestration import (
    ChildExecutionResolution,
    ChildSessionPlan,
    build_mixed_execution_groups,
    estimate_child_tier,
    execute_child_execution_policy,
    merge_child_result_usage,
    merge_token_usage,
    plan_child_session,
    should_llm_synthesize,
    synthesize_child_results,
)
from dan.agent_runtime.tool_loop import (
    NoToolTurnResolution,
    resolve_no_tool_turn,
)
from dan.agent_runtime.synthesis import (
    SynthesisGapReview,
    collect_synthesis_uncertainties,
    deterministic_synthesis_gap_review,
    find_synthesis_gap_reason,
    parse_subtask_decomposition_response,
    parse_synthesis_review_response,
    planned_subtasks,
)
from dan.agent_runtime.runtime import AgentRuntime, BaseAgentRuntime
from dan.agent_runtime.recovery import (
    PostToolFollowupResolution,
    recover_text_completion,
    resolve_post_tool_followup_failure,
    run_post_tool_followup_flow,
)
from dan.agent_runtime.tokens import (
    MODEL_CONTEXT_WINDOWS,
    compact_history,
    context_pressure_hint,
    estimate_tokens,
)
from dan.agent_runtime.types import AgentEvent, AgentProfile, AgentRequest, AgentResult

__all__ = [
    "AgentEvent",
    "AgentProfile",
    "AgentRequest",
    "AgentResult",
    "AgentRuntime",
    "BaseAgentRuntime",
    "ChildExecutionResolution",
    "ChildSessionPlan",
    "CompiledMutationPreview",
    "MentionResolution",
    "PreparedMutationAutoApply",
    "MODEL_CONTEXT_WINDOWS",
    "PostToolFollowupResolution",
    "PromptModuleResolution",
    "ResolvedAgentProfile",
    "SynthesisGapReview",
    "ToolFollowupAttemptResolution",
    "build_mixed_execution_groups",
    "build_assistant_followup_message",
    "build_graph_summary",
    "build_mutation_repair_messages",
    "build_post_tool_failure_content",
    "build_runtime_messages",
    "collect_synthesis_uncertainties",
    "compact_history",
    "compile_mutation_preview",
    "compute_graph_revision",
    "context_pressure_hint",
    "deterministic_synthesis_gap_review",
    "estimate_child_tier",
    "estimate_tokens",
    "execute_child_execution_policy",
    "find_synthesis_gap_reason",
    "iter_guarded_completion",
    "merge_child_result_usage",
    "merge_token_usage",
    "normalize_agent_mode",
    "NoToolTurnResolution",
    "parse_subtask_decomposition_response",
    "parse_synthesis_review_response",
    "plan_child_session",
    "planned_subtasks",
    "prepare_mutation_auto_apply",
    "prepare_tool_followup_request",
    "recover_text_completion",
    "resolve_agent_profile",
    "resolve_no_tool_turn",
    "resolve_post_tool_followup_failure",
    "run_post_tool_followup_flow",
    "run_tool_followup_attempt",
    "serialize_for_prompt",
    "should_llm_synthesize",
    "synthesize_child_results",
]
