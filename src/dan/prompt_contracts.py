from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

PromptTrustLabel = Literal[
    "authoritative",
    "advisory",
    "retrieved",
    "historical",
    "user-preference",
]


def _clean_text(value: Any, *, limit: int | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if limit is not None:
        return text[:limit]
    return text


def _clean_string_list(values: Any, *, limit: int | None = None) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned = [str(item or "").strip() for item in values if str(item or "").strip()]
    if limit is not None:
        return cleaned[:limit]
    return cleaned


@dataclass
class PromptSlot:
    """Typed prompt slot carrying content plus a trust label."""

    content: str = ""
    trust_label: PromptTrustLabel = "authoritative"

    def is_empty(self) -> bool:
        return not self.content.strip()

    def append(self, content: str) -> None:
        text = str(content or "").strip()
        if not text:
            return
        current = self.content.strip()
        self.content = f"{current}\n\n{text}" if current else text

    def render(self, *, include_trust_label: bool = True) -> str:
        text = self.content.strip()
        if not text:
            return ""
        if not include_trust_label:
            return text
        label_line = f"Trust label: {self.trust_label}"
        lines = text.splitlines()
        if lines[0].startswith("## "):
            if len(lines) > 1 and lines[1].strip().lower().startswith("trust label:"):
                return text
            return "\n".join([lines[0], label_line, *lines[1:]])
        if lines[0].strip().lower().startswith("trust label:"):
            return text
        return f"{label_line}\n{text}"


@dataclass
class PromptEnvelope:
    """Typed concierge/chat prompt envelope flattened only at message assembly."""

    system_policy: PromptSlot = field(
        default_factory=lambda: PromptSlot(trust_label="authoritative")
    )
    stage_overlay: PromptSlot = field(
        default_factory=lambda: PromptSlot(trust_label="authoritative")
    )
    response_mode: PromptSlot = field(
        default_factory=lambda: PromptSlot(trust_label="authoritative")
    )
    capability_context: PromptSlot = field(
        default_factory=lambda: PromptSlot(trust_label="advisory")
    )
    memory_context: PromptSlot = field(
        default_factory=lambda: PromptSlot(trust_label="retrieved")
    )
    workflow_context_pack: PromptSlot = field(
        default_factory=lambda: PromptSlot(trust_label="authoritative")
    )
    attachment_context: PromptSlot = field(
        default_factory=lambda: PromptSlot(trust_label="authoritative")
    )
    prefetched_action_context: PromptSlot = field(
        default_factory=lambda: PromptSlot(trust_label="authoritative")
    )
    turn_constraints: PromptSlot = field(
        default_factory=lambda: PromptSlot(trust_label="authoritative")
    )
    output_contract: PromptSlot = field(
        default_factory=lambda: PromptSlot(trust_label="authoritative")
    )
    user_preference_context: PromptSlot = field(
        default_factory=lambda: PromptSlot(trust_label="user-preference")
    )
    historical_context: PromptSlot = field(
        default_factory=lambda: PromptSlot(trust_label="historical")
    )
    surface_context: PromptSlot = field(
        default_factory=lambda: PromptSlot(trust_label="advisory")
    )
    mcp_context: PromptSlot = field(
        default_factory=lambda: PromptSlot(trust_label="advisory")
    )

    _ORDERED_SLOT_NAMES: tuple[str, ...] = (
        "system_policy",
        "stage_overlay",
        "response_mode",
        "capability_context",
        "memory_context",
        "workflow_context_pack",
        "attachment_context",
        "prefetched_action_context",
        "turn_constraints",
        "output_contract",
        "user_preference_context",
        "historical_context",
        "surface_context",
        "mcp_context",
    )
    _EXTRA_SYSTEM_SLOT_NAMES: tuple[str, ...] = (
        "stage_overlay",
        "response_mode",
        "attachment_context",
        "prefetched_action_context",
        "workflow_context_pack",
        "turn_constraints",
        "output_contract",
    )
    _SYSTEM_APPENDIX_SLOT_NAMES: tuple[str, ...] = (
        "user_preference_context",
        "mcp_context",
        "memory_context",
        * _EXTRA_SYSTEM_SLOT_NAMES,
    )

    def slot(self, name: str) -> PromptSlot:
        slot = getattr(self, name, None)
        if not isinstance(slot, PromptSlot):
            raise AttributeError(f"Unknown prompt slot '{name}'")
        return slot

    def append_to_slot(self, name: str, content: str) -> None:
        self.slot(name).append(content)

    def iter_slots(self, names: tuple[str, ...] | None = None) -> list[tuple[str, PromptSlot]]:
        ordered_names = names or self._ORDERED_SLOT_NAMES
        return [(name, self.slot(name)) for name in ordered_names]

    def render_slots(
        self,
        names: tuple[str, ...] | None = None,
        *,
        include_trust_labels: bool = True,
    ) -> str:
        sections = [
            slot.render(include_trust_label=include_trust_labels)
            for _, slot in self.iter_slots(names)
            if not slot.is_empty()
        ]
        return "\n\n".join(section for section in sections if section)

    def render_extra_system_instructions(self) -> str:
        return self.render_slots(self._EXTRA_SYSTEM_SLOT_NAMES)

    def render_system_appendix(self) -> str:
        return self.render_slots(self._SYSTEM_APPENDIX_SLOT_NAMES)

    def to_legacy_fields(self) -> tuple[str, str]:
        return self.system_policy.content.strip(), self.render_extra_system_instructions()


@dataclass
class TurnExecutionEnvelope:
    """Typed execution wrapper for a single concierge turn."""

    prompt_envelope: PromptEnvelope = field(default_factory=PromptEnvelope)
    dispatch_mode: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    capability_surface: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChildHandoffContext:
    recent_turns: list[str] = field(default_factory=list)
    task_snapshot: str = ""
    repo_snapshot: str = ""
    memory_context: str = ""
    domain_expertise: str = ""
    file_refs: list[str] = field(default_factory=list)
    file_snippets: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.recent_turns:
            payload["recent_turns"] = list(self.recent_turns)
        if self.task_snapshot:
            payload["task_snapshot"] = self.task_snapshot
        if self.repo_snapshot:
            payload["repo_snapshot"] = self.repo_snapshot
        if self.memory_context:
            payload["memory_context"] = self.memory_context
        if self.domain_expertise:
            payload["domain_expertise"] = self.domain_expertise
        if self.file_refs:
            payload["file_refs"] = list(self.file_refs)
        if self.file_snippets:
            payload["file_snippets"] = dict(self.file_snippets)
        return payload

    @classmethod
    def from_payload(cls, payload: Any) -> ChildHandoffContext:
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, dict):
            return cls()
        file_snippets = payload.get("file_snippets")
        return cls(
            recent_turns=_clean_string_list(payload.get("recent_turns"), limit=4),
            task_snapshot=_clean_text(payload.get("task_snapshot"), limit=2000),
            repo_snapshot=_clean_text(payload.get("repo_snapshot"), limit=2000),
            memory_context=_clean_text(payload.get("memory_context"), limit=4000),
            domain_expertise=_clean_text(payload.get("domain_expertise"), limit=4000),
            file_refs=_clean_string_list(payload.get("file_refs"), limit=6),
            file_snippets=(
                {
                    str(path or "").strip(): _clean_text(content, limit=2000)
                    for path, content in dict(file_snippets).items()
                    if str(path or "").strip() and _clean_text(content, limit=2000)
                }
                if isinstance(file_snippets, dict)
                else {}
            ),
        )


