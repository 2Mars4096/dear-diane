"""Chat stream event models and graph summary types.

This neutral module lets orchestrators and other shared layers depend on chat
event shapes without importing through ``dan.server``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class NodeSummary(BaseModel):
    id: str
    name: str
    node_type: str
    description: str = ""
    input_ports: list[str] = Field(default_factory=list)
    output_ports: list[str] = Field(default_factory=list)
    model: str | None = None
    prompt_snippet: str | None = None


class EdgeSummary(BaseModel):
    edge_type: str
    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str


class GraphSummary(BaseModel):
    workflow_id: str
    name: str
    description: str
    node_count: int
    edge_count: int
    nodes: list[NodeSummary] = Field(default_factory=list)
    edges: list[EdgeSummary] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    exit_points: list[str] = Field(default_factory=list)
    revision: str


class ChatTokenEvent(BaseModel):
    type: str = "chat_token"
    delta: str
    accumulated: str


class ChatCompleteEvent(BaseModel):
    type: str = "chat_complete"
    message_id: str
    content: str
    token_usage: dict[str, int] = Field(default_factory=dict)
    estimated_cost: float | None = None
    context_window: int = 0
    graph_revision: str
    revision_mismatch: bool = False
    detected_mode: str | None = None
    phase_label: str | None = None
    stream_channel_id: str | None = None


class ChatErrorEvent(BaseModel):
    type: str = "chat_error"
    error: str


class ChatNoticeEvent(BaseModel):
    type: str = "chat_notice"
    content: str
    level: str = "info"


class ChatMutationEvent(BaseModel):
    type: str = "chat_mutation"
    message_id: str
    content: str
    mutation_plan: dict[str, Any]
    dry_run_result: dict[str, Any]
    token_usage: dict[str, int] = Field(default_factory=dict)
    context_window: int = 0
    graph_revision: str
    revision_mismatch: bool = False
    detected_mode: str | None = None
    applied: bool = False


class ChatInterruptedEvent(BaseModel):
    type: str = "chat_interrupted"
    message_id: str
    content: str
    token_usage: dict[str, int] = Field(default_factory=dict)


class ChatToolCallStartEvent(BaseModel):
    type: str = "chat_tool_call_start"
    tool_call_id: str
    tool_name: str
    args_preview: str


class ChatToolCallResultEvent(BaseModel):
    type: str = "chat_tool_call_result"
    tool_call_id: str
    tool_name: str
    status: str
    output_preview: str
    duration_ms: int


class ChatIntentExtractedEvent(BaseModel):
    type: str = "chat_intent_extracted"
    intent_summary: str
    stage_count: int = 0
    fully_covered: bool = False


class ChatCodeGeneratedEvent(BaseModel):
    type: str = "chat_code_generated"
    code_snippet: str
    source: str = "codegen"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatValidationResultEvent(BaseModel):
    type: str = "chat_validation_result"
    success: bool
    error_count: int = 0
    errors: list[str] = Field(default_factory=list)
    failure_mode: str | None = None
    build_status: str | None = None
    auto_fix_count: int = 0
    auto_fixes: list[str] = Field(default_factory=list)
    failure_bucket: str | None = None
    handoff_reason: str | None = None
    build_summary: str | None = None
    automatic_recovery: dict[str, Any] = Field(default_factory=dict)


class ChatGraphCreatedEvent(BaseModel):
    type: str = "chat_graph_created"
    workflow_id: str
    node_count: int = 0
    edge_count: int = 0
    graph_revision: str = ""


class ChatGraphQualityEvent(BaseModel):
    type: str = "chat_graph_quality"
    score: int = 0
    concerns: list[str] = Field(default_factory=list)


class ChatQueuedEvent(BaseModel):
    type: str = "chat_queued"
    stream_channel_id: str
    correlation_id: str
    queue_position: int = 0


class ChatTaskAckEvent(BaseModel):
    type: str = "task_ack"
    task_id: str
    title: str
    state: str
    dispatch_mode: str
    summary: str = ""
    stream_channel_id: str | None = None
    queue_position: int = 0


class ChatTaskNotificationEvent(BaseModel):
    type: str = "task_notification"
    task_id: str
    title: str
    state: str
    attention_reason: str
    summary_line: str


class ChatTaskNotificationBatchEvent(BaseModel):
    type: str = "task_notification_batch"
    notifications: list[ChatTaskNotificationEvent] = Field(default_factory=list)


class ChatFileAttachmentEvent(BaseModel):
    type: str = "chat_file_attachment"
    path: str
    filename: str
    size: int


class ChatPollRequestEvent(BaseModel):
    type: str = "chat_poll_request"
    question: str
    options: list[str] = Field(default_factory=list)
    is_anonymous: bool = False
    allows_multiple: bool = False


class ChatMultiPartEvent(BaseModel):
    type: str = "chat_multi_part"
    parts: list[str]


class ChatGenerationSummaryEvent(BaseModel):
    type: str = "chat_generation_summary"
    path_taken: str = "none"
    retries_used: dict[str, int] = Field(default_factory=dict)
    quality_score: int | None = None
    wall_clock_ms: int = 0
    fallback_chain: list[str] = Field(default_factory=list)
    node_count: int | None = None
    complexity_tier: str | None = None
    pre_generation_ms: int | None = None
    failure_mode: str | None = None
    build_status: str | None = None
    auto_fix_count: int = 0
    auto_fixes: list[str] = Field(default_factory=list)
    failure_bucket: str | None = None
    handoff_reason: str | None = None
    build_summary: str | None = None
    automatic_recovery: dict[str, Any] = Field(default_factory=dict)


class ChatInjectedMessageEvent(BaseModel):
    type: str = "chat_injected_message"
    content: str
    inject_id: str


ChatStreamEvent = (
    ChatTokenEvent
    | ChatCompleteEvent
    | ChatErrorEvent
    | ChatNoticeEvent
    | ChatMutationEvent
    | ChatInterruptedEvent
    | ChatToolCallStartEvent
    | ChatToolCallResultEvent
    | ChatIntentExtractedEvent
    | ChatCodeGeneratedEvent
    | ChatValidationResultEvent
    | ChatGraphCreatedEvent
    | ChatGraphQualityEvent
    | ChatQueuedEvent
    | ChatTaskAckEvent
    | ChatTaskNotificationEvent
    | ChatTaskNotificationBatchEvent
    | ChatFileAttachmentEvent
    | ChatPollRequestEvent
    | ChatMultiPartEvent
    | ChatGenerationSummaryEvent
    | ChatInjectedMessageEvent
)


__all__ = [
    "ChatCodeGeneratedEvent",
    "ChatCompleteEvent",
    "ChatErrorEvent",
    "ChatFileAttachmentEvent",
    "ChatGenerationSummaryEvent",
    "ChatGraphCreatedEvent",
    "ChatGraphQualityEvent",
    "ChatInjectedMessageEvent",
    "ChatIntentExtractedEvent",
    "ChatInterruptedEvent",
    "ChatMultiPartEvent",
    "ChatMutationEvent",
    "ChatNoticeEvent",
    "ChatPollRequestEvent",
    "ChatQueuedEvent",
    "ChatTaskAckEvent",
    "ChatTaskNotificationEvent",
    "ChatTaskNotificationBatchEvent",
    "ChatStreamEvent",
    "ChatTokenEvent",
    "ChatToolCallResultEvent",
    "ChatToolCallStartEvent",
    "ChatValidationResultEvent",
    "EdgeSummary",
    "GraphSummary",
    "NodeSummary",
]
