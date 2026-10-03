"""Core progress narrator contracts.

The narrator lane is intentionally snapshot-only. It explains already-known
run state for status/progress questions, but it never receives workspace tools
or mutation capabilities.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence


NARRATOR_READ_ONLY = "narrator read-only"
EXECUTOR_READ_ONLY = "executor read-only"
EXECUTOR_WRITE = "executor write"
PLAN_MODE = "plan mode"
CLARIFICATION = "clarification"

ANSWER_BUDGET_BRIEF = "brief"
ANSWER_BUDGET_NORMAL = "normal"
ANSWER_BUDGET_DETAILED = "detailed"
ANSWER_BUDGETS = frozenset(
    {
        ANSWER_BUDGET_BRIEF,
        ANSWER_BUDGET_NORMAL,
        ANSWER_BUDGET_DETAILED,
    }
)

LATENCY_FAST = "fast"
LATENCY_BALANCED = "balanced"
LATENCY_DEEP = "deep"
LATENCY_PREFERENCES = frozenset({LATENCY_FAST, LATENCY_BALANCED, LATENCY_DEEP})

PROGRESS_QUIET = "quiet"
PROGRESS_COMPACT = "compact"
PROGRESS_VERBOSE = "verbose"
PROGRESS_DETAILS = frozenset({PROGRESS_QUIET, PROGRESS_COMPACT, PROGRESS_VERBOSE})

INTERACTION_ANSWER_ONLY = "answer_only"
INTERACTION_FINDINGS = "findings"
INTERACTION_ACT_THEN_REPORT = "act_then_report"
INTERACTION_REVIEW = "review"
INTERACTION_AUTONOMOUS_PROGRESS = "autonomous_progress"
INTERACTION_STYLES = frozenset(
    {
        INTERACTION_ANSWER_ONLY,
        INTERACTION_FINDINGS,
        INTERACTION_ACT_THEN_REPORT,
        INTERACTION_REVIEW,
        INTERACTION_AUTONOMOUS_PROGRESS,
    }
)

EXECUTION_GUIDED = "guided"
EXECUTION_CONTINUOUS = "continuous"
EXECUTION_MANUAL = "manual"
EXECUTION_AUTONOMY_MODES = frozenset({EXECUTION_GUIDED, EXECUTION_CONTINUOUS, EXECUTION_MANUAL})

STOP_OBJECTIVE_SATISFIED = "objective_satisfied"
STOP_VALIDATION_PASSES = "validation_passes"
STOP_PRECISE_BLOCKER = "precise_blocker"
EXECUTION_STOP_CONDITIONS = frozenset(
    {
        STOP_OBJECTIVE_SATISFIED,
        STOP_VALIDATION_PASSES,
        STOP_PRECISE_BLOCKER,
    }
)

CONTROLLER_RESPOND = "respond"
CONTROLLER_NARRATE_RUN = "narrate_run"
CONTROLLER_INSPECT = "inspect"
CONTROLLER_EXECUTE = "execute"
CONTROLLER_PLAN = "plan"
CONTROLLER_CLARIFY = "clarify"
CONTROLLER_LANES = frozenset(
    {
        CONTROLLER_RESPOND,
        CONTROLLER_NARRATE_RUN,
        CONTROLLER_INSPECT,
        CONTROLLER_EXECUTE,
        CONTROLLER_PLAN,
        CONTROLLER_CLARIFY,
    }
)

PERMISSION_NONE = "none"
PERMISSION_READ_ONLY = "read_only"
PERMISSION_TRANSIENT_EXECUTE = "transient_execute"
PERMISSION_WORKSPACE_WRITE = "workspace_write"
PERMISSION_EXTERNAL_WRITE = "external_write"
PERMISSION_SCOPES = frozenset(
    {
        PERMISSION_NONE,
        PERMISSION_READ_ONLY,
        PERMISSION_TRANSIENT_EXECUTE,
        PERMISSION_WORKSPACE_WRITE,
        PERMISSION_EXTERNAL_WRITE,
    }
)

CAPABILITY_PACK_BROWSER_CONTROL = "browser_control"
CAPABILITY_PACK_DESKTOP_CONTROL = "desktop_control"
CAPABILITY_PACK_COMPUTER_CONTROL = "computer_control"
CAPABILITY_PACKS = frozenset(
    {
        CAPABILITY_PACK_BROWSER_CONTROL,
        CAPABILITY_PACK_DESKTOP_CONTROL,
        CAPABILITY_PACK_COMPUTER_CONTROL,
    }
)

EVIDENCE_KNOWN_STATE = "known_state"
EVIDENCE_VALIDATED_CACHE = "validated_cache"
EVIDENCE_FRESH = "fresh"
EVIDENCE_FRESHNESS_VALUES = frozenset({EVIDENCE_KNOWN_STATE, EVIDENCE_VALIDATED_CACHE, EVIDENCE_FRESH})

EVIDENCE_SCOPE_NONE = "none"
EVIDENCE_SCOPE_TARGETED = "targeted"
EVIDENCE_SCOPE_BROAD = "broad"
EVIDENCE_SCOPE_VALUES = frozenset({EVIDENCE_SCOPE_NONE, EVIDENCE_SCOPE_TARGETED, EVIDENCE_SCOPE_BROAD})

EVIDENCE_SOURCE_CONVERSATION = "conversation"
EVIDENCE_SOURCE_RUN_STATE = "run_state"
EVIDENCE_SOURCE_WORKSPACE = "workspace"
EVIDENCE_SOURCE_VALIDATION = "validation"
EVIDENCE_SOURCE_EXTERNAL = "external"
EVIDENCE_SOURCES = frozenset(
    {
        EVIDENCE_SOURCE_CONVERSATION,
        EVIDENCE_SOURCE_RUN_STATE,
        EVIDENCE_SOURCE_WORKSPACE,
        EVIDENCE_SOURCE_VALIDATION,
        EVIDENCE_SOURCE_EXTERNAL,
    }
)

PHASE_NONE = "none"
PHASE_SNAPSHOT = "snapshot"
PHASE_PROBE = "probe"
PHASE_ONE_PASS = "one_pass"
PHASE_VALIDATION_GATE = "validation_gate"
PHASE_REPAIR_LOOP = "repair_loop"
PHASE_MONITOR = "monitor"
PHASE_SHAPES = frozenset(
    {
        PHASE_NONE,
        PHASE_SNAPSHOT,
        PHASE_PROBE,
        PHASE_ONE_PASS,
        PHASE_VALIDATION_GATE,
        PHASE_REPAIR_LOOP,
        PHASE_MONITOR,
    }
)

LATENCY_INSTANT = "instant"
LATENCY_NORMAL = "normal"
LATENCY_BACKGROUND = "background"
LATENCY_CLASSES = frozenset({LATENCY_INSTANT, LATENCY_FAST, LATENCY_NORMAL, LATENCY_DEEP, LATENCY_BACKGROUND})

PROGRESS_NONE = "none"
PROGRESS_DEBUG = "debug"

ADMISSION_AUTO = "auto"
ADMISSION_STATUS_ONLY = "status_only"
ADMISSION_APPEND_ACTIVE = "append_active"
ADMISSION_NEW_TASK = "new_task"
ADMISSION_TASK_RELATIONS = frozenset(
    {
        ADMISSION_AUTO,
        ADMISSION_STATUS_ONLY,
        ADMISSION_APPEND_ACTIVE,
        ADMISSION_NEW_TASK,
    }
)

def tokenize_intent_text(text: str) -> tuple[str, ...]:
    """Return lowercase word tokens for non-routing local text utilities."""

    normalized = str(text or "").lower().replace("’", "'")
    normalized = normalized.replace("what's", "what is").replace("whats", "what is")
    tokens: list[str] = []
    current: list[str] = []
    for char in normalized:
        if char.isalnum():
            current.append(char)
            continue
        if current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def _intent_command(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return ""
    return stripped.split(maxsplit=1)[0].lower()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clip(value: Any, *, limit: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _compact_list(values: Sequence[Any], *, limit: int = 8) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _display_path(path: str, workspace: str = "") -> str:
    text = str(path or "").strip()
    root = str(workspace or "").strip().rstrip("/")
    if root and text.startswith(root + "/"):
        return text[len(root) + 1 :]
    return text


def _clean_progress_line(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\s*-\s*", "", text)
    if not text:
        return ""
    if text.startswith("Progress summary requested") or text.startswith("Narrator "):
        return ""
    if "Workspace context checked" in text:
        match = re.search(r"\((\d+\s+items?)\)", text)
        suffix = f" ({match.group(1)})" if match else ""
        return f"workspace context checked{suffix}"
    if "Terminal command finished" in text:
        return _clip(text, limit=140)
    if text.startswith("Model provider ready"):
        return "model provider ready"
    if text.startswith("Validation "):
        return text.rstrip(".")
    if text.startswith("File changed:"):
        return text
    if "{" in text or "}" in text or "result=" in text or "arguments=" in text:
        return ""
    return _clip(text, limit=150)


def _latest_clean_progress(values: Sequence[str]) -> str:
    for value in reversed(values):
        cleaned = _clean_progress_line(value)
        if cleaned:
            return cleaned
    return ""


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _heartbeat_visible_step(snapshot: RunNarratorSnapshot) -> str:
    phase = str(snapshot.phase or "").strip().lower()
    current = _clean_progress_line(snapshot.current_step) or _clip(snapshot.current_step, limit=120)
    if current.lower().startswith(("still running:", "still working", "waiting on ")):
        current = ""
    if current:
        return current
    if "model" in phase:
        return "waiting for the model response"
    if "valid" in phase:
        return "validation is still running"
    if "repair" in phase or "retry" in phase:
        return "the repair pass is still running"
    if "tool" in phase:
        return "waiting for the active tool to finish"
    if phase:
        return f"{phase.replace('_', ' ')} is still running"
    return ""


def _is_next_step_question(text: str) -> bool:
    """Detect next-step phrasing for deterministic narration only, not routing."""

    tokens = tokenize_intent_text(text)
    phrases = (
        ("what", "is", "next"),
        ("what", "now"),
        ("next", "step"),
        ("next", "expected"),
        ("where", "next"),
        ("what", "shall", "we", "do", "next"),
        ("what", "should", "we", "do", "next"),
        ("what", "can", "we", "do", "next"),
    )
    for phrase in phrases:
        width = len(phrase)
        if width > len(tokens):
            continue
        for index in range(len(tokens) - width + 1):
            if tuple(tokens[index : index + width]) == phrase:
                return True
    return False


def _status_line(snapshot: RunNarratorSnapshot) -> str:
    state = snapshot.status or "unknown"
    phase = snapshot.phase or "unknown"
    elapsed = f" after {_format_elapsed(snapshot.elapsed_seconds)}" if float(snapshot.elapsed_seconds or 0.0) >= 1.0 else ""
    if state in {"completed", "done"}:
        return f"Status: complete{elapsed}; final phase was {phase}."
    if state in {"failed", "blocked", "stopped"}:
        return f"Status: {state}{elapsed}; phase is {phase}."
    return f"Status: {state}{elapsed}; phase is {phase}."


def _next_step_line(snapshot: RunNarratorSnapshot) -> str:
    state = str(snapshot.status or "").strip().lower()
    phase = str(snapshot.phase or "").strip().lower()
    queued = _clip(snapshot.queued_work, limit=120)
    current = _clean_progress_line(snapshot.current_step) or _clip(snapshot.current_step, limit=120)
    if snapshot.blockers:
        return f"Next: address the visible blocker: {_clip(snapshot.blockers[-1], limit=120)}."
    if state in {"completed", "done"}:
        return "Next: the run is already complete; review the changed files or start a new request."
    if state in {"failed", "blocked", "stopped"}:
        return "Next: inspect the failure/blocker and run a repair or follow-up request."
    if queued:
        return f"Next: queued {queued} should run after the current phase."
    if "valid" in phase or "valid" in current.lower():
        return "Next: validation should finish; if it passes the run can complete, otherwise repair/follow-up work will be queued."
    if "model" in phase:
        return "Next: the model is choosing whether to call a tool, validate, or finish from the current workspace state."
    if "tool" in phase:
        return "Next: the active tool needs to finish, then the run will decide whether to validate or continue."
    if current:
        return f"Next: continue the current step: {current}."
    return "Next: wait for the next run event; no more specific next action is visible in the snapshot."


@dataclass(frozen=True)
class AgentTurnIntentDecision:
    """Three-lane front-door decision shared by surfaces."""

    lane: str
    confidence: float
    rationale: str
    executor_effort: str = ""
    clarification: str = ""
    communication_policy: Any = None
    execution_policy: Any = None
    surface_policy: Any = None

    def __post_init__(self) -> None:
        if self.communication_policy is None:
            object.__setattr__(
                self,
                "communication_policy",
                normalize_agent_communication_policy(
                    lane=self.lane,
                    executor_effort=self.executor_effort,
                ),
            )
        if self.execution_policy is None:
            object.__setattr__(
                self,
                "execution_policy",
                normalize_agent_execution_policy(
                    lane=self.lane,
                    communication_policy=self.communication_policy,
                ),
            )
        if self.surface_policy is None:
            object.__setattr__(
                self,
                "surface_policy",
                normalize_agent_surface_policy(
                    lane=self.lane,
                    executor_effort=self.executor_effort,
                    communication_policy=self.communication_policy,
                    execution_policy=self.execution_policy,
                ),
            )

    @property
    def needs_clarification(self) -> bool:
        return self.lane == CLARIFICATION or bool(self.clarification)

    @property
    def is_narrator(self) -> bool:
        return self.lane == NARRATOR_READ_ONLY


def classify_agent_turn_intent(text: str) -> AgentTurnIntentDecision:
    """Classify only explicit command turns without keyword or regex routing.

    Free-text requests should go through ``route_agent_turn_intent_with_model``.
    This function intentionally refuses to guess natural-language intent when a
    model router is unavailable.
    """

    stripped = str(text or "").strip()
    if not stripped:
        return AgentTurnIntentDecision(
            lane=CLARIFICATION,
            confidence=0.0,
            rationale="empty input",
            clarification="What should Diane do?",
        )
    command = _intent_command(stripped)
    if command in {"/progress", "/last", "/narrate"}:
        return AgentTurnIntentDecision(
            lane=NARRATOR_READ_ONLY,
            confidence=1.0,
            rationale="explicit progress command",
        )
    return AgentTurnIntentDecision(
        lane=CLARIFICATION,
        confidence=0.35,
        rationale="free-text routing requires model-assisted intent classification",
        clarification=(
            "I need the model router to decide whether this is progress, read-only inspection, "
            "or workspace-changing work."
        ),
    )


def _first_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from model text without regex."""

    raw = str(text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    start = raw.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(raw)):
            char = raw[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(raw[start : index + 1])
                    break
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _normalize_model_route_lane(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _normalize_policy_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


@dataclass(frozen=True)
class AgentCommunicationPolicy:
    """Model-authored response-shaping policy for agent-facing surfaces."""

    answer_budget: str = ANSWER_BUDGET_NORMAL
    latency_preference: str = LATENCY_BALANCED
    progress_detail: str = PROGRESS_COMPACT
    interaction_style: str = INTERACTION_ACT_THEN_REPORT

    @property
    def needs_progress_detail(self) -> bool:
        return self.progress_detail == PROGRESS_VERBOSE

    def to_payload(self) -> dict[str, Any]:
        return {
            "answer_budget": self.answer_budget,
            "latency_preference": self.latency_preference,
            "progress_detail": self.progress_detail,
            "interaction_style": self.interaction_style,
            "needs_progress_detail": self.needs_progress_detail,
        }


@dataclass(frozen=True)
class AgentExecutionPolicy:
    """Model-authored autonomy and live-loop budget policy for agent-facing surfaces."""

    autonomy_mode: str = EXECUTION_GUIDED
    stop_condition: str = STOP_OBJECTIVE_SATISFIED
    max_work_seconds: int | None = None
    max_auto_fix_rounds: int = 1
    max_validation_cycles: int = 2
    allow_repair_cycles: bool = True

    def to_payload(self) -> dict[str, Any]:
        return {
            "autonomy_mode": self.autonomy_mode,
            "mode": self.autonomy_mode,
            "stop_condition": self.stop_condition,
            "max_work_seconds": self.max_work_seconds,
            "max_auto_fix_rounds": self.max_auto_fix_rounds,
            "max_validation_cycles": self.max_validation_cycles,
            "allow_repair_cycles": self.allow_repair_cycles,
        }


@dataclass(frozen=True)
class AgentEvidencePolicy:
    """Evidence requirements independent from lane, permissions, and answer shape."""

    freshness: str = EVIDENCE_KNOWN_STATE
    scope: str = EVIDENCE_SCOPE_NONE
    sources: tuple[str, ...] = (EVIDENCE_SOURCE_CONVERSATION,)

    def to_payload(self) -> dict[str, Any]:
        return {
            "freshness": self.freshness,
            "scope": self.scope,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class AgentLatencyPolicy:
    """Time-budget requirements independent from final-answer length."""

    latency_class: str = LATENCY_NORMAL
    max_work_seconds: int | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "class": self.latency_class,
            "max_work_seconds": self.max_work_seconds,
        }


@dataclass(frozen=True)
class AgentResponsePolicy:
    """Final-answer shape independent from progress visibility and work budget."""

    answer_budget: str = ANSWER_BUDGET_NORMAL
    interaction_style: str = INTERACTION_ACT_THEN_REPORT

    def to_payload(self) -> dict[str, Any]:
        return {
            "answer_budget": self.answer_budget,
            "interaction_style": self.interaction_style,
        }


@dataclass(frozen=True)
class AgentProgressPolicy:
    """Live progress visibility independent from final-answer length."""

    detail: str = PROGRESS_COMPACT
    heartbeat_seconds: int = 10

    def to_payload(self) -> dict[str, Any]:
        return {
            "detail": self.detail,
            "heartbeat_seconds": self.heartbeat_seconds,
        }


@dataclass(frozen=True)
class AgentAdmissionPolicy:
    """Relationship between this turn and active background work."""

    task_relation: str = ADMISSION_AUTO

    def to_payload(self) -> dict[str, Any]:
        return {"task_relation": self.task_relation}


@dataclass(frozen=True)
class AgentSurfacePolicy:
    """Canonical orthogonal policy for Diane-style agent surfaces.

    Legacy lane, communication_policy, and execution_policy are still carried
    as compatibility projections, but this object is the complete policy the
    model router should author.
    """

    controller_lane: str = CONTROLLER_RESPOND
    permission_scope: str = PERMISSION_NONE
    evidence_policy: AgentEvidencePolicy = field(default_factory=AgentEvidencePolicy)
    phase_shape: str = PHASE_NONE
    autonomy: str = EXECUTION_GUIDED
    latency_policy: AgentLatencyPolicy = field(default_factory=AgentLatencyPolicy)
    response_policy: AgentResponsePolicy = field(default_factory=AgentResponsePolicy)
    progress_policy: AgentProgressPolicy = field(default_factory=AgentProgressPolicy)
    admission_policy: AgentAdmissionPolicy = field(default_factory=AgentAdmissionPolicy)
    capability_packs: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "controller_lane": self.controller_lane,
            "permission_scope": self.permission_scope,
            "evidence_policy": self.evidence_policy.to_payload(),
            "phase_shape": self.phase_shape,
            "autonomy": self.autonomy,
            "latency_policy": self.latency_policy.to_payload(),
            "response_policy": self.response_policy.to_payload(),
            "progress_policy": self.progress_policy.to_payload(),
            "admission_policy": self.admission_policy.to_payload(),
            "capability_packs": list(self.capability_packs),
        }


def _normalize_answer_budget(value: Any, *, default: str = ANSWER_BUDGET_NORMAL) -> str:
    token = _normalize_policy_token(value)
    aliases = {
        "short": ANSWER_BUDGET_BRIEF,
        "small": ANSWER_BUDGET_BRIEF,
        "tiny": ANSWER_BUDGET_BRIEF,
        "quick": ANSWER_BUDGET_BRIEF,
        "concise": ANSWER_BUDGET_NORMAL,
        "standard": ANSWER_BUDGET_NORMAL,
        "regular": ANSWER_BUDGET_NORMAL,
        "full": ANSWER_BUDGET_DETAILED,
        "long": ANSWER_BUDGET_DETAILED,
        "deep": ANSWER_BUDGET_DETAILED,
        "findings": ANSWER_BUDGET_DETAILED,
        "autonomous_progress": ANSWER_BUDGET_BRIEF,
    }
    token = aliases.get(token, token)
    return token if token in ANSWER_BUDGETS else default


def _normalize_latency_preference(value: Any, *, default: str = LATENCY_BALANCED) -> str:
    token = _normalize_policy_token(value)
    aliases = {
        "instant": LATENCY_FAST,
        "low_latency": LATENCY_FAST,
        "quick": LATENCY_FAST,
        "brief": LATENCY_FAST,
        "normal": LATENCY_BALANCED,
        "standard": LATENCY_BALANCED,
        "balanced": LATENCY_BALANCED,
        "thorough": LATENCY_DEEP,
        "deeper": LATENCY_DEEP,
        "slow": LATENCY_DEEP,
        "background": LATENCY_DEEP,
    }
    token = aliases.get(token, token)
    return token if token in LATENCY_PREFERENCES else default


def _normalize_progress_detail(
    value: Any,
    *,
    needs_progress_detail: Any = None,
    default: str = PROGRESS_COMPACT,
) -> str:
    token = _normalize_policy_token(value)
    aliases = {
        "none": PROGRESS_QUIET,
        "false": PROGRESS_QUIET,
        "off": PROGRESS_QUIET,
        "minimal": PROGRESS_QUIET,
        "low": PROGRESS_QUIET,
        "normal": PROGRESS_COMPACT,
        "standard": PROGRESS_COMPACT,
        "brief": PROGRESS_COMPACT,
        "autonomous_progress": PROGRESS_COMPACT,
        "high": PROGRESS_VERBOSE,
        "full": PROGRESS_VERBOSE,
        "true": PROGRESS_VERBOSE,
        "detailed": PROGRESS_VERBOSE,
    }
    token = aliases.get(token, token)
    if token in PROGRESS_DETAILS:
        return token
    if isinstance(needs_progress_detail, bool):
        return PROGRESS_VERBOSE if needs_progress_detail else PROGRESS_QUIET
    return default


def _normalize_interaction_style(value: Any, *, default: str = INTERACTION_ACT_THEN_REPORT) -> str:
    token = _normalize_policy_token(value)
    aliases = {
        "answer": INTERACTION_ANSWER_ONLY,
        "answer_only": INTERACTION_ANSWER_ONLY,
        "chat": INTERACTION_ANSWER_ONLY,
        "status": INTERACTION_ANSWER_ONLY,
        "report": INTERACTION_ACT_THEN_REPORT,
        "execute": INTERACTION_ACT_THEN_REPORT,
        "act": INTERACTION_ACT_THEN_REPORT,
        "act_then_report": INTERACTION_ACT_THEN_REPORT,
        "findings": INTERACTION_FINDINGS,
        "audit": INTERACTION_REVIEW,
        "review": INTERACTION_REVIEW,
        "autonomous": INTERACTION_AUTONOMOUS_PROGRESS,
        "autonomous_loop": INTERACTION_AUTONOMOUS_PROGRESS,
        "autonomous_progress": INTERACTION_AUTONOMOUS_PROGRESS,
        "loop": INTERACTION_AUTONOMOUS_PROGRESS,
    }
    token = aliases.get(token, token)
    return token if token in INTERACTION_STYLES else default


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _surface_policy_payload(payload: Mapping[str, Any] | AgentSurfacePolicy | None) -> Mapping[str, Any]:
    if isinstance(payload, AgentSurfacePolicy):
        return payload.to_payload()
    if not isinstance(payload, Mapping):
        return {}
    nested = payload.get("surface_policy") or payload.get("agent_policy") or payload.get("policy")
    if isinstance(nested, Mapping):
        return nested
    return payload


def _normalize_controller_lane(value: Any, *, default: str = CONTROLLER_RESPOND) -> str:
    token = _normalize_policy_token(value)
    aliases = {
        "chat": CONTROLLER_RESPOND,
        "answer": CONTROLLER_RESPOND,
        "answer_only": CONTROLLER_RESPOND,
        "narrator": CONTROLLER_NARRATE_RUN,
        "narrator_read_only": CONTROLLER_NARRATE_RUN,
        "progress": CONTROLLER_NARRATE_RUN,
        "status": CONTROLLER_NARRATE_RUN,
        "status_snapshot": CONTROLLER_NARRATE_RUN,
        "run_status": CONTROLLER_NARRATE_RUN,
        "read": CONTROLLER_INSPECT,
        "read_only": CONTROLLER_INSPECT,
        "readonly": CONTROLLER_INSPECT,
        "executor_read_only": CONTROLLER_INSPECT,
        "inspect_workspace": CONTROLLER_INSPECT,
        "act": CONTROLLER_EXECUTE,
        "run": CONTROLLER_EXECUTE,
        "executor": CONTROLLER_EXECUTE,
        "executor_write": CONTROLLER_EXECUTE,
        "write": CONTROLLER_EXECUTE,
        "workspace_write": CONTROLLER_EXECUTE,
        "planning": CONTROLLER_PLAN,
        "plan_mode": CONTROLLER_PLAN,
        "clarification": CONTROLLER_CLARIFY,
        "ask": CONTROLLER_CLARIFY,
    }
    token = aliases.get(token, token)
    return token if token in CONTROLLER_LANES else default


def _normalize_permission_scope(value: Any, *, default: str = PERMISSION_NONE) -> str:
    token = _normalize_policy_token(value)
    aliases = {
        "no_tools": PERMISSION_NONE,
        "none": PERMISSION_NONE,
        "readonly": PERMISSION_READ_ONLY,
        "read": PERMISSION_READ_ONLY,
        "read_only": PERMISSION_READ_ONLY,
        "shell_only": PERMISSION_TRANSIENT_EXECUTE,
        "validation": PERMISSION_TRANSIENT_EXECUTE,
        "transient": PERMISSION_TRANSIENT_EXECUTE,
        "execute": PERMISSION_TRANSIENT_EXECUTE,
        "run": PERMISSION_TRANSIENT_EXECUTE,
        "write": PERMISSION_WORKSPACE_WRITE,
        "workspace_mutation": PERMISSION_WORKSPACE_WRITE,
        "mutation": PERMISSION_WORKSPACE_WRITE,
        "workspace_write": PERMISSION_WORKSPACE_WRITE,
        "external": PERMISSION_EXTERNAL_WRITE,
        "external_mutation": PERMISSION_EXTERNAL_WRITE,
    }
    token = aliases.get(token, token)
    return token if token in PERMISSION_SCOPES else default


def _normalize_capability_packs(value: Any) -> tuple[str, ...]:
    aliases = {
        "browser": CAPABILITY_PACK_BROWSER_CONTROL,
        "browser_control": CAPABILITY_PACK_BROWSER_CONTROL,
        "browser_navigation": CAPABILITY_PACK_BROWSER_CONTROL,
        "web_ui": CAPABILITY_PACK_BROWSER_CONTROL,
        "desktop": CAPABILITY_PACK_DESKTOP_CONTROL,
        "desktop_control": CAPABILITY_PACK_DESKTOP_CONTROL,
        "desktop_ui": CAPABILITY_PACK_DESKTOP_CONTROL,
        "computer": CAPABILITY_PACK_COMPUTER_CONTROL,
        "computer_control": CAPABILITY_PACK_COMPUTER_CONTROL,
        "computer_use": CAPABILITY_PACK_COMPUTER_CONTROL,
        "ui_control": CAPABILITY_PACK_COMPUTER_CONTROL,
    }
    raw_items: list[Any] = []
    if isinstance(value, Mapping):
        for key, enabled in value.items():
            if enabled:
                raw_items.append(key)
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_items.extend(value)
    elif value not in (None, ""):
        raw_items.append(value)

    packs: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        token = "_".join(str(raw or "").strip().lower().replace("-", "_").split())
        normalized = aliases.get(token, token)
        if normalized in CAPABILITY_PACKS and normalized not in seen:
            seen.add(normalized)
            packs.append(normalized)
    return tuple(packs)


def _normalize_evidence_freshness(value: Any, *, default: str = EVIDENCE_KNOWN_STATE) -> str:
    token = _normalize_policy_token(value)
    aliases = {
        "known": EVIDENCE_KNOWN_STATE,
        "cached": EVIDENCE_VALIDATED_CACHE,
        "fingerprinted_cache": EVIDENCE_VALIDATED_CACHE,
        "fresh_read": EVIDENCE_FRESH,
        "fresh_validation": EVIDENCE_FRESH,
    }
    token = aliases.get(token, token)
    return token if token in EVIDENCE_FRESHNESS_VALUES else default


def _normalize_evidence_scope(value: Any, *, default: str = EVIDENCE_SCOPE_NONE) -> str:
    token = _normalize_policy_token(value)
    aliases = {
        "single": EVIDENCE_SCOPE_TARGETED,
        "narrow": EVIDENCE_SCOPE_TARGETED,
        "focused": EVIDENCE_SCOPE_TARGETED,
        "wide": EVIDENCE_SCOPE_BROAD,
        "deep": EVIDENCE_SCOPE_BROAD,
        "full": EVIDENCE_SCOPE_BROAD,
    }
    token = aliases.get(token, token)
    return token if token in EVIDENCE_SCOPE_VALUES else default


def _normalize_evidence_sources(value: Any, *, default: Sequence[str]) -> tuple[str, ...]:
    raw_items: list[Any]
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.replace(",", " ").split()]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        raw_items = list(value)
    else:
        raw_items = list(default)
    aliases = {
        "chat": EVIDENCE_SOURCE_CONVERSATION,
        "conversation": EVIDENCE_SOURCE_CONVERSATION,
        "transcript": EVIDENCE_SOURCE_CONVERSATION,
        "state": EVIDENCE_SOURCE_RUN_STATE,
        "run": EVIDENCE_SOURCE_RUN_STATE,
        "run_state": EVIDENCE_SOURCE_RUN_STATE,
        "files": EVIDENCE_SOURCE_WORKSPACE,
        "file": EVIDENCE_SOURCE_WORKSPACE,
        "workspace": EVIDENCE_SOURCE_WORKSPACE,
        "checks": EVIDENCE_SOURCE_VALIDATION,
        "validation": EVIDENCE_SOURCE_VALIDATION,
        "web": EVIDENCE_SOURCE_EXTERNAL,
        "network": EVIDENCE_SOURCE_EXTERNAL,
        "external": EVIDENCE_SOURCE_EXTERNAL,
    }
    result: list[str] = []
    for item in raw_items:
        token = aliases.get(_normalize_policy_token(item), _normalize_policy_token(item))
        if token in EVIDENCE_SOURCES and token not in result:
            result.append(token)
    if not result:
        result = [item for item in default if item in EVIDENCE_SOURCES]
    return tuple(result)


