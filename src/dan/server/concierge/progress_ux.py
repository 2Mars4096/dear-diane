"""Progressive Response UX — phase-chunked progressive disclosure (plan 31-14).

Replaces the "silence → big dump" response pattern with continuous, compact
status.  Each logical phase of work gets its own chat block that evolves
in-place with sub-step progress.  The user sees:
  acknowledge → plan → progress → checkpoint → result

Surface-specific renderers adapt the output to the capabilities of each
surface (editor, CLI, Telegram, WhatsApp).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

VerbosityLevel = Literal["full", "compact", "minimal"]

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ProgressPhase(BaseModel):
    """Single phase in a progressive response lifecycle."""

    id: str
    name: str
    status: Literal["pending", "active", "completed", "failed"] = "pending"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    elapsed_seconds: float = 0.0
    summary: str | None = None
    sub_steps: list[str] = Field(default_factory=list)


class CheckpointOption(BaseModel):
    """One option in an interactive checkpoint."""

    label: str
    value: str
    is_default: bool = False
    is_safe_default: bool = False


class CheckpointOptions(BaseModel):
    """Interactive checkpoint presented to the user at phase boundaries."""

    summary: str
    options: list[CheckpointOption]


class InteractionRequest(BaseModel):
    """A request for user interaction during progressive response."""

    kind: Literal["required_clarification", "advisory_checkpoint"]
    checkpoint: CheckpointOptions
    timeout_seconds: float = 300.0


# ---------------------------------------------------------------------------
# ProgressRenderer protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ProgressRenderer(Protocol):
    """Surface-specific renderer for progressive response blocks."""

    async def announce_plan(
        self, steps: list[str], estimated_time: float | None = None,
    ) -> None: ...

    async def phase_update(self, phase_id: str, label: str) -> None: ...

    async def phase_complete(self, phase_id: str, summary: str) -> None: ...

    async def checkpoint(self, options: CheckpointOptions) -> str | None: ...

    async def deliver_result(self, content: str) -> None: ...

    async def heartbeat(self, elapsed: float, message: str) -> None: ...


# ---------------------------------------------------------------------------
# ProgressSession
# ---------------------------------------------------------------------------

_DEFAULT_THROTTLE_SECONDS = 0.5
_ANTI_NOISE_DELAY_SECONDS = 0.5
_ANTI_NOISE_THRESHOLD_SECONDS = 3.0


class ProgressSession:
    """Manages the block lifecycle for a single progressive response.

    Tracks phases, enforces throttling, and routes updates to the renderer.
    """

    def __init__(
        self,
        renderer: ProgressRenderer | None = None,
        surface_type: str = "editor",
        throttle_seconds: float = _DEFAULT_THROTTLE_SECONDS,
        *,
        surface: str | None = None,
        verbosity: VerbosityLevel | None = None,
    ) -> None:
        self.renderer = renderer or NullProgressRenderer()
        self.surface_type = surface or surface_type
        self.verbosity = verbosity or _SURFACE_VERBOSITY.get(self.surface_type, "compact")
        self.throttle_seconds = throttle_seconds

        self._phases: dict[str, ProgressPhase] = {}
        self._phase_order: list[str] = []
        self._current_phase_id: str | None = None
        self._start_time: float = time.monotonic()
        self._last_update_time: float = 0.0
        self._last_phase_event_time: float = 0.0

    # -- phase lifecycle ----------------------------------------------------

    def start_phase(self, phase_id: str, name: str) -> ProgressPhase:
        """Create and activate a new phase."""
        now = datetime.now(timezone.utc)
        phase = ProgressPhase(
            id=phase_id, name=name, status="active", started_at=now,
        )
        self._phases[phase_id] = phase
        self._phase_order.append(phase_id)
        self._current_phase_id = phase_id
        return phase

    def update_phase(self, phase_id: str, label: str) -> None:
        """Add a sub-step label to *phase_id* (throttled)."""
        phase = self._phases.get(phase_id)
        if phase is None:
            return
        now = time.monotonic()
        if now - self._last_update_time < self.throttle_seconds:
            return
        self._last_update_time = now
        phase.sub_steps.append(label)
        if phase.started_at is not None:
            phase.elapsed_seconds = (
                datetime.now(timezone.utc) - phase.started_at
            ).total_seconds()

    def complete_phase(self, phase_id: str, summary: str) -> None:
        """Mark *phase_id* as completed with a summary."""
        phase = self._phases.get(phase_id)
        if phase is None:
            return
        now = datetime.now(timezone.utc)
        phase.status = "completed"
        phase.completed_at = now
        phase.summary = summary
        if phase.started_at is not None:
            phase.elapsed_seconds = (now - phase.started_at).total_seconds()
        if self._current_phase_id == phase_id:
            self._current_phase_id = None

    def get_current_phase(self) -> ProgressPhase | None:
        if self._current_phase_id is None:
            return None
        return self._phases.get(self._current_phase_id)

    def get_all_phases(self) -> list[ProgressPhase]:
        return [self._phases[pid] for pid in self._phase_order if pid in self._phases]

    def elapsed_total(self) -> float:
        return time.monotonic() - self._start_time

    # -- plan disclosure (Task 3-3) ----------------------------------------

    async def disclose_plan(
        self,
        steps: list[str],
        estimated_time: float | None = None,
        *,
        offer_review: bool | None = None,
    ) -> str | None:
        """Emit a plan disclosure block via the renderer.

        If *offer_review* is ``None``, it defaults to ``True`` for complex
        plans (>3 steps).  When review is offered, a checkpoint is presented.
        Returns the user's checkpoint response or ``None``.
        """
        if offer_review is None:
            offer_review = len(steps) > _PLAN_REVIEW_STEP_THRESHOLD

        await self.renderer.announce_plan(steps, estimated_time)

        if not offer_review:
            return None

        opts = CheckpointOptions(
            summary="Review before I start?",
            options=[
                CheckpointOption(
                    label="Looks good, go ahead",
                    value="proceed",
                    is_default=True,
                    is_safe_default=True,
                ),
                CheckpointOption(label="Let me adjust the plan", value="revise"),
            ],
        )
        return await self.renderer.checkpoint(opts)


# ---------------------------------------------------------------------------
# Surface-specific renderers
# ---------------------------------------------------------------------------


class NullProgressRenderer:
    """No-op renderer for sessions created before a surface-specific renderer is available."""

    async def announce_plan(
        self, steps: list[str], estimated_time: float | None = None,
    ) -> None:
        pass

    async def phase_update(self, phase_id: str, label: str) -> None:
        pass

    async def phase_complete(self, phase_id: str, summary: str) -> None:
        pass

    async def checkpoint(self, options: CheckpointOptions) -> str | None:
        return None

    async def deliver_result(self, content: str) -> None:
        pass

    async def heartbeat(self, elapsed: float, message: str) -> None:
        pass


class CLIProgressRenderer:
    """Format text with phase headers, spinners, timers.

    For each phase: ``"▶ {name}..."`` → ``"✓ {name} ({elapsed}s)"``.
    Heartbeat as status line.
    """

    def __init__(self) -> None:
        self.output: list[str] = []

    async def announce_plan(
        self, steps: list[str], estimated_time: float | None = None,
    ) -> None:
        header = "Plan:"
        if estimated_time is not None:
            header += f" (~{estimated_time:.0f}s)"
        lines = [header] + [f"  {i + 1}. {s}" for i, s in enumerate(steps)]
        self.output.append("\n".join(lines))

    async def phase_update(self, phase_id: str, label: str) -> None:
        self.output.append(f"▶ {label}...")

    async def phase_complete(self, phase_id: str, summary: str) -> None:
        self.output.append(f"✓ {summary}")

    async def checkpoint(self, options: CheckpointOptions) -> str | None:
        lines = [options.summary]
        for opt in options.options:
            marker = " [default]" if opt.is_default else ""
            lines.append(f"  - {opt.label}{marker}")
        self.output.append("\n".join(lines))
        defaults = [o for o in options.options if o.is_default]
        return defaults[0].value if defaults else None

    async def deliver_result(self, content: str) -> None:
        self.output.append(content)

    async def heartbeat(self, elapsed: float, message: str) -> None:
        self.output.append(f"⏱ {elapsed:.0f}s — {message}")


class TelegramProgressRenderer:
    """Edit-in-place messages for Telegram.

    New message per phase.  Sub-step updates edit the current message.
    Checkpoints as inline keyboard buttons.  Respects 4096-char limit.
    """

    _MAX_MESSAGE_LENGTH = 4096

    def __init__(
        self,
        send_fn: Callable[..., Coroutine[Any, Any, int]],
        edit_fn: Callable[..., Coroutine[Any, Any, None]],
    ) -> None:
        self._send = send_fn
        self._edit = edit_fn
        self._current_message_id: int | None = None
        self._current_text: str = ""
        self.messages_sent: list[str] = []

    async def announce_plan(
        self, steps: list[str], estimated_time: float | None = None,
    ) -> None:
        header = "📋 Plan"
        if estimated_time is not None:
            header += f" (~{estimated_time:.0f}s)"
        lines = [header + ":"] + [f"  {i + 1}. {s}" for i, s in enumerate(steps)]
        text = "\n".join(lines)
        self._current_message_id = await self._send(text)
        self._current_text = text
        self.messages_sent.append(text)

    async def phase_update(self, phase_id: str, label: str) -> None:
        text = f"▶ {label}..."
        if self._current_message_id is not None:
            combined = self._current_text + "\n" + text
            if len(combined) > self._MAX_MESSAGE_LENGTH:
                self._current_message_id = await self._send(text)
                self._current_text = text
                self.messages_sent.append(text)
            else:
                await self._edit(self._current_message_id, combined)
                self._current_text = combined
        else:
            self._current_message_id = await self._send(text)
            self._current_text = text
            self.messages_sent.append(text)

    async def phase_complete(self, phase_id: str, summary: str) -> None:
        text = f"✓ {summary}"
        if self._current_message_id is not None:
            combined = self._current_text + "\n" + text
            if len(combined) > self._MAX_MESSAGE_LENGTH:
                self._current_message_id = await self._send(text)
                self._current_text = text
                self.messages_sent.append(text)
            else:
                await self._edit(self._current_message_id, combined)
                self._current_text = combined
        else:
            self._current_message_id = await self._send(text)
            self._current_text = text
            self.messages_sent.append(text)

    async def checkpoint(self, options: CheckpointOptions) -> str | None:
        lines = [options.summary]
        for opt in options.options:
            marker = " ✅" if opt.is_default else ""
            lines.append(f"  • {opt.label}{marker}")
        text = "\n".join(lines)
        self._current_message_id = await self._send(text)
        self._current_text = text
        self.messages_sent.append(text)
        defaults = [o for o in options.options if o.is_default]
        return defaults[0].value if defaults else None

    async def deliver_result(self, content: str) -> None:
        if len(content) > self._MAX_MESSAGE_LENGTH:
            content = content[: self._MAX_MESSAGE_LENGTH - 20] + "\n\n[truncated]"
        self._current_message_id = await self._send(content)
        self._current_text = content
        self.messages_sent.append(content)

    async def heartbeat(self, elapsed: float, message: str) -> None:
        text = f"⏱ {elapsed:.0f}s — {message}"
        if self._current_message_id is not None:
            combined = self._current_text + "\n" + text
            if len(combined) <= self._MAX_MESSAGE_LENGTH:
                await self._edit(self._current_message_id, combined)
                self._current_text = combined
                return
        self._current_message_id = await self._send(text)
        self._current_text = text
        self.messages_sent.append(text)


class WhatsAppProgressRenderer:
    """Bookend pattern: one message at start, one at end.

    Max one mid-point heartbeat for tasks >5 minutes.
    """

    _HEARTBEAT_THRESHOLD_SECONDS = 300.0

    def __init__(
        self,
        send_fn: Callable[[str], Coroutine[Any, Any, None]],
    ) -> None:
        self._send = send_fn
        self._start_sent: bool = False
        self._heartbeat_sent: bool = False
        self._result_sent: bool = False
        self.messages_sent: list[str] = []

    async def announce_plan(
        self, steps: list[str], estimated_time: float | None = None,
    ) -> None:
        header = "Got it — working on it."
        if estimated_time is not None:
            header += f" ETA ~{estimated_time:.0f}s."
        lines = [header, ""] + [f"{i + 1}. {s}" for i, s in enumerate(steps)]
        text = "\n".join(lines)
        await self._send(text)
        self._start_sent = True
        self.messages_sent.append(text)

    async def phase_update(self, phase_id: str, label: str) -> None:
        pass

    async def phase_complete(self, phase_id: str, summary: str) -> None:
        pass

    async def checkpoint(self, options: CheckpointOptions) -> str | None:
        lines = [options.summary]
        for opt in options.options:
            lines.append(f"  • {opt.label}")
        text = "\n".join(lines)
        await self._send(text)
        self.messages_sent.append(text)
        defaults = [o for o in options.options if o.is_default]
        return defaults[0].value if defaults else None

    async def deliver_result(self, content: str) -> None:
        await self._send(content)
        self._result_sent = True
        self.messages_sent.append(content)

    async def heartbeat(self, elapsed: float, message: str) -> None:
        if self._heartbeat_sent:
            return
        if elapsed < self._HEARTBEAT_THRESHOLD_SECONDS:
            return
        text = f"Still working... ({elapsed:.0f}s elapsed) — {message}"
        await self._send(text)
        self._heartbeat_sent = True
        self.messages_sent.append(text)


class EditorProgressRenderer:
    """Streaming sections with collapsible sub-steps for the editor surface."""

    def __init__(
        self,
        send_fn: Callable[[str], Coroutine[Any, Any, None]],
    ) -> None:
        self._send = send_fn
        self.output: list[str] = []

    async def announce_plan(
        self, steps: list[str], estimated_time: float | None = None,
    ) -> None:
        header = "**Plan**"
        if estimated_time is not None:
            header += f" (~{estimated_time:.0f}s)"
        lines = [header] + [f"  {i + 1}. {s}" for i, s in enumerate(steps)]
        text = "\n".join(lines)
        await self._send(text)
        self.output.append(text)

    async def phase_update(self, phase_id: str, label: str) -> None:
        text = f"  ▸ {label}"
        await self._send(text)
        self.output.append(text)

    async def phase_complete(self, phase_id: str, summary: str) -> None:
        text = f"✓ {summary}"
        await self._send(text)
        self.output.append(text)

    async def checkpoint(self, options: CheckpointOptions) -> str | None:
        lines = [f"**{options.summary}**"]
        for opt in options.options:
            marker = " (default)" if opt.is_default else ""
            lines.append(f"  - {opt.label}{marker}")
        text = "\n".join(lines)
        await self._send(text)
        self.output.append(text)
        defaults = [o for o in options.options if o.is_default]
        return defaults[0].value if defaults else None

    async def deliver_result(self, content: str) -> None:
        await self._send(content)
        self.output.append(content)

    async def heartbeat(self, elapsed: float, message: str) -> None:
        text = f"⏱ {elapsed:.0f}s — {message}"
        await self._send(text)
        self.output.append(text)


# ---------------------------------------------------------------------------
# Verbosity control
# ---------------------------------------------------------------------------

_SURFACE_VERBOSITY: dict[str, VerbosityLevel] = {
    "editor": "full",
    "cli": "full",
    "telegram": "compact",
    "whatsapp": "minimal",
    "whatsapp-web": "minimal",
    "email": "minimal",
    "wechat": "minimal",
}


def resolve_verbosity(surface: str) -> VerbosityLevel:
    """Return verbosity level for *surface*, respecting env var override."""
    override = os.environ.get("DAN_PROGRESS_VERBOSITY", "").strip().lower()
    if override in ("full", "compact", "minimal"):
        return override  # type: ignore[return-value]
    return _SURFACE_VERBOSITY.get(surface, "compact")


# ---------------------------------------------------------------------------
# Pre-flight clarification
# ---------------------------------------------------------------------------


def _preflight_enabled() -> bool:
    return os.environ.get("DAN_PREFLIGHT_CLARIFY", "1").strip() == "1"


def _preflight_threshold() -> float:
    try:
        return float(os.environ.get("DAN_PREFLIGHT_THRESHOLD_SECONDS", "10"))
    except (ValueError, TypeError):
        return 10.0


def _preflight_cost_threshold() -> float:
    try:
        return float(os.environ.get("DAN_PREFLIGHT_COST_THRESHOLD", "0.10"))
    except (ValueError, TypeError):
        return 0.10


def _clarification_timeout(surface: str) -> float:
    """Return the clarification timeout for *surface*.

    CLI and editor surfaces default to infinite (no timeout).
    Messaging surfaces use ``DAN_CLARIFICATION_TIMEOUT_SECONDS`` (default 300s).
    """
    if surface in ("cli", "editor"):
        return float("inf")
    try:
        return float(os.environ.get("DAN_CLARIFICATION_TIMEOUT_SECONDS", "300"))
    except (ValueError, TypeError):
        return 300.0


# ---------------------------------------------------------------------------
# Task 4-2: Pre-flight cost/time threshold
# ---------------------------------------------------------------------------


def should_preflight_clarify(
    estimated_seconds: float,
    estimated_cost: float,
    config: dict[str, Any] | None = None,
) -> bool:
    """Return ``True`` if the planned task exceeds cost or time thresholds.

    Thresholds come from env vars ``DAN_PREFLIGHT_THRESHOLD_SECONDS`` and
    ``DAN_PREFLIGHT_COST_THRESHOLD``, overridable via *config*.
    """
    if not _preflight_enabled():
        return False

    cfg = config or {}
    time_threshold = cfg.get("threshold_seconds", _preflight_threshold())
    cost_threshold = cfg.get("cost_threshold", _preflight_cost_threshold())

    return estimated_seconds >= time_threshold or estimated_cost >= cost_threshold


# ---------------------------------------------------------------------------
# Task 4-3: Question generation (enhanced heuristics)
# ---------------------------------------------------------------------------

PREFLIGHT_QUESTION_PROMPT_TEMPLATE = """\
Given the following plan summary and context, generate 1-2 specific clarifying
questions that would help avoid wasted work.  Do NOT ask generic questions.

