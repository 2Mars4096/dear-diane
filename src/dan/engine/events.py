"""Typed runtime events emitted during graph execution.

Events are fire-and-forget: the engine emits them via an optional async
callback.  Consumers (e.g. the run manager / WebSocket layer) subscribe
to the callback; the engine itself never blocks on delivery.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
    RUN_PROGRESS = "run_progress"
    RUN_LIMIT_REACHED = "run_limit_reached"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    NODE_SKIPPED = "node_skipped"
    NODE_OUTPUT = "node_output"
    NODE_REPAIR_STARTED = "node_repair_started"
    NODE_REPAIR_APPLIED = "node_repair_applied"
    NODE_REPAIR_FAILED = "node_repair_failed"
    NODE_REPAIR_SKIPPED = "node_repair_skipped"
    NODE_OVERLAY_APPLIED = "node_overlay_applied"
    CHILD_WORKFLOW_STARTED = "child_workflow_started"
    CHILD_WORKFLOW_COMPLETED = "child_workflow_completed"
    CHILD_WORKFLOW_FAILED = "child_workflow_failed"
    LOG = "log"
    # -- 5-3: Rich logging event types -----------------------------------------
    LLM_THINKING = "llm_thinking"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_RESULT = "tool_call_result"
    CODE_OUTPUT = "code_output"
    INTERMEDIATE_TEXT = "intermediate_text"
    # -- 6-6: Execution UX event types -----------------------------------------
    ITERATION_STARTED = "iteration_started"
    ITERATION_COMPLETED = "iteration_completed"
    HUMAN_INPUT_NEEDED = "human_input_needed"
    HUMAN_INPUT_RECEIVED = "human_input_received"
    HUMAN_INPUT_RESOLVED = "human_input_resolved"
    # -- 6-10: Gate node event types -------------------------------------------
    GATE_EVALUATED = "gate_evaluated"
    # -- 7-1: Runtime reliability -----------------------------------------------
    RETRY_ATTEMPTED = "retry_attempted"
    # -- 9-1: RAG events -------------------------------------------------------
    RETRIEVAL_STARTED = "retrieval_started"
    RETRIEVAL_COMPLETED = "retrieval_completed"
    # -- 9-2: Sandbox events ----------------------------------------------------
    SANDBOX_STARTED = "sandbox_started"
    SANDBOX_COMPLETED = "sandbox_completed"
    # -- 9-3: Validator events --------------------------------------------------
    VALIDATION_RESULT = "validation_result"
    # -- 6-14: Dead-edge warnings -----------------------------------------------
    DEAD_EDGE_WARNING = "dead_edge_warning"
    # -- 7-9: Async parallel subagents -----------------------------------------
    PARALLEL_BRANCH_STARTED = "parallel_branch_started"
    PARALLEL_BRANCH_COMPLETED = "parallel_branch_completed"
    PARALLEL_FAN_IN_COMPLETED = "parallel_fan_in_completed"
    # -- 15-1: Hyperedge runtime & cost tracking --------------------------------
    HYPEREDGE_APPLIED = "hyperedge_applied"
    HYPEREDGE_VIOLATION = "hyperedge_violation"
    HYPEREDGE_BLOCKED = "hyperedge_blocked"
    MODEL_SELECTED = "model_selected"
    TIER_ESCALATION = "tier_escalation"
    COST_RECORDED = "cost_recorded"
    BUDGET_WARNING = "budget_warning"
    BUDGET_EXCEEDED = "budget_exceeded"
    # -- 16-1: Agent team events -----------------------------------------------
    TEAM_TURN_STARTED = "team_turn_started"
    TEAM_TURN_COMPLETED = "team_turn_completed"
    TEAM_HANDOFF = "team_handoff"
    TEAM_COMPLETED = "team_completed"
    # -- 16-2: Voting / ensemble events ----------------------------------------
    VOTE_STARTED = "vote_started"
    VOTE_CAST = "vote_cast"
    VOTE_COMPLETED = "vote_completed"
    # -- 17-1: Error memory events ---------------------------------------------
    ERROR_MEMORY_INDEXED = "error_memory_indexed"
    ERROR_MEMORY_RETRIEVED = "error_memory_retrieved"
    # -- 17-2: Reflection events -----------------------------------------------
    REFLECTION_STARTED = "reflection_started"
    REFLECTION_COMPLETED = "reflection_completed"
    # -- 17-3: Self-evolving rule events ---------------------------------------
    RULE_GENERATED = "rule_generated"
    RULE_ACTIVATED = "rule_activated"
    RULE_EXPIRED = "rule_expired"
    RULE_DISABLED = "rule_disabled"
    RULE_AUTO_DISABLED = "rule_auto_disabled"
    RULE_PRUNED = "rule_pruned"
    RULE_EFFECTIVENESS_UPDATE = "rule_effectiveness_update"
    # -- 18-3: Agent-directed context events -----------------------------------
    STATE_EXTERNALIZED = "state_externalized"
    LOOP_COMPACTION_APPLIED = "loop_compaction_applied"
    BUDGET_ADVISORY = "budget_advisory"
    # -- 18-1: Smart context assembly events -----------------------------------
    TOKEN_BUDGET_ADVISORY = BUDGET_ADVISORY
    CONTEXT_DEFERRED = "context_deferred"
    INPUT_SUMMARIZED = "input_summarized"
    JIT_SCHEMA_LOADED = "jit_schema_loaded"
    PAYLOAD_PRUNED = "payload_pruned"
    CONTEXT_TOOL_CALLED = "context_tool_called"
    # -- 18-2: Caching layer events -------------------------------------------
    CACHE_HIT = "cache_hit"
    CACHE_MISS = "cache_miss"
    CACHE_INVALIDATED = "cache_invalidated"
    SEMANTIC_CACHE_HIT = "semantic_cache_hit"
    # -- 18-4: Token analytics events ------------------------------------------
    TOKEN_BREAKDOWN_RECORDED = "token_breakdown_recorded"
    WASTE_DETECTED = "waste_detected"
    OPTIMIZATION_REPORT_READY = "optimization_report_ready"
    OPTIMIZATION_APPLIED = "optimization_applied"
    # -- 19-1: Experience memory events ----------------------------------------
    EXPERIENCE_CONSOLIDATED = "experience_consolidated"
    EXPERIENCE_INDEXED = "experience_indexed"
    # -- 13-2: Checkpoint portal events ----------------------------------------
    RERUN_STARTED = "rerun_started"
    # -- 19-4: Meta-orchestrator events ----------------------------------------
    META_SESSION_STARTED = "meta_session_started"
    META_PLAN_CREATED = "meta_plan_created"
    META_EXECUTION_STARTED = "meta_execution_started"
    META_DIAGNOSIS_STARTED = "meta_diagnosis_started"
    META_REPAIR_APPLIED = "meta_repair_applied"
    META_REDESIGN_TRIGGERED = "meta_redesign_triggered"
    META_PAUSED = "meta_paused"
    META_RESUMED = "meta_resumed"
    META_SESSION_COMPLETED = "meta_session_completed"
    META_SESSION_FAILED = "meta_session_failed"


@dataclass(frozen=True)
class EngineEvent:
    """Single event emitted during execution."""

    event_type: EventType
    run_id: str
    timestamp: float = field(default_factory=time.time)
    node_id: str | None = None
    node_type: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EngineEvent":
        """Deserialize an EngineEvent from a dict (inverse of to_dict)."""
        event_type_str = data.get("event_type", "")
        try:
            event_type = EventType(event_type_str)
        except ValueError:
            import logging
            logging.getLogger(__name__).warning(
                "Unknown event type %r, falling back to LOG", event_type_str,
            )
            event_type = EventType.LOG
        return cls(
            event_type=event_type,
            run_id=data.get("run_id", ""),
            timestamp=data.get("timestamp", 0.0),
            node_id=data.get("node_id"),
            node_type=data.get("node_type"),
            data=data.get("data") or {},
        )