def _normalize_phase_shape(value: Any, *, default: str = PHASE_NONE) -> str:
    token = _normalize_policy_token(value)
    aliases = {
        "direct": PHASE_NONE,
        "status": PHASE_SNAPSHOT,
        "status_snapshot": PHASE_SNAPSHOT,
        "cheap_probe": PHASE_PROBE,
        "single_probe": PHASE_PROBE,
        "single_pass": PHASE_ONE_PASS,
        "validate_once": PHASE_VALIDATION_GATE,
        "validation": PHASE_VALIDATION_GATE,
        "loop": PHASE_REPAIR_LOOP,
        "loop_until_condition": PHASE_REPAIR_LOOP,
        "repair": PHASE_REPAIR_LOOP,
        "watch": PHASE_MONITOR,
        "observe": PHASE_MONITOR,
    }
    token = aliases.get(token, token)
    return token if token in PHASE_SHAPES else default


def _normalize_latency_class(value: Any, *, default: str = LATENCY_NORMAL) -> str:
    token = _normalize_policy_token(value)
    aliases = {
        "immediate": LATENCY_INSTANT,
        "quick": LATENCY_FAST,
        "low_latency": LATENCY_FAST,
        "balanced": LATENCY_NORMAL,
        "standard": LATENCY_NORMAL,
        "normal": LATENCY_NORMAL,
        "thorough": LATENCY_DEEP,
        "deeper": LATENCY_DEEP,
        "async": LATENCY_BACKGROUND,
        "long_running": LATENCY_BACKGROUND,
    }
    token = aliases.get(token, token)
    return token if token in LATENCY_CLASSES else default