Plan: {plan_summary}
Context: {context}

Questions (one per line):
"""


def generate_preflight_questions(
    task_description: str,
    context: dict[str, Any],
    *,
    plan_summary: str | None = None,
) -> list[str] | None:
    """Generate pre-flight clarifying questions for a task.

    Returns ``None`` when pre-flight is disabled or the task is below the
    cost/time threshold.  Uses heuristic rules keyed on keywords in the task
    description and context flags.
    """
    if not _preflight_enabled():
        return None

    estimated_time = context.get("estimated_time", 0.0)
    estimated_cost = context.get("estimated_cost", 0.0)
    if not should_preflight_clarify(estimated_time, estimated_cost, context):
        return None

    desc_lower = task_description.lower()
    questions: list[str] = []

    if "dataset" in desc_lower and not context.get("columns"):
        questions.append(
            "Which columns or variables should I focus on?"
        )
    if "model" in desc_lower and not context.get("model_specified"):
        questions.append(
            "Any preference on which model or method to use?"
        )
    if context.get("multiple_files") and not context.get("file_selected"):
        questions.append(
            "I found multiple relevant files — which one should I use?"
        )
    if any(kw in desc_lower for kw in ("file", "directory", "folder", "path")) and not context.get("target_directory"):
        questions.append("Which directory or file path should I target?")
    if any(kw in desc_lower for kw in ("analysis", "analyze", "statistics")) and not context.get("key_variables"):
        questions.append("Which key variables or metrics matter most?")
    if any(kw in desc_lower for kw in ("web", "url", "http", "fetch", "scrape")) and not context.get("target_url"):
        questions.append("Which specific URL or domain should I access?")

    return questions[:3] if questions else None


# ---------------------------------------------------------------------------
# Task 4-4: Quick-confirm mode
# ---------------------------------------------------------------------------


def format_quick_confirm(
    questions: list[str],
    defaults: list[str],
) -> InteractionRequest:
    """Format questions with obvious defaults as a quick-confirm checkpoint.

    Returns an ``InteractionRequest`` with ``advisory_checkpoint`` kind and a
    5-second timeout so the system proceeds automatically with defaults unless
    the user objects.
    """
    if not defaults:
        defaults = ["(auto)"] * len(questions)

    paired = list(zip(questions, defaults))
    assumption_lines = [f"• {d}" for _, d in paired]
    summary = "I'll proceed with:\n" + "\n".join(assumption_lines) + "\nOK?"

    return InteractionRequest(
        kind="advisory_checkpoint",
        checkpoint=CheckpointOptions(
            summary=summary,
            options=[
                CheckpointOption(
                    label="Proceed with defaults",
                    value="proceed",
                    is_default=True,
                    is_safe_default=True,
                ),
                CheckpointOption(label="Let me change something", value="change"),
            ],
        ),
        timeout_seconds=5.0,
    )


# ---------------------------------------------------------------------------
# Task 3-3: Plan disclosure
# ---------------------------------------------------------------------------

_PLAN_REVIEW_STEP_THRESHOLD = 3


def format_plan_disclosure(
    steps: list[str],
    estimated_time: float | None = None,
) -> str:
    """Format a plan summary suitable for display to the user."""
    header = "Plan"
    if estimated_time is not None:
        header += f" (~{estimated_time:.0f}s)"
    lines = [header + ":"] + [f"  {i + 1}. {s}" for i, s in enumerate(steps)]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Task 3-5: Result checkpoint
# ---------------------------------------------------------------------------

RESULT_SUMMARY_PROMPT_TEMPLATE = """\
Summarize the following content concisely.  The user's original question was:
"{user_focus}"