@dataclass
class ChildHandoffEnvelope:
    """Typed child-session handoff contract shared by concierge child turns."""

    parent_task: str = ""
    delegated_task: str = ""
    route_target: str = ""
    action_hints: list[str] = field(default_factory=list)
    parent_thread_id: str = ""
    definition_of_done: str = ""
    expected_return_shape: str = ""
    context: ChildHandoffContext = field(default_factory=ChildHandoffContext)
    constraints: dict[str, Any] = field(default_factory=dict)
    return_channel: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        goal: dict[str, Any] = {}
        if self.parent_task:
            goal["parent_task"] = self.parent_task
        if self.delegated_task:
            goal["delegated_task"] = self.delegated_task
        if self.route_target:
            goal["route_target"] = self.route_target
        if self.action_hints:
            goal["action_hints"] = list(self.action_hints)
        if goal:
            payload["goal"] = goal
        context_payload = self.context.to_dict()
        if context_payload:
            payload["context"] = context_payload
        if self.constraints:
            payload["constraints"] = dict(self.constraints)
        if self.return_channel:
            payload["return_channel"] = dict(self.return_channel)
        if self.parent_thread_id:
            payload["parent_thread_id"] = self.parent_thread_id
        if self.route_target:
            payload["route_target"] = self.route_target
        if self.action_hints:
            payload["action_hints"] = list(self.action_hints)
        if self.definition_of_done:
            payload["definition_of_done"] = self.definition_of_done
        if self.expected_return_shape:
            payload["expected_return_shape"] = self.expected_return_shape
        return payload

    @classmethod
    def from_payload(cls, payload: Any) -> ChildHandoffEnvelope:
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, dict):
            return cls()
        goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else {}
        return cls(
            parent_task=_clean_text(
                goal.get("parent_task") or payload.get("parent_task"),
                limit=400,
            ),
            delegated_task=_clean_text(
                goal.get("delegated_task") or payload.get("child_task"),
                limit=400,
            ),
            route_target=_clean_text(
                goal.get("route_target") or payload.get("route_target"),
                limit=120,
            ),
            action_hints=_clean_string_list(
                goal.get("action_hints") or payload.get("action_hints"),
                limit=6,
            ),
            parent_thread_id=_clean_text(payload.get("parent_thread_id"), limit=120),
            definition_of_done=_clean_text(payload.get("definition_of_done"), limit=400),
            expected_return_shape=_clean_text(payload.get("expected_return_shape"), limit=400),
            context=ChildHandoffContext.from_payload(payload.get("context")),
            constraints=(
                dict(payload.get("constraints"))
                if isinstance(payload.get("constraints"), dict)
                else {}
            ),
            return_channel=(
                dict(payload.get("return_channel"))
                if isinstance(payload.get("return_channel"), dict)
                else {}
            ),
        )

    def render_prompt_block(self) -> str:
        lines: list[str] = []
        if self.parent_task:
            lines.append(f"Parent task: {self.parent_task}")
        if self.delegated_task:
            lines.append(f"Delegated task: {self.delegated_task}")
        if self.route_target:
            lines.append(f"Route target: {self.route_target}")
        if self.action_hints:
            lines.append("Action hints: " + ", ".join(self.action_hints[:6]))
        if self.definition_of_done:
            lines.append(f"Definition of done: {self.definition_of_done}")
        if self.expected_return_shape:
            lines.append(f"Expected return shape: {self.expected_return_shape}")

        context = self.context
        if context.recent_turns:
            lines.append("Recent task turns:")
            lines.extend(context.recent_turns[:4])
        if context.task_snapshot:
            lines.append(f"Task snapshot:\n{context.task_snapshot}")
        if context.repo_snapshot:
            lines.append(f"Repo snapshot:\n{context.repo_snapshot}")
        if context.memory_context:
            lines.append(f"Relevant memory:\n{context.memory_context}")
        if context.domain_expertise:
            lines.append(f"Relevant domain expertise:\n{context.domain_expertise}")
        if context.file_refs:
            lines.append("Related files: " + ", ".join(context.file_refs[:6]))
        if context.file_snippets:
            snippets: list[str] = []
            for path, content in list(context.file_snippets.items())[:3]:
                snippets.append(f"[{path}]\n{content}")
            if snippets:
                lines.append("Relevant file content:\n" + "\n\n".join(snippets))

        if self.constraints:
            constraint_bits: list[str] = []
            if self.constraints.get("read_only_parent_context") is True:
                constraint_bits.append("parent context is read-only")
            if self.constraints.get("copy_on_write_metadata") is True:
                constraint_bits.append("child metadata is isolated")
            if self.constraints.get("allow_mutation_tool") is not None:
                constraint_bits.append(
                    "allow_mutation_tool="
                    + ("true" if self.constraints.get("allow_mutation_tool") else "false")
                )
            if constraint_bits:
                lines.append("Constraints: " + ", ".join(constraint_bits))

        if self.return_channel:
            return_kind = _clean_text(self.return_channel.get("kind"), limit=120)
            parent_session_id = _clean_text(
                self.return_channel.get("parent_session_id"),
                limit=120,
            )
            if return_kind or parent_session_id:
                pieces = [piece for piece in [return_kind, parent_session_id] if piece]
                lines.append("Return channel: " + " -> ".join(pieces))

        if not lines:
            return ""
        return "Child handoff:\n" + "\n".join(lines)


__all__ = [
    "ChildHandoffContext",
    "ChildHandoffEnvelope",
    "PromptEnvelope",
    "PromptSlot",
    "PromptTrustLabel",
    "TurnExecutionEnvelope",
]