def _legacy_latency_from_class(value: str) -> str:
    if value in {LATENCY_INSTANT, LATENCY_FAST}:
        return LATENCY_FAST
    if value == LATENCY_DEEP or value == LATENCY_BACKGROUND:
        return LATENCY_DEEP
    return LATENCY_BALANCED


def _normalize_surface_progress_detail(value: Any, *, default: str = PROGRESS_COMPACT) -> str:
    token = _normalize_policy_token(value)
    aliases = {
        "off": PROGRESS_NONE,
        "false": PROGRESS_NONE,
        "minimal": PROGRESS_QUIET,
        "low": PROGRESS_QUIET,
        "normal": PROGRESS_COMPACT,
        "standard": PROGRESS_COMPACT,
        "high": PROGRESS_VERBOSE,
        "full": PROGRESS_VERBOSE,
        "trace": PROGRESS_DEBUG,
    }
    token = aliases.get(token, token)
    if token in {PROGRESS_NONE, PROGRESS_QUIET, PROGRESS_COMPACT, PROGRESS_VERBOSE, PROGRESS_DEBUG}:
        return token
    return default


def _legacy_progress_from_surface(value: str) -> str:
    if value in {PROGRESS_NONE, PROGRESS_QUIET}:
        return PROGRESS_QUIET
    if value in {PROGRESS_VERBOSE, PROGRESS_DEBUG}:
        return PROGRESS_VERBOSE
    return PROGRESS_COMPACT


def _normalize_heartbeat_seconds(value: Any, *, default: int = 10) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(120, seconds))


def _normalize_admission_relation(value: Any, *, default: str = ADMISSION_AUTO) -> str:
    token = _normalize_policy_token(value)
    aliases = {
        "status": ADMISSION_STATUS_ONLY,
        "status_only": ADMISSION_STATUS_ONLY,
        "append": ADMISSION_APPEND_ACTIVE,
        "append_to_active": ADMISSION_APPEND_ACTIVE,
        "new": ADMISSION_NEW_TASK,
        "separate": ADMISSION_NEW_TASK,
        "new_task": ADMISSION_NEW_TASK,
    }
    token = aliases.get(token, token)
    return token if token in ADMISSION_TASK_RELATIONS else default


def _controller_from_legacy_lane(lane: Any) -> str:
    lane_text = _lane_from_model_route_text(lane)
    if lane_text == NARRATOR_READ_ONLY:
        return CONTROLLER_NARRATE_RUN
    if lane_text == EXECUTOR_READ_ONLY:
        return CONTROLLER_INSPECT
    if lane_text == EXECUTOR_WRITE:
        return CONTROLLER_EXECUTE
    if lane_text == PLAN_MODE:
        return CONTROLLER_PLAN
    if lane_text == CLARIFICATION:
        return CONTROLLER_CLARIFY
    return CONTROLLER_RESPOND


def _legacy_lane_from_controller(controller_lane: Any) -> str:
    controller = _normalize_controller_lane(controller_lane)
    if controller in {CONTROLLER_RESPOND, CONTROLLER_NARRATE_RUN}:
        return NARRATOR_READ_ONLY
    if controller == CONTROLLER_INSPECT:
        return EXECUTOR_READ_ONLY
    if controller == CONTROLLER_EXECUTE:
        return EXECUTOR_WRITE
    if controller == CONTROLLER_PLAN:
        return PLAN_MODE
    if controller == CONTROLLER_CLARIFY:
        return CLARIFICATION
    return ""


def _default_permission_for_controller(controller_lane: str) -> str:
    if controller_lane == CONTROLLER_INSPECT:
        return PERMISSION_READ_ONLY
    if controller_lane == CONTROLLER_EXECUTE:
        return PERMISSION_WORKSPACE_WRITE
    return PERMISSION_NONE