Content:
{content}

Provide a 2-3 sentence summary capturing the key findings.
"""


def result_checkpoint_enabled() -> bool:
    """Return ``True`` if result checkpoints are enabled via ``DAN_RESULT_CHECKPOINT=1``."""
    return os.environ.get("DAN_RESULT_CHECKPOINT", "0").strip() == "1"


def should_checkpoint_result(content: str, threshold: int = 1000) -> bool:
    """Return ``True`` if *content* is long enough to warrant a checkpoint."""
    return len(content) > threshold


def format_result_checkpoint(
    content: str,
    user_focus: str | None = None,
) -> CheckpointOptions:
    """Create a checkpoint offering detail-level options for a large result.

    Deterministic fallback: first 500 chars + ``"..."`` as the summary.
    """
    preview = content[:500] + ("..." if len(content) > 500 else "")
    focus_label = f"Show only: {user_focus}" if user_focus else "Show key findings"

    return CheckpointOptions(
        summary=preview,
        options=[
            CheckpointOption(label="Show full output", value="full"),
            CheckpointOption(
                label="Just key findings",
                value="summary",
                is_default=True,
                is_safe_default=True,
            ),
            CheckpointOption(label=focus_label, value="focus"),
        ],
    )


# ---------------------------------------------------------------------------
# Task 5-3: Checkpoint auto-proceed / timeout handling
# ---------------------------------------------------------------------------


def handle_checkpoint_timeout(checkpoint: CheckpointOptions) -> str | None:
    """Return the safe-default value from *checkpoint*, or ``None`` if none exists.

    Called when a checkpoint times out without user response.  If a safe default
    exists the task auto-proceeds; otherwise the caller should pause the task.
    """
    for opt in checkpoint.options:
        if opt.is_safe_default:
            return opt.value
    return None


async def wait_for_interaction(
    interaction: InteractionRequest,
    session: ProgressSession,
    surface: str,
    *,
    timeout_override: float | None = None,
) -> tuple[str | None, bool]:
    """Wait for user response at a checkpoint, with timeout handling.

    Returns ``(response_value, timed_out)``.

    * ``advisory_checkpoint`` — renders and returns immediately (auto-proceed).
    * ``required_clarification`` — renders with a surface-specific timeout.
      On timeout, returns the safe-default value if one exists, otherwise
      ``(None, True)`` so the caller can pause the task.
    """
    if interaction.kind == "advisory_checkpoint":
        response = await session.renderer.checkpoint(interaction.checkpoint)
        return response, False

    timeout = timeout_override if timeout_override is not None else _clarification_timeout(surface)
    if timeout == float("inf"):
        response = await session.renderer.checkpoint(interaction.checkpoint)
        return response, False

    try:
        response = await asyncio.wait_for(
            session.renderer.checkpoint(interaction.checkpoint),
            timeout=timeout,
        )
        return response, False
    except asyncio.TimeoutError:
        safe = handle_checkpoint_timeout(interaction.checkpoint)
        return safe, True


# ---------------------------------------------------------------------------
# Task 5-4: Result filtering
# ---------------------------------------------------------------------------

RESULT_FILTER_PROMPT_TEMPLATE = """\
The user selected detail level "{selection}" for the following result.
Their original question was: "{original_question}"