def _default_phase_for_controller(controller_lane: str, autonomy: str) -> str:
    if controller_lane == CONTROLLER_NARRATE_RUN:
        return PHASE_SNAPSHOT
    if controller_lane == CONTROLLER_INSPECT:
        return PHASE_ONE_PASS
    if controller_lane == CONTROLLER_EXECUTE:
        return PHASE_REPAIR_LOOP if autonomy == EXECUTION_CONTINUOUS else PHASE_ONE_PASS
    if controller_lane == CONTROLLER_PLAN:
        return PHASE_ONE_PASS
    return PHASE_NONE


def _default_evidence_for_controller(
    controller_lane: str,
    phase_shape: str,
) -> tuple[str, str, tuple[str, ...]]:
    if controller_lane == CONTROLLER_NARRATE_RUN:
        return EVIDENCE_KNOWN_STATE, EVIDENCE_SCOPE_TARGETED, (
            EVIDENCE_SOURCE_CONVERSATION,
            EVIDENCE_SOURCE_RUN_STATE,
        )
    if controller_lane == CONTROLLER_INSPECT:
        return EVIDENCE_FRESH, EVIDENCE_SCOPE_TARGETED, (EVIDENCE_SOURCE_WORKSPACE,)
    if controller_lane == CONTROLLER_EXECUTE:
        sources = [EVIDENCE_SOURCE_WORKSPACE]
        if phase_shape in {PHASE_PROBE, PHASE_VALIDATION_GATE, PHASE_REPAIR_LOOP}:
            sources.append(EVIDENCE_SOURCE_VALIDATION)
        return EVIDENCE_FRESH, EVIDENCE_SCOPE_TARGETED, tuple(sources)
    if controller_lane == CONTROLLER_PLAN:
        return EVIDENCE_KNOWN_STATE, EVIDENCE_SCOPE_TARGETED, (EVIDENCE_SOURCE_CONVERSATION,)
    return EVIDENCE_KNOWN_STATE, EVIDENCE_SCOPE_NONE, (EVIDENCE_SOURCE_CONVERSATION,)


def _stop_condition_from_phase_shape(phase_shape: str, autonomy: str) -> str:
    if phase_shape in {PHASE_VALIDATION_GATE, PHASE_REPAIR_LOOP}:
        return STOP_VALIDATION_PASSES
    if autonomy == EXECUTION_CONTINUOUS:
        return STOP_VALIDATION_PASSES
    return STOP_OBJECTIVE_SATISFIED


def normalize_agent_communication_policy(
    payload: Mapping[str, Any] | AgentCommunicationPolicy | None = None,
    *,
    lane: str = "",
    executor_effort: str = "",
) -> AgentCommunicationPolicy:
    """Normalize model-authored communication-policy fields.

    Defaults are derived only from the structured route lane/effort, not from
    natural-language keyword matching.
    """

    policy_payload: Mapping[str, Any] = {}
    surface_payload: Mapping[str, Any] = {}
    if isinstance(payload, AgentCommunicationPolicy):
        return payload
    if isinstance(payload, Mapping):
        surface_payload = _surface_policy_payload(payload)
        nested = payload.get("communication_policy") or payload.get("response_policy")
        if isinstance(nested, Mapping):
            policy_payload = nested
        else:
            policy_payload = payload
    surface_response = _mapping_or_empty(surface_payload.get("response_policy"))
    surface_latency = _mapping_or_empty(surface_payload.get("latency_policy"))
    surface_progress = _mapping_or_empty(surface_payload.get("progress_policy"))
    lane_text = _lane_from_model_route_text(lane)
    effort = _normalize_policy_token(executor_effort)
    default_budget = ANSWER_BUDGET_NORMAL
    default_latency = LATENCY_BALANCED
    default_progress = PROGRESS_COMPACT
    default_style = INTERACTION_ACT_THEN_REPORT
    if lane_text in {NARRATOR_READ_ONLY, EXECUTOR_READ_ONLY}:
        default_style = INTERACTION_ANSWER_ONLY
        default_latency = LATENCY_FAST if effort == "simple" else LATENCY_BALANCED
    if lane_text == PLAN_MODE:
        default_style = INTERACTION_REVIEW
        default_budget = ANSWER_BUDGET_DETAILED
        default_progress = PROGRESS_VERBOSE
        default_latency = LATENCY_DEEP
    if effort == "simple" and lane_text != PLAN_MODE:
        default_budget = ANSWER_BUDGET_BRIEF
        default_latency = LATENCY_FAST
        default_progress = PROGRESS_QUIET

    raw_budget = _first_mapping_value(policy_payload, "answer_budget") or surface_response.get("answer_budget")
    raw_latency = (
        _first_mapping_value(policy_payload, "latency_preference")
        or surface_latency.get("class")
        or surface_latency.get("latency_class")
    )
    raw_progress = _first_mapping_value(policy_payload, "progress_detail") or surface_progress.get("detail")
    raw_needs_progress = (
        policy_payload.get("needs_progress_detail") if isinstance(policy_payload, Mapping) else None
    )
    raw_style = _first_mapping_value(policy_payload, "interaction_style") or surface_response.get("interaction_style")
    budget = _normalize_answer_budget(raw_budget, default=default_budget)
    latency = _legacy_latency_from_class(_normalize_latency_class(raw_latency)) if raw_latency else default_latency
    progress = _normalize_progress_detail(
        _legacy_progress_from_surface(_normalize_surface_progress_detail(raw_progress)) if raw_progress else raw_progress,
        needs_progress_detail=raw_needs_progress,
        default=default_progress,
    )
    style = _normalize_interaction_style(raw_style, default=default_style)
    if _normalize_policy_token(raw_budget) == INTERACTION_AUTONOMOUS_PROGRESS and not raw_style:
        style = INTERACTION_AUTONOMOUS_PROGRESS
    return AgentCommunicationPolicy(
        answer_budget=budget,
        latency_preference=latency,
        progress_detail=progress,
        interaction_style=style,
    )


def _normalize_execution_autonomy_mode(value: Any, *, default: str = EXECUTION_GUIDED) -> str:
    token = _normalize_policy_token(value)
    aliases = {
        "auto": EXECUTION_CONTINUOUS,
        "autonomous": EXECUTION_CONTINUOUS,
        "autonomous_loop": EXECUTION_CONTINUOUS,
        "loop": EXECUTION_CONTINUOUS,
        "loop_until_condition": EXECUTION_CONTINUOUS,
        "until_done": EXECUTION_CONTINUOUS,
        "until_fixed": EXECUTION_CONTINUOUS,
        "background": EXECUTION_CONTINUOUS,
        "interactive": EXECUTION_GUIDED,
        "normal": EXECUTION_GUIDED,
        "default": EXECUTION_GUIDED,
        "ask": EXECUTION_MANUAL,
        "confirm": EXECUTION_MANUAL,
    }
    token = aliases.get(token, token)
    return token if token in EXECUTION_AUTONOMY_MODES else default


def _normalize_execution_stop_condition(
    value: Any,
    *,
    default: str = STOP_OBJECTIVE_SATISFIED,
) -> str:
    token = _normalize_policy_token(value)
    aliases = {
        "done": STOP_OBJECTIVE_SATISFIED,
        "objective": STOP_OBJECTIVE_SATISFIED,
        "objective_done": STOP_OBJECTIVE_SATISFIED,
        "complete": STOP_OBJECTIVE_SATISFIED,
        "completed": STOP_OBJECTIVE_SATISFIED,
        "validation": STOP_VALIDATION_PASSES,
        "validation_pass": STOP_VALIDATION_PASSES,
        "validation_passed": STOP_VALIDATION_PASSES,
        "validation_passing": STOP_VALIDATION_PASSES,
        "compile_passes": STOP_VALIDATION_PASSES,
        "tests_pass": STOP_VALIDATION_PASSES,
        "blocker": STOP_PRECISE_BLOCKER,
        "blocked": STOP_PRECISE_BLOCKER,
        "precise_blocker": STOP_PRECISE_BLOCKER,
    }
    token = aliases.get(token, token)
    return token if token in EXECUTION_STOP_CONDITIONS else default


def _bounded_execution_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _normalize_execution_seconds(value: Any, *, default: int | None) -> int | None:
    if value is None or value == "":
        return default
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return default
    if seconds <= 0:
        return None
    return max(30, min(3600, seconds))


def _normalize_latency_seconds(value: Any, *, default: int | None) -> int | None:
    if value is None or value == "":
        return default
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return default
    if seconds <= 0:
        return None
    return max(1, min(3600, seconds))


def _first_mapping_value(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload.get(key)
    return None


def normalize_agent_execution_policy(
    payload: Mapping[str, Any] | AgentExecutionPolicy | None = None,
    *,
    lane: str = "",
    communication_policy: AgentCommunicationPolicy | Mapping[str, Any] | None = None,
) -> AgentExecutionPolicy:
    """Normalize model-authored execution autonomy without free-text routing rules."""

    if isinstance(payload, AgentExecutionPolicy):
        return payload
    policy_payload: Mapping[str, Any] = {}
    surface_payload: Mapping[str, Any] = {}
    if isinstance(payload, Mapping):
        surface_payload = _surface_policy_payload(payload)
        nested = payload.get("execution_policy") or payload.get("autonomy_policy") or payload.get("loop_policy")
        if isinstance(nested, Mapping):
            policy_payload = nested
        else:
            policy_payload = payload
    surface_latency = _mapping_or_empty(surface_payload.get("latency_policy"))
    surface_phase = _normalize_phase_shape(surface_payload.get("phase_shape"), default="")
    surface_autonomy = surface_payload.get("autonomy")
    comm = normalize_agent_communication_policy(communication_policy) if communication_policy is not None else None
    lane_text = _lane_from_model_route_text(lane)
    default_mode = EXECUTION_GUIDED
    if comm is not None and comm.interaction_style == INTERACTION_AUTONOMOUS_PROGRESS:
        default_mode = EXECUTION_CONTINUOUS
    if lane_text != EXECUTOR_WRITE:
        default_mode = EXECUTION_MANUAL if lane_text == PLAN_MODE else EXECUTION_GUIDED

    raw_mode = _first_mapping_value(policy_payload, "autonomy_mode", "mode", "execution_mode") or surface_autonomy
    mode = _normalize_execution_autonomy_mode(raw_mode, default=default_mode)
    default_stop = _stop_condition_from_phase_shape(surface_phase, mode) if surface_phase else (
        STOP_VALIDATION_PASSES if mode == EXECUTION_CONTINUOUS else STOP_OBJECTIVE_SATISFIED
    )
    stop = _normalize_execution_stop_condition(
        _first_mapping_value(policy_payload, "stop_condition", "until"),
        default=default_stop,
    )
    default_seconds = 900 if mode == EXECUTION_CONTINUOUS else None
    max_work_seconds = _normalize_execution_seconds(
        _first_mapping_value(policy_payload, "max_work_seconds", "max_seconds", "time_budget_seconds")
        or surface_latency.get("max_work_seconds"),
        default=default_seconds,
    )
    default_fix_rounds = 4 if mode == EXECUTION_CONTINUOUS or surface_phase == PHASE_REPAIR_LOOP else 1
    default_validation_cycles = 5 if mode == EXECUTION_CONTINUOUS or surface_phase == PHASE_REPAIR_LOOP else 2
    max_auto_fix_rounds = _bounded_execution_int(
        _first_mapping_value(policy_payload, "max_auto_fix_rounds", "max_repair_rounds", "max_cycles"),
        default=default_fix_rounds,
        minimum=0,
        maximum=8,
    )
    max_validation_cycles = _bounded_execution_int(
        _first_mapping_value(policy_payload, "max_validation_cycles", "validation_cycles", "max_checks"),
        default=default_validation_cycles,
        minimum=1,
        maximum=12,
    )
    raw_allow_repair = policy_payload.get("allow_repair_cycles")
    allow_repair_cycles = True
    if isinstance(raw_allow_repair, bool):
        allow_repair_cycles = raw_allow_repair
    elif _normalize_policy_token(raw_allow_repair) in {"false", "no", "off", "0"}:
        allow_repair_cycles = False
    elif surface_phase in {PHASE_VALIDATION_GATE, PHASE_PROBE, PHASE_ONE_PASS, PHASE_SNAPSHOT, PHASE_NONE}:
        allow_repair_cycles = False
    elif surface_phase == PHASE_REPAIR_LOOP:
        allow_repair_cycles = True
    if not allow_repair_cycles:
        max_auto_fix_rounds = 0
    return AgentExecutionPolicy(
        autonomy_mode=mode,
        stop_condition=stop,
        max_work_seconds=max_work_seconds,
        max_auto_fix_rounds=max_auto_fix_rounds,
        max_validation_cycles=max_validation_cycles,
        allow_repair_cycles=allow_repair_cycles,
    )


def normalize_agent_surface_policy(
    payload: Mapping[str, Any] | AgentSurfacePolicy | None = None,
    *,
    lane: str = "",
    executor_effort: str = "",
    communication_policy: AgentCommunicationPolicy | Mapping[str, Any] | None = None,
    execution_policy: AgentExecutionPolicy | Mapping[str, Any] | None = None,
) -> AgentSurfacePolicy:
    """Normalize the canonical orthogonal surface policy.

    This accepts both the new ``surface_policy`` shape and the legacy router
    fields. No natural-language keyword matching happens here; defaults are
    derived from already-structured lane/policy fields.
    """

    if isinstance(payload, AgentSurfacePolicy):
        return payload
    policy_payload = _surface_policy_payload(payload)
    legacy_lane = _lane_from_model_route_text(lane)
    if not legacy_lane and isinstance(payload, Mapping):
        legacy_lane = _lane_from_model_route_text(payload.get("lane") or payload.get("route"))
    raw_controller = (
        policy_payload.get("controller_lane")
        or policy_payload.get("controller")
        or policy_payload.get("lane_owner")
    )
    default_controller = _controller_from_legacy_lane(legacy_lane)
    controller = _normalize_controller_lane(raw_controller, default=default_controller)
    legacy_lane = legacy_lane or _legacy_lane_from_controller(controller)

    comm = normalize_agent_communication_policy(
        communication_policy if communication_policy is not None else payload,
        lane=legacy_lane,
        executor_effort=executor_effort,
    )
    execution = normalize_agent_execution_policy(
        execution_policy if execution_policy is not None else payload,
        lane=legacy_lane,
        communication_policy=comm,
    )

    raw_autonomy = policy_payload.get("autonomy")
    autonomy = _normalize_execution_autonomy_mode(raw_autonomy, default=execution.autonomy_mode)
    raw_phase = policy_payload.get("phase_shape")
    phase_default = _default_phase_for_controller(controller, autonomy)
    phase = _normalize_phase_shape(raw_phase, default=phase_default)
    default_freshness, default_scope, default_sources = _default_evidence_for_controller(controller, phase)

    evidence_payload = _mapping_or_empty(policy_payload.get("evidence_policy"))
    evidence = AgentEvidencePolicy(
        freshness=_normalize_evidence_freshness(evidence_payload.get("freshness"), default=default_freshness),
        scope=_normalize_evidence_scope(evidence_payload.get("scope"), default=default_scope),
        sources=_normalize_evidence_sources(evidence_payload.get("sources"), default=default_sources),
    )

    permission_default = _default_permission_for_controller(controller)
    permission = _normalize_permission_scope(policy_payload.get("permission_scope"), default=permission_default)

    latency_payload = _mapping_or_empty(policy_payload.get("latency_policy"))
    latency_class = _normalize_latency_class(
        latency_payload.get("class") or latency_payload.get("latency_class"),
        default=_normalize_latency_class(comm.latency_preference),
    )
    latency = AgentLatencyPolicy(
        latency_class=latency_class,
        max_work_seconds=_normalize_latency_seconds(
            latency_payload.get("max_work_seconds"),
            default=execution.max_work_seconds,
        ),
    )

    response_payload = _mapping_or_empty(policy_payload.get("response_policy"))
    response = AgentResponsePolicy(
        answer_budget=_normalize_answer_budget(response_payload.get("answer_budget"), default=comm.answer_budget),
        interaction_style=_normalize_interaction_style(
            response_payload.get("interaction_style"),
            default=comm.interaction_style,
        ),
    )

    progress_payload = _mapping_or_empty(policy_payload.get("progress_policy"))
    progress = AgentProgressPolicy(
        detail=_normalize_surface_progress_detail(progress_payload.get("detail"), default=comm.progress_detail),
        heartbeat_seconds=_normalize_heartbeat_seconds(progress_payload.get("heartbeat_seconds"), default=10),
    )

    admission_payload = _mapping_or_empty(policy_payload.get("admission_policy"))
    admission_default = ADMISSION_STATUS_ONLY if controller == CONTROLLER_NARRATE_RUN else ADMISSION_AUTO
    admission = AgentAdmissionPolicy(
        task_relation=_normalize_admission_relation(
            admission_payload.get("task_relation") or policy_payload.get("task_relation"),
            default=admission_default,
        )
    )
    capability_packs = _normalize_capability_packs(
        policy_payload.get("capability_packs")
        or policy_payload.get("tool_packs")
        or policy_payload.get("capabilities")
    )

    return AgentSurfacePolicy(
        controller_lane=controller,
        permission_scope=permission,
        evidence_policy=evidence,
        phase_shape=phase,
        autonomy=autonomy,
        latency_policy=latency,
        response_policy=response,
        progress_policy=progress,
        admission_policy=admission,
        capability_packs=capability_packs,
    )


def _lane_from_model_route_text(value: Any) -> str:
    lane_text = _normalize_model_route_lane(value)
    if lane_text in {"narrator", "narrator_read_only", "progress", "status"}:
        return NARRATOR_READ_ONLY
    if lane_text in {"executor_read_only", "read_only", "readonly"}:
        return EXECUTOR_READ_ONLY
    if lane_text in {"executor_write", "write", "workspace_write"}:
        return EXECUTOR_WRITE
    if lane_text in {"plan", "plan_mode", "planning", "planning_mode"}:
        return PLAN_MODE
    if lane_text == CLARIFICATION:
        return CLARIFICATION
    return ""


def _model_route_payload_issue(payload: Mapping[str, Any] | None) -> str:
    if payload is None:
        return "no JSON object was found"
    lane_value = payload.get("lane") or payload.get("route")
    surface_payload = _surface_policy_payload(payload)
    controller_value = surface_payload.get("controller_lane") or surface_payload.get("controller")
    if not str(lane_value or "").strip() and not str(controller_value or "").strip():
        return "the route object is missing controller_lane"
    if str(lane_value or "").strip() and not _lane_from_model_route_text(lane_value):
        return f"unsupported lane: {lane_value}"
    if str(controller_value or "").strip() and _normalize_controller_lane(controller_value, default="") == "":
        return f"unsupported controller_lane: {controller_value}"
    return ""


def _legacy_lane_from_route_payload(payload: Mapping[str, Any]) -> str:
    lane = _lane_from_model_route_text(payload.get("lane") or payload.get("route"))
    if lane:
        return lane
    surface_payload = _surface_policy_payload(payload)
    return _legacy_lane_from_controller(surface_payload.get("controller_lane") or surface_payload.get("controller"))


def _route_payload_has_canonical_policy(payload: Mapping[str, Any] | None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    surface_payload = _surface_policy_payload(payload)
    return any(
        key in surface_payload
        for key in (
            "controller_lane",
            "controller",
            "permission_scope",
            "evidence_policy",
            "phase_shape",
            "autonomy",
            "latency_policy",
            "response_policy",
            "progress_policy",
            "admission_policy",
        )
    )


def _route_payload_needs_no_execution_consistency_review(payload: Mapping[str, Any] | None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    legacy_lane = _legacy_lane_from_route_payload(payload)
    if legacy_lane not in {NARRATOR_READ_ONLY, EXECUTOR_READ_ONLY}:
        return False
    effort = str(payload.get("complexity") or payload.get("executor_effort") or "complex").strip().lower()
    if effort not in {"simple", "complex"}:
        effort = "complex"
    communication = normalize_agent_communication_policy(
        payload,
        lane=legacy_lane,
        executor_effort=effort,
    )
    surface_policy = normalize_agent_surface_policy(
        payload,
        lane=legacy_lane,
        executor_effort=effort,
        communication_policy=communication,
    )
    if (
        surface_policy.controller_lane == CONTROLLER_INSPECT
        and surface_policy.permission_scope in {PERMISSION_TRANSIENT_EXECUTE, PERMISSION_EXTERNAL_WRITE}
        and surface_policy.phase_shape in {PHASE_PROBE, PHASE_ONE_PASS}
        and (
            CAPABILITY_PACK_BROWSER_CONTROL in surface_policy.capability_packs
            or CAPABILITY_PACK_DESKTOP_CONTROL in surface_policy.capability_packs
            or CAPABILITY_PACK_COMPUTER_CONTROL in surface_policy.capability_packs
        )
    ):
        return False
    return (
        communication.interaction_style in {INTERACTION_ACT_THEN_REPORT, INTERACTION_AUTONOMOUS_PROGRESS}
        or surface_policy.phase_shape in {PHASE_PROBE, PHASE_VALIDATION_GATE, PHASE_REPAIR_LOOP}
    )


def _decision_from_model_route_payload(payload: Mapping[str, Any]) -> AgentTurnIntentDecision:
    surface_policy = normalize_agent_surface_policy(payload)
    legacy_lane = _legacy_lane_from_route_payload(payload)
    if _route_payload_has_canonical_policy(payload):
        lane = _legacy_lane_from_controller(surface_policy.controller_lane) or legacy_lane
    else:
        lane = legacy_lane or _legacy_lane_from_controller(surface_policy.controller_lane)
    effort = str(payload.get("complexity") or payload.get("executor_effort") or "complex").strip().lower()
    if effort not in {"simple", "complex"}:
        if surface_policy.phase_shape in {PHASE_SNAPSHOT, PHASE_PROBE, PHASE_VALIDATION_GATE}:
            effort = "simple"
        else:
            effort = "complex"
    rationale = _clip(payload.get("rationale") or payload.get("reason") or "model-assisted route", limit=220)
    confidence = _coerce_confidence(payload.get("confidence"))
    clarification = _clip(payload.get("clarification") or "", limit=1200)
    communication_policy = normalize_agent_communication_policy(
        payload,
        lane=lane,
        executor_effort=effort,
    )
    execution_policy = normalize_agent_execution_policy(
        payload,
        lane=lane,
        communication_policy=communication_policy,
    )
    surface_policy = normalize_agent_surface_policy(
        payload,
        lane=lane,
        executor_effort=effort,
        communication_policy=communication_policy,
        execution_policy=execution_policy,
    )
    if lane == NARRATOR_READ_ONLY:
        return AgentTurnIntentDecision(
            lane=NARRATOR_READ_ONLY,
            confidence=confidence or 0.7,
            rationale=rationale,
            communication_policy=communication_policy,
            execution_policy=execution_policy,
            surface_policy=surface_policy,
        )
    if lane == EXECUTOR_READ_ONLY:
        return AgentTurnIntentDecision(
            lane=EXECUTOR_READ_ONLY,
            confidence=confidence or 0.7,
            rationale=rationale,
            executor_effort=effort,
            communication_policy=communication_policy,
            execution_policy=execution_policy,
            surface_policy=surface_policy,
        )
    if lane == EXECUTOR_WRITE:
        return AgentTurnIntentDecision(
            lane=EXECUTOR_WRITE,
            confidence=confidence or 0.7,
            rationale=rationale,
            executor_effort=effort,
            communication_policy=communication_policy,
            execution_policy=execution_policy,
            surface_policy=surface_policy,
        )
    if lane == PLAN_MODE:
        return AgentTurnIntentDecision(
            lane=PLAN_MODE,
            confidence=confidence or 0.7,
            rationale=rationale,
            executor_effort="",
            communication_policy=communication_policy,
            execution_policy=execution_policy,
            surface_policy=surface_policy,
        )
    return AgentTurnIntentDecision(
        lane=CLARIFICATION,
        confidence=confidence,
        rationale=rationale or "model route requested clarification",
        clarification=clarification
        or "Should this be progress/status, read-only workspace inspection, or workspace-changing work?",
        communication_policy=communication_policy,
        execution_policy=execution_policy,
        surface_policy=surface_policy,
    )


def _router_transcript_lines(transcript_tail: Sequence[Any]) -> list[str]:
    lines: list[str] = []
    for item in transcript_tail[-6:]:
        if isinstance(item, Mapping):
            role = str(item.get("role") or "").strip()
            text = str(item.get("text") or "").strip()
        else:
            role = str(getattr(item, "role", "") or "").strip()
            text = str(getattr(item, "text", "") or "").strip()
        if not text:
            continue
        lines.append(f"{role or 'message'}: {_clip(text, limit=500)}")
    return lines


def _agent_turn_router_messages(
    *,
    text: str,
    transcript_tail: Sequence[Any] = (),
    selected_skills: Sequence[str] = (),
    surface: str = "super-tui",
) -> list[dict[str, str]]:
    transcript = "\n".join(_router_transcript_lines(transcript_tail)) or "(none)"
    skills = ", ".join(str(skill).strip() for skill in selected_skills if str(skill).strip()) or "(none)"
    return [
        {
            "role": "system",
            "content": (
                "Route the user's Diane turn by meaning and recent context, not by keywords. "
                "Return exactly one JSON object with keys controller_lane, permission_scope, evidence_policy, phase_shape, autonomy, latency_policy, response_policy, progress_policy, admission_policy, capability_packs, confidence, rationale, clarification, lane, complexity, communication_policy, execution_policy. "
                "The canonical policy fields are orthogonal and complete: controller_lane chooses the owner; permission_scope chooses allowed side effects; evidence_policy chooses freshness/scope/sources; phase_shape chooses workflow shape; autonomy chooses continuation authority; latency_policy chooses time budget; response_policy chooses final answer shape; progress_policy chooses live narration; admission_policy chooses relation to active work; capability_packs chooses optional UI-control tool families. "
                "The compatibility lane and rationale must agree with the canonical policy; if they conflict, fix the policy and derive the lane from it. "
                "controller_lane is respond, narrate_run, inspect, execute, plan, or clarify. "
                "permission_scope is none, read_only, transient_execute, workspace_write, or external_write. "
                "evidence_policy.freshness is known_state, validated_cache, or fresh; evidence_policy.scope is none, targeted, or broad; evidence_policy.sources is any of conversation, run_state, workspace, validation, external. "
                "phase_shape is none, snapshot, probe, one_pass, validation_gate, repair_loop, or monitor. "
                "autonomy is manual, guided, or continuous. "
                "latency_policy.class is instant, fast, normal, deep, or background, with max_work_seconds as a numeric cap when useful. "
                "response_policy.answer_budget is brief, normal, or detailed; response_policy.interaction_style is answer_only, findings, act_then_report, or review. "
                "progress_policy.detail is none, quiet, compact, verbose, or debug; progress_policy.heartbeat_seconds is a numeric cadence. "
                "admission_policy.task_relation is auto, status_only, append_active, or new_task. "
                "capability_packs is an array containing any of browser_control, desktop_control, or computer_control; leave it empty by default. "
                "Use browser_control only when the current turn needs browser UI state, DOM/HTML evidence, screenshots, direct element interaction, or downloads. "
                "Use desktop_control only when the current turn needs local desktop screenshot, mouse, keyboard, app focus, or window interaction. "
                "Use computer_control only when both browser and desktop control are needed or the user explicitly asks for general computer-use control. "
                "For capability questions, if the user asks whether Diane has browser, desktop, or computer-control capabilities, answer from this capability-pack contract; do not claim those capabilities are unavailable merely because the current turn has not activated a pack. "
                "Also include compatibility projections: lane is narrator_read_only for current/recent run status only, executor_read_only for workspace inspection with no file changes, "
                "executor_write for edits/execution/generated artifacts/validation commands, plan_mode for refinement/planning before execution, "
                "or clarification when choosing would be unsafe; complexity is simple or complex. "
                "For ordinary workspace-changing work, the user's request is authorization to select executor_write; "
                "do not ask for a second confirmation merely because files may change, commands may run, or an analysis will create artifacts. "
                "If the user is asking to refine, compare, or decide on a plan before implementation, use plan_mode instead of starting executor_write. "
                "Do not use plan_mode just because a request is broad or asks for a review; project review, audit, inspection, or code-quality analysis is executor_read_only unless the user explicitly asks to plan/refine/compare before execution. "
                "Executor_write is for requests that are ready to execute or create artifacts now. "
                "For browser/web/news/current external information requests where the user wants an answer and does not ask to create or edit a workspace file, use controller_lane=inspect, permission_scope=transient_execute, evidence_policy freshness=fresh/sources=[external], phase_shape=probe or one_pass, and capability_packs=[browser_control]. "
                "Do not choose workspace_write merely because browser navigation, web fetching, or transient UI state is needed. "
                "Use clarification sparingly. Follow-up questions about progress, outcome, what remains, or the next step should usually be narrator_read_only "
                "when the recent transcript gives enough session context. "
                "For a current truth question that requires fresh validation, such as whether the project builds, tests, compiles, runs, or passes a check right now, use controller_lane=execute, permission_scope=transient_execute, evidence_policy freshness=fresh/sources=[validation], phase_shape=validation_gate, brief answer, and fast latency. "
                "Do not answer those fresh validation questions from a stale narrator snapshot unless the recent transcript already contains fresh validation evidence for the exact question. "
                "Use clarification only when the goal, target, or capability boundary is genuinely missing or unsafe to choose. "
                "complexity is simple only for a bounded single-source read or exact single-file operation; otherwise complex. "
                "Leave clarification empty unless lane is clarification. "
                "communication_policy.answer_budget is brief, normal, or detailed. "
                "communication_policy.latency_preference is fast, balanced, or deep. "
                "communication_policy.progress_detail is quiet, compact, or verbose. "
                "communication_policy.interaction_style is answer_only, findings, act_then_report, review, or autonomous_progress; derive it from response_policy/progress_policy for compatibility. "
                "Use instant or fast with brief/quiet for tiny status, snapshot, or single validation-probe answers; use deep or background for substantial reviews or autonomous loops. "
                "execution_policy.autonomy_mode is manual, guided, or continuous; use continuous for requests to keep repairing/improving until a condition is met. "
                "execution_policy.stop_condition is objective_satisfied, validation_passes, or precise_blocker. "
                "execution_policy.max_work_seconds, execution_policy.max_auto_fix_rounds, and execution_policy.max_validation_cycles are numeric caps; derive them from phase_shape/autonomy/latency_policy and raise them only when the user asks for a longer autonomous loop."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Surface: {surface}\n"
                f"Selected skills: {skills}\n"
                f"Recent visible transcript:\n{transcript}\n\n"
                f"User turn:\n{text}\n\n"
                "Route this turn now."
            ),
        },
    ]


def _agent_turn_router_repair_messages(
    *,
    text: str,
    bad_response: str,
    issue: str,
    transcript_tail: Sequence[Any] = (),
    selected_skills: Sequence[str] = (),
    surface: str = "super-tui",
) -> list[dict[str, str]]:
    transcript = "\n".join(_router_transcript_lines(transcript_tail)) or "(none)"
    skills = ", ".join(str(skill).strip() for skill in selected_skills if str(skill).strip()) or "(none)"
    return [
        {
            "role": "system",
            "content": (
                "Repair a Diane routing response. Decide by meaning and recent context, not by keyword matching. "
                "Return only one JSON object with this exact shape: "
                '{"controller_lane":"respond|narrate_run|inspect|execute|plan|clarify",'
                '"permission_scope":"none|read_only|transient_execute|workspace_write|external_write",'
                '"evidence_policy":{"freshness":"known_state|validated_cache|fresh","scope":"none|targeted|broad","sources":["conversation|run_state|workspace|validation|external"]},'
                '"phase_shape":"none|snapshot|probe|one_pass|validation_gate|repair_loop|monitor",'
                '"autonomy":"manual|guided|continuous",'
                '"latency_policy":{"class":"instant|fast|normal|deep|background","max_work_seconds":30},'
                '"response_policy":{"answer_budget":"brief|normal|detailed","interaction_style":"answer_only|findings|act_then_report|review"},'
                '"progress_policy":{"detail":"none|quiet|compact|verbose|debug","heartbeat_seconds":10},'
                '"admission_policy":{"task_relation":"auto|status_only|append_active|new_task"},'
                '"capability_packs":["browser_control|desktop_control|computer_control"],'
                '"lane":"narrator_read_only|executor_read_only|executor_write|plan_mode|clarification",'
                '"complexity":"simple|complex","confidence":0.0,"rationale":"short reason","clarification":"",'
                '"communication_policy":{"answer_budget":"brief|normal|detailed",'
                '"latency_preference":"fast|balanced|deep","progress_detail":"quiet|compact|verbose",'
                '"interaction_style":"answer_only|findings|act_then_report|review|autonomous_progress"},'
                '"execution_policy":{"autonomy_mode":"manual|guided|continuous","stop_condition":"objective_satisfied|validation_passes|precise_blocker",'
                '"max_work_seconds":900,"max_auto_fix_rounds":1,"max_validation_cycles":2,"allow_repair_cycles":true}}. '
                "The canonical policy axes must stay orthogonal: do not encode answer length into latency, do not encode permissions into lane, do not use progress detail to choose workflow shape, and do not use capability_packs unless optional browser/desktop UI-control tools are actually needed. "
                "The compatibility lane and rationale must agree with the canonical policy; when they conflict, repair the canonical policy first and then derive the lane from it. "
                "Use narrator_read_only only for current/recent run status. Use executor_read_only for inspection or answers that should not change files. "
                "Use executor_write when the request needs edits, generated artifacts, command execution, data processing, or other workspace-changing work. "
                "If the user needs fresh validation truth about whether the project builds, tests, compiles, runs, or passes now, repair to controller_lane=execute, permission_scope=transient_execute, phase_shape=validation_gate, and evidence sources including validation. "
                "Use plan_mode when the user is asking to refine, compare, or decide on a plan before implementation. "
                "Do not use plan_mode just because a request is broad or asks for a review; project review, audit, inspection, or code-quality analysis is executor_read_only unless the user explicitly asks to plan/refine/compare before execution. "
                "For ordinary workspace-changing work, the user's request is authorization to select executor_write; "
                "do not ask for a second confirmation merely because files may change, commands may run, or an analysis will create artifacts. "
                "Executor_write is for requests that are ready to execute or create artifacts now. "
                "For browser/web/news/current external information requests that should answer directly without creating files, repair to controller_lane=inspect, permission_scope=transient_execute, evidence sources including external, phase_shape=probe or one_pass, and capability_packs including browser_control. "
                "Use clarification only when the goal, target, or capability boundary is genuinely missing or unsafe to choose. "
                "The communication_policy is response shaping, not routing; set it from the requested depth, latency, progress visibility, and answer style. "
                "The execution_policy is autonomy and budget shaping, not task-specific logic. "
                "Set capability_packs to [] by default; use browser_control for browser DOM/screenshot/element work, desktop_control for local screenshot/mouse/keyboard/app-focus work, and computer_control only when both are needed. "
                "For capability questions, explain the optional packs instead of denying browser or desktop control."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Surface: {surface}\n"
                f"Selected skills: {skills}\n"
                f"Recent visible transcript:\n{transcript}\n\n"
                f"User turn:\n{text}\n\n"
                f"Previous router output was invalid because: {issue}\n"
                f"Previous router output:\n{_clip(bad_response, limit=900)}\n\n"
                "Repair the route now. Return JSON only."
            ),
        },
    ]


async def route_agent_turn_intent_with_model(
    provider: Any,
    text: str,
    *,
    model: str,
    transcript_tail: Sequence[Any] = (),
    selected_skills: Sequence[str] = (),
    surface: str = "super-tui",
) -> AgentTurnIntentDecision:
    """Route a free-text turn through a model-authored structured decision."""

    response = await provider.complete(
        messages=_agent_turn_router_messages(
            text=text,
            transcript_tail=transcript_tail,
            selected_skills=selected_skills,
            surface=surface,
        ),
        model=model,
        temperature=0.0,
        max_tokens=900,
    )
    raw_text = getattr(response, "text", "")
    payload = _first_json_object(raw_text)
    issue = _model_route_payload_issue(payload)
    if issue:
        repair_response = await provider.complete(
            messages=_agent_turn_router_repair_messages(
                text=text,
                bad_response=raw_text,
                issue=issue,
                transcript_tail=transcript_tail,
                selected_skills=selected_skills,
                surface=surface,
            ),
            model=model,
            temperature=0.0,
            max_tokens=900,
        )
        repair_payload = _first_json_object(getattr(repair_response, "text", ""))
        repair_issue = _model_route_payload_issue(repair_payload)
        if not repair_issue and repair_payload is not None:
            payload = repair_payload
        else:
            return AgentTurnIntentDecision(
                lane=CLARIFICATION,
                confidence=0.0,
                rationale=f"model router did not return a usable structured route: {repair_issue or issue}",
                clarification="I could not route that request safely. Please say whether this should read, write, or report progress.",
            )
    if _legacy_lane_from_route_payload(payload) == CLARIFICATION:
        review_response = await provider.complete(
            messages=_agent_turn_router_repair_messages(
                text=text,
                bad_response=json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
                issue=(
                    "the route selected clarification; verify that clarification is truly necessary "
                    "and not only asking for extra permission to perform requested workspace-changing work"
                ),
                transcript_tail=transcript_tail,
                selected_skills=selected_skills,
                surface=surface,
            ),
            model=model,
            temperature=0.0,
            max_tokens=900,
        )
        review_payload = _first_json_object(getattr(review_response, "text", ""))
        review_issue = _model_route_payload_issue(review_payload)
        if not review_issue and review_payload is not None:
            payload = review_payload
    if _legacy_lane_from_route_payload(payload) == PLAN_MODE:
        review_response = await provider.complete(
            messages=_agent_turn_router_repair_messages(
                text=text,
                bad_response=json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
                issue=(
                    "the route selected plan_mode; verify that the user explicitly asked for planning, "
                    "refinement, comparison, or decision-making before execution, and not merely for "
                    "review, audit, inspection, or code-quality analysis of the current workspace"
                ),
                transcript_tail=transcript_tail,
                selected_skills=selected_skills,
                surface=surface,
            ),
            model=model,
            temperature=0.0,
            max_tokens=900,
        )
        review_payload = _first_json_object(getattr(review_response, "text", ""))
        review_issue = _model_route_payload_issue(review_payload)
        if not review_issue and review_payload is not None:
            payload = review_payload
    no_execution_consistency_reviewed = False
    if _route_payload_needs_no_execution_consistency_review(payload):
        review_response = await provider.complete(
            messages=_agent_turn_router_repair_messages(
                text=text,
                bad_response=json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
                issue=(
                    "the route selected a no-execution/read-only compatibility lane while also asking for "
                    "action/reporting or a probe-like workflow. Verify that no command execution, fresh "
                    "validation, workspace mutation, repair loop, or current build/test/run/check truth is "
                    "needed. If the user needs fresh validation truth, choose execute, transient_execute, "
                    "validation_gate, and validation evidence. If the user wants advice, proposal, diagnosis, "
                    "or a broader repair loop, choose inspect, plan, one_pass, or repair_loop as appropriate."
                ),
                transcript_tail=transcript_tail,
                selected_skills=selected_skills,
                surface=surface,
            ),
            model=model,
            temperature=0.0,
            max_tokens=900,
        )
        review_payload = _first_json_object(getattr(review_response, "text", ""))
        review_issue = _model_route_payload_issue(review_payload)
        if not review_issue and review_payload is not None:
            payload = review_payload
            no_execution_consistency_reviewed = True
    surface_policy = normalize_agent_surface_policy(payload)
    if surface_policy.phase_shape == PHASE_VALIDATION_GATE and not no_execution_consistency_reviewed:
        review_response = await provider.complete(
            messages=_agent_turn_router_repair_messages(
                text=text,
                bad_response=json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
                issue=(
                    "the route selected validation_gate; verify that the user is asking for a pure fresh "
                    "validation truth answer, not advice, a proposal, performance diagnosis, gameplay "
                    "assessment, planning, or a broader repair loop. If the request is about what to do "
                    "next or how to fix a quality/performance problem, choose inspect, plan, one_pass, "
                    "or repair_loop as appropriate instead of validation_gate"
                ),
                transcript_tail=transcript_tail,
                selected_skills=selected_skills,
                surface=surface,
            ),
            model=model,
            temperature=0.0,
            max_tokens=900,
        )
        review_payload = _first_json_object(getattr(review_response, "text", ""))
        review_issue = _model_route_payload_issue(review_payload)
        if not review_issue and review_payload is not None:
            payload = review_payload
    return _decision_from_model_route_payload(payload)


@dataclass(frozen=True)
class TranscriptRef:
    role: str
    text: str
    created_at: str = ""

    def to_payload(self) -> dict[str, str]:
        return {
            "role": self.role,
            "text": self.text,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class RunNarratorSnapshot:
    """Snapshot passed to the narrator lane.

    It should be assembled from run state, event streams, queue state, and
    transcript state. It should not require arbitrary file reads.
    """

    snapshot_version: str = "run_narrator_snapshot_v1"
    snapshot_id: str = ""
    created_at: str = field(default_factory=_now_iso)
    run_id: str = ""
    task_id: str = ""
    objective: str = ""
    workspace: str = ""
    status: str = "idle"
    phase: str = "waiting"
    current_step: str = ""
    elapsed_seconds: float = 0.0
    model: str = ""
    mode_line: str = ""
    recent_events: tuple[str, ...] = ()
    activity: tuple[str, ...] = ()
    results: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    validation: str = ""
    validation_score: str = ""
    blockers: tuple[str, ...] = ()
    queued_work: str = ""
    trace_refs: tuple[str, ...] = ()
    transcript_tail: tuple[TranscriptRef, ...] = ()
    source_event_count: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "snapshot_version": self.snapshot_version,
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "objective": self.objective,
            "workspace": self.workspace,
            "status": self.status,
            "phase": self.phase,
            "current_step": self.current_step,
            "elapsed_seconds": round(float(self.elapsed_seconds or 0.0), 3),
            "model": self.model,
            "mode_line": self.mode_line,
            "recent_events": list(self.recent_events),
            "activity": list(self.activity),
            "results": list(self.results),
            "changed_files": list(self.changed_files),
            "artifacts": list(self.artifacts),
            "validation": self.validation,
            "validation_score": self.validation_score,
            "blockers": list(self.blockers),
            "queued_work": self.queued_work,
            "trace_refs": list(self.trace_refs),
            "transcript_tail": [item.to_payload() for item in self.transcript_tail],
            "source_event_count": int(self.source_event_count or 0),
        }

    def to_prompt_text(self) -> str:
        return json.dumps(self.to_payload(), sort_keys=True, ensure_ascii=True, indent=2)

    @property
    def has_run_context(self) -> bool:
        return bool(
            self.run_id
            or self.task_id
            or self.objective
            or self.recent_events
            or self.activity
            or self.results
            or self.changed_files
            or self.artifacts
            or self.trace_refs
        )


@dataclass(frozen=True)
class NarratorRequest:
    question: str
    snapshot: RunNarratorSnapshot
    request_id: str = ""
    surface: str = ""
    style: str = "compact"
    verbosity: str = "normal"
    max_tokens: int = 700
    created_at: str = field(default_factory=_now_iso)

    def to_prompt_text(self) -> str:
        return (
            f"User question: {self.question}\n"
            f"Surface: {self.surface or 'unknown'}\n"
            f"Style: {self.style}; verbosity: {self.verbosity}\n\n"
            "Run snapshot:\n"
            f"{self.snapshot.to_prompt_text()}"
        )


@dataclass(frozen=True)
class NarratorResponse:
    request_id: str
    status: str
    text: str
    created_at: str = field(default_factory=_now_iso)
    cited_refs: tuple[str, ...] = ()
    confidence: float = 1.0
    stale: bool = False
    fallback_used: bool = False
    failure: str = ""
    snapshot_id: str = ""

    def is_stale_against(self, snapshot: RunNarratorSnapshot) -> bool:
        return bool(self.snapshot_id and snapshot.snapshot_id and self.snapshot_id != snapshot.snapshot_id)


@dataclass(frozen=True)
class NarratorReport:
    """Human report emitted by the passive narrator sidecar.

    Reports are generated from sanitized run snapshots/events. They are not
    tool calls and they do not grant the narrator read, write, shell, approval,
    or steering authority.
    """

    kind: str
    text: str
    relation_to_request: str = ""
    done: tuple[str, ...] = ()
    remaining: str = ""
    next: str = ""
    confidence: float = 0.8
    stale: bool = False
    verbosity: str = "compact"
    source_snapshot_id: str = ""
    created_at: str = field(default_factory=_now_iso)

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "relation_to_request": self.relation_to_request,
            "done": list(self.done),
            "remaining": self.remaining,
            "next": self.next,
            "confidence": self.confidence,
            "stale": self.stale,
            "verbosity": self.verbosity,
            "source_snapshot_id": self.source_snapshot_id,
            "created_at": self.created_at,
        }


NarratorEventCallback = Callable[[dict[str, Any]], None]
NarratorSnapshotGetter = Callable[[], RunNarratorSnapshot]


def _emit_narrator_event(
    callback: NarratorEventCallback | None,
    event_type: str,
    *,
    request: NarratorRequest | None = None,
    response: NarratorResponse | None = None,
    text: str = "",
    error: str = "",
) -> None:
    if callback is None:
        return
    callback(
        build_narrator_event(
            event_type,
            request=request,
            response=response,
            text=text,
            error=error,
        )
    )


@dataclass
class NarratorJobHandle:
    """Cancellable handle for one in-flight narrator request."""

    request: NarratorRequest
    task: asyncio.Task[NarratorResponse]

    def cancel(self) -> bool:
        return self.task.cancel()

    def done(self) -> bool:
        return self.task.done()

    async def wait(self) -> NarratorResponse:
        return await self.task


def build_narrator_event(
    event_type: str,
    *,
    request: NarratorRequest | None = None,
    response: NarratorResponse | None = None,
    text: str = "",
    error: str = "",
) -> dict[str, Any]:
    """Return a normalized narrator event row for surface subscribers."""

    snapshot = request.snapshot if request is not None else None
    if snapshot is None and response is not None:
        snapshot_id = response.snapshot_id
    else:
        snapshot_id = snapshot.snapshot_id if snapshot is not None else ""
    return {
        "event": event_type,
        "request_id": request.request_id if request is not None else response.request_id if response is not None else "",
        "snapshot_id": snapshot_id,
        "snapshot_version": snapshot.snapshot_version if snapshot is not None else "",
        "surface": request.surface if request is not None else "",
        "status": response.status if response is not None else "",
        "text": text or (response.text if response is not None else ""),
        "error": error or (response.failure if response is not None else ""),
        "created_at": _now_iso(),
    }


def build_narrator_report_event(report: NarratorReport) -> dict[str, Any]:
    """Return a normalized event row for a passive narrator report."""

    payload = report.to_payload()
    return {
        "event": f"narrator.{report.kind}",
        "report": payload,
        **payload,
    }


def _request_subject(snapshot: RunNarratorSnapshot) -> str:
    objective = _clip(snapshot.objective, limit=120)
    return objective or "the request"


def _changed_summary(snapshot: RunNarratorSnapshot, *, limit: int = 2) -> tuple[str, ...]:
    return tuple(
        _display_path(path, snapshot.workspace)
        for path in snapshot.changed_files[-limit:]
        if str(path or "").strip()
    )


def _done_items(snapshot: RunNarratorSnapshot) -> tuple[str, ...]:
    items: list[str] = []
    changed = _changed_summary(snapshot, limit=3)
    if changed:
        items.append("changed " + ", ".join(changed))
    if snapshot.validation and snapshot.validation != "running":
        score = f" ({snapshot.validation_score})" if snapshot.validation_score else ""
        items.append(f"validation {snapshot.validation}{score}")
    latest = _latest_clean_progress((*snapshot.activity, *snapshot.results))
    if latest and latest.rstrip(".").lower() not in {"done", "run completed"}:
        items.append(latest.rstrip("."))
    return _compact_list(items, limit=4)


def deterministic_narrator_report(
    snapshot: RunNarratorSnapshot,
    *,
    trigger: str,
    event_name: str = "",
    event_payload: Mapping[str, Any] | None = None,
) -> NarratorReport | None:
    """Build a passive narrator report from a sanitized snapshot/event.

    This function is deliberately deterministic and side-effect free. It never
    reads files, calls tools, or mutates state.
    """

    trigger = str(trigger or "").strip().lower()
    event_name = str(event_name or "").strip()
    event_payload = event_payload or {}
    subject = _request_subject(snapshot)
    done = _done_items(snapshot)
    next_step = _next_step_line(snapshot)
    relation = f"Report is grounded in the current run state for: {subject}"
    kind = trigger if trigger in {
        "opening",
        "progress",
        "checkpoint",
        "uncertainty",
        "drift",
        "blocker",
        "final",
        "heartbeat",
    } else "progress"
    text = ""
    remaining = ""

    if kind == "opening":
        return None
    elif kind == "heartbeat":
        elapsed = event_payload.get("elapsed_seconds") or snapshot.elapsed_seconds
        elapsed_text = _format_elapsed(float(elapsed or 0.0))
        current = _heartbeat_visible_step(snapshot)
        if current:
            text = f"Still working after {elapsed_text}. Current visible step: {current}."
        else:
            text = f"Still working after {elapsed_text}. No newer executor event is visible yet."
        remaining = next_step
    elif kind == "final":
        status = str(snapshot.status or "finished").strip().lower()
        if status in {"completed", "done"}:
            summary = "; ".join(done)
            if summary:
                text = f"Run finished successfully: {summary}."
            else:
                text = "Run finished successfully."
            remaining = "No blocker is visible in the final snapshot."
        elif status in {"failed", "blocked", "stopped"}:
            blocker = "; ".join(_clip(item, limit=120) for item in snapshot.blockers[-3:]) or "the run did not complete cleanly"
            text = f"The run did not complete cleanly. Status: {status}; blocker: {blocker}."
            remaining = "A follow-up or repair pass is needed."
        else:
            text = f"The run finished with status {status or 'unknown'}."
            remaining = next_step
    elif kind == "blocker":
        blocker = "; ".join(_clip(item, limit=120) for item in snapshot.blockers[-3:]) or _clip(snapshot.current_step, limit=120)
        text = f"A blocker is visible for {subject}: {blocker}."
        remaining = "The executor needs a repair, retry, or user decision before this can finish cleanly."
    elif kind == "checkpoint":
        if event_name == "live.validation.started":
            text = f"Validation is checking whether the current work satisfies {subject}."
            remaining = "The next visible result should be validation passed, validation failed, or queued repair work."
        elif event_name in {"live.validation.completed", "live.validation.model_completed"}:
            score = f" ({snapshot.validation_score})" if snapshot.validation_score else ""
            if snapshot.validation == "passed":
                text = f"Validation passed{score}; the run is close to a clean finish for {subject}."
                remaining = "The executor should finish or hand off any queued follow-up."
            else:
                text = f"Validation failed{score}; the executor needs to repair the visible gaps for {subject}."
                remaining = "A repair or follow-up validation step remains."
        elif event_name == "super.hook.packet_enqueued" or snapshot.queued_work:
            text = f"Follow-up work was queued for {subject}: {_clip(snapshot.queued_work, limit=120)}."
            remaining = next_step
        elif _changed_summary(snapshot):
            text = f"Changed {', '.join(_changed_summary(snapshot))} for {subject}."
            remaining = "The next step is to validate or continue from that change."
        else:
            summary = "; ".join(done) if done else _clip(snapshot.current_step, limit=120)
            text = f"Checkpoint for {subject}: {summary or 'the run advanced'}."
            remaining = next_step
    else:
        if event_name == "model.requested":
            text = "Thinking through the next step."
            remaining = "It should either call a tool, validate, or prepare a response."
        elif event_name == "tool.started":
            tool_id = str(event_payload.get("tool_id") or "").strip()
            if tool_id in {"file_read", "list_directory", "workspace_check"}:
                text = "Checking the relevant workspace context."
            elif tool_id in {"file_write", "file_edit"}:
                text = "Preparing a workspace change."
            elif tool_id == "shell_command":
                text = "Using a terminal command because it is the direct way to advance the current task."
            else:
                text = "Running a project tool."
            remaining = next_step
        elif event_name == "tool.completed":
            tool_id = str(event_payload.get("tool_id") or "").strip()
            if tool_id in {"file_read", "list_directory", "workspace_check"}:
                text = "Relevant context is available; moving toward an answer."
            elif tool_id in {"file_write", "file_edit"}:
                changed = ", ".join(_changed_summary(snapshot)) or "workspace files"
                text = f"A workspace change landed: {changed}."
            elif tool_id == "shell_command":
                text = "The terminal command finished; using that result for the current task."
            else:
                text = "A project tool finished."
            remaining = next_step
        elif event_name == "super.heartbeat":
            return deterministic_narrator_report(
                snapshot,
                trigger="heartbeat",
                event_name=event_name,
                event_payload=event_payload,
            )
        else:
            current = _clean_progress_line(snapshot.current_step) or _clip(snapshot.current_step, limit=120)
            text = f"The run is progressing on {subject}: {current or snapshot.phase}."
            remaining = next_step

    if not text:
        return None
    return NarratorReport(
        kind=kind,
        text=_clip(text, limit=240),
        relation_to_request=relation,
        done=done,
        remaining=_clip(remaining, limit=180),
        next=_clip(next_step, limit=180),
        confidence=0.78 if snapshot.has_run_context else 0.58,
        source_snapshot_id=snapshot.snapshot_id,
    )


def deterministic_narrator_response(request: NarratorRequest) -> NarratorResponse:
    """Build a compact answer from the snapshot without a model provider."""

    snapshot = request.snapshot
    lines: list[str] = []
    wants_next_step = _is_next_step_question(request.question)
    if not snapshot.has_run_context:
        transcript_hint = ""
        if snapshot.transcript_tail:
            latest = snapshot.transcript_tail[-1]
            transcript_hint = f" Latest transcript entry: {latest.role}: {_clip(latest.text, limit=120)}"
        if wants_next_step:
            lines.append("Next: no active or recent run is available, so there is no visible next step yet.")
        else:
            lines.append("Status: no active or recent run is available in this session snapshot." + transcript_hint)
    else:
        if wants_next_step:
            lines.append(_next_step_line(snapshot))
            lines.append(_status_line(snapshot))
        else:
            lines.append(_status_line(snapshot))
            if str(snapshot.status or "").strip().lower() in {"running", "queued"}:
                lines.append(_next_step_line(snapshot))
        latest_completed = _latest_clean_progress((*snapshot.activity, *snapshot.results))
        if latest_completed and latest_completed.rstrip(".").lower() not in {"done", "run completed"}:
            lines.append(f"Last completed: {latest_completed.rstrip('.')}.")
        current_step = _clean_progress_line(snapshot.current_step) or _clip(snapshot.current_step, limit=150)
        if current_step and not wants_next_step:
            lines.append(f"Current work: {current_step}.")
        elif str(snapshot.status or "").strip().lower() == "running" and not wants_next_step:
            lines.append("Current work: waiting for the next event from the active run.")
        if snapshot.changed_files and not wants_next_step:
            lines.append(
                "Changed: "
                + ", ".join(
                    _clip(_display_path(path, snapshot.workspace), limit=80)
                    for path in snapshot.changed_files[-4:]
                )
                + "."
            )
        elif snapshot.artifacts and not wants_next_step:
            lines.append(
                "Artifacts: "
                + ", ".join(
                    _clip(_display_path(path, snapshot.workspace), limit=80)
                    for path in snapshot.artifacts[-4:]
                )
                + "."
            )
        if snapshot.validation:
            score = f" ({snapshot.validation_score})" if snapshot.validation_score else ""
            lines.append(f"Validation: {snapshot.validation}{score}.")
        if snapshot.blockers:
            lines.append(
                "Blockers: " + "; ".join(_clip(item, limit=120) for item in snapshot.blockers[-3:])
            )
        elif not wants_next_step:
            lines.append("Blockers: none visible in the current snapshot.")
        if snapshot.queued_work and not wants_next_step:
            lines.append(f"Queued: {_clip(snapshot.queued_work, limit=160)}.")

    return NarratorResponse(
        request_id=request.request_id,
        status="fallback",
        text="\n".join(lines),
        cited_refs=_compact_list(snapshot.trace_refs, limit=4),
        confidence=0.72 if snapshot.has_run_context else 0.55,
        fallback_used=True,
        snapshot_id=snapshot.snapshot_id,
    )


def narrator_system_prompt() -> str:
    return (
        "You are the progress narrator for Diane. Answer only from the provided run snapshot. "
        "Do not claim to inspect files, run commands, mutate state, steer execution, or access hidden context. "
        "If the snapshot is missing information, say what is unknown. Answer the user's exact question first. "
        "For next-step questions, start with a short Next line. For progress/status questions, start with a short Status line. "
        "Use at most 4 short plain lines. Prefer labels like Next, Status, Last completed, Validation, and Blockers. "
        "Do not include trace paths, event-log paths, Markdown headings, bold text, or raw JSON/tool payloads unless the user explicitly asks for logs."
    )


def narrator_messages(request: NarratorRequest) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": narrator_system_prompt()},
        {"role": "user", "content": request.to_prompt_text()},
    ]


async def generate_narrator_response(
    provider: Any,
    request: NarratorRequest,
    *,
    model: str,
) -> NarratorResponse:
    """Ask an LLM to narrate a snapshot, falling back deterministically.

    This function intentionally passes no tools to the provider.
    """

    fallback = deterministic_narrator_response(request)
    if provider is None:
        return fallback
    try:
        result = await provider.complete(
            messages=narrator_messages(request),
            model=model,
            temperature=0.2,
            max_tokens=request.max_tokens,
        )
    except Exception as exc:
        return NarratorResponse(
            request_id=request.request_id,
            status="failed",
            text=fallback.text,
            cited_refs=fallback.cited_refs,
            confidence=fallback.confidence,
            fallback_used=True,
            failure=str(exc),
            snapshot_id=request.snapshot.snapshot_id,
        )
    text = str(getattr(result, "text", "") or "").strip()
    if not text:
        return fallback
    return NarratorResponse(
        request_id=request.request_id,
        status="completed",
        text=text,
        cited_refs=_compact_list(request.snapshot.trace_refs, limit=4),
        confidence=0.86,
        snapshot_id=request.snapshot.snapshot_id,
    )


async def run_narrator_job(
    provider: Any,
    request: NarratorRequest,
    *,
    model: str,
    latest_snapshot_getter: NarratorSnapshotGetter | None = None,
    event_callback: NarratorEventCallback | None = None,
) -> NarratorResponse:
    """Run one narrator request as a cancellable async job."""

    _emit_narrator_event(event_callback, "narrator.requested", request=request)
    _emit_narrator_event(event_callback, "narrator.started", request=request)
    try:
        response = await generate_narrator_response(provider, request, model=model)
    except asyncio.CancelledError:
        _emit_narrator_event(
            event_callback,
            "narrator.failed",
            request=request,
            error="cancelled",
        )
        raise

    latest_snapshot = latest_snapshot_getter() if latest_snapshot_getter is not None else request.snapshot
    if response.is_stale_against(latest_snapshot):
        stale_response = replace(response, status="stale", stale=True)
        _emit_narrator_event(
            event_callback,
            "narrator.stale",
            request=request,
            response=stale_response,
        )
        return stale_response

    if response.status == "failed":
        _emit_narrator_event(
            event_callback,
            "narrator.failed",
            request=request,
            response=response,
            error=response.failure,
        )
    else:
        _emit_narrator_event(
            event_callback,
            "narrator.completed",
            request=request,
            response=response,
        )
    return response


def start_narrator_job(
    provider: Any,
    request: NarratorRequest,
    *,
    model: str,
    latest_snapshot_getter: NarratorSnapshotGetter | None = None,
    event_callback: NarratorEventCallback | None = None,
    previous: NarratorJobHandle | None = None,
    cancel_previous: bool = True,
) -> NarratorJobHandle:
    """Start a narrator job on the current event loop.

    Surfaces can pass the previous handle to debounce repeated progress
    questions; the old task is cancelled before the new one starts.
    """

    if cancel_previous and previous is not None and not previous.done():
        previous.cancel()
    task = asyncio.create_task(
        run_narrator_job(
            provider,
            request,
            model=model,
            latest_snapshot_getter=latest_snapshot_getter,
            event_callback=event_callback,
        )
    )
    return NarratorJobHandle(request=request, task=task)