Full content:
{full_content}

Return only the portion matching the requested detail level.
"""


_FILTER_STOPWORDS = frozenset({
    "what", "about", "which", "where", "when", "that", "this", "these",
    "those", "from", "with", "have", "does", "will", "would", "could",
    "should", "been", "being", "into", "than", "then", "them", "they",
    "their", "there", "here", "some", "more", "most", "also", "just",
    "only", "very", "much", "many", "each", "every", "other", "such",
    "your", "were", "show",
})


def filter_result(
    full_content: str,
    user_selection: str,
    original_question: str = "",
) -> str:
    """Filter *full_content* based on the user's selected detail level.

    Deterministic fallback — an LLM layer can wrap this for richer filtering.
    """
    sel = user_selection.strip().lower()
    if sel == "full":
        return full_content
    if sel == "summary":
        cutoff = min(500, len(full_content))
        return full_content[:cutoff] + ("..." if len(full_content) > cutoff else "")
    if sel == "focus":
        if original_question:
            keywords = {
                w.lower().rstrip("?.,!:;") for w in original_question.split()
                if len(w) > 3 and w.lower().rstrip("?.,!:;") not in _FILTER_STOPWORDS
            }
            keywords.discard("")
            if keywords:
                lines = full_content.splitlines()
                relevant = [ln for ln in lines if any(kw in ln.lower() for kw in keywords)]
                if relevant:
                    return "\n".join(relevant[:30])
        return full_content[:500] + ("..." if len(full_content) > 500 else "")
    return full_content


async def apply_result_filter(
    full_content: str,
    user_selection: str,
    original_question: str = "",
    *,
    llm_fn: Callable[[str], Coroutine[Any, Any, str]] | None = None,
) -> str:
    """Apply detail-level filtering with optional LLM enhancement.

    If *llm_fn* is provided and succeeds, uses LLM to format the output.
    Otherwise falls back to the deterministic ``filter_result()``.
    """
    if user_selection.strip().lower() == "full":
        return full_content

    if llm_fn is not None:
        try:
            prompt = RESULT_FILTER_PROMPT_TEMPLATE.format(
                selection=user_selection,
                original_question=original_question,
                full_content=full_content[:4000],
            )
            return await llm_fn(prompt)
        except Exception:
            logger.debug("LLM result filter failed, using deterministic fallback", exc_info=True)

    return filter_result(full_content, user_selection, original_question)


# ---------------------------------------------------------------------------
# Chat command handler
# ---------------------------------------------------------------------------

_DEFAULT_OVERRIDE_SCOPE = "__default__"
_user_verbosity_overrides: dict[str, VerbosityLevel] = {}


def handle_progress_command(text: str, surface_id: str | None = None) -> str:
    """Handle ``/progress [full|compact|minimal]`` to show or set verbosity."""
    scope = surface_id or _DEFAULT_OVERRIDE_SCOPE

    parts = text.strip().split()
    if len(parts) >= 2:
        level = parts[1].lower()
        if level in ("full", "compact", "minimal"):
            _user_verbosity_overrides[scope] = level  # type: ignore[assignment]
            return f"Progress verbosity set to **{level}**."
        return f"Unknown verbosity level '{level}'. Choose: full, compact, minimal."

    current = get_user_verbosity_override(surface_id) or "(auto per surface)"
    return f"Current progress verbosity: **{current}**."


def get_user_verbosity_override(surface_id: str | None = None) -> VerbosityLevel | None:
    """Return the user-set verbosity override, if any."""
    scope = surface_id or _DEFAULT_OVERRIDE_SCOPE
    return _user_verbosity_overrides.get(scope)


def reset_user_verbosity_override(surface_id: str | None = None) -> None:
    """Clear the user verbosity override (for testing)."""
    if surface_id is None:
        _user_verbosity_overrides.clear()
        return
    _user_verbosity_overrides.pop(surface_id, None)
