from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from .models import ConciergeState

logger = logging.getLogger(__name__)

_CONCIERGE_STATE_PREFIX = "concierge_state_"


class MemoryServices(Protocol):
    """Engine-backed memory operations consumed by concierge runtime."""

    def load_concierge_state(
        self,
        scope_id: str,
        *,
        legacy_scope_ids: list[str] | None = None,
        volatile_states: dict[str, ConciergeState] | None = None,
    ) -> ConciergeState: ...

    def save_concierge_state(
        self,
        scope_id: str,
        state: ConciergeState,
        *,
        volatile_states: dict[str, ConciergeState] | None = None,
    ) -> None: ...

    def apply_project_scope(self, items: list[Any], project_id: str | None) -> None: ...

    def list_domain_upgrade_items(self, domain: str) -> list[Any]: ...

    def retrieve_memory_context(
        self,
        message: str,
        *,
        has_active_build: bool = False,
        project_id: str | None = None,
        max_chars: int = 1500,
    ) -> str: ...

    def retrieve_domain_expertise(
        self,
        query: str,
        domain: str,
        project_id: str | None = None,
        *,
        max_items: int = 8,
        max_chars: int = 1200,
    ) -> str: ...

    def extract_memory_candidates(
        self,
        message: str,
        response: str,
        goal_context: dict[str, Any] | None = None,
    ) -> list[Any]: ...

    def store_domain_validation_warnings(
        self,
        warnings: list[str],
        domain: str | None,
    ) -> None: ...


class KernelBackedMemoryServices:
    """Default concierge memory-services implementation over MemoryKernel."""

    def __init__(self, memory_kernel: Any) -> None:
        self._memory_kernel = memory_kernel

    def load_concierge_state(
        self,
        scope_id: str,
        *,
        legacy_scope_ids: list[str] | None = None,
        volatile_states: dict[str, ConciergeState] | None = None,
    ) -> ConciergeState:
        candidate_ids = [scope_id]
        for legacy_id in legacy_scope_ids or []:
            legacy_text = str(legacy_id or "").strip()
            if legacy_text and legacy_text not in candidate_ids:
                candidate_ids.append(legacy_text)

        if not self._memory_kernel:
            for candidate_id in candidate_ids:
                state = (volatile_states or {}).get(candidate_id)
                if state is not None:
                    return state.model_copy(deep=True)
            return ConciergeState()

        for candidate_id in candidate_ids:
            item_id = _CONCIERGE_STATE_PREFIX + candidate_id
            item = self._memory_kernel.get(item_id)
            if item is None:
                continue
            try:
                data = json.loads(item.content)
                return ConciergeState.model_validate(data)
            except Exception:
                logger.debug(
                    "Failed to parse concierge state for %s",
                    candidate_id,
                    exc_info=True,
                )
        return ConciergeState()

    def save_concierge_state(
        self,
        scope_id: str,
        state: ConciergeState,
        *,
        volatile_states: dict[str, ConciergeState] | None = None,
    ) -> None:
        if not self._memory_kernel:
            if volatile_states is not None:
                volatile_states[scope_id] = state.model_copy(deep=True)
            return

        from dan.engine.memory_kernel import MemoryItem, MemoryScope, MemoryType

        item_id = _CONCIERGE_STATE_PREFIX + scope_id
        item = MemoryItem(
            id=item_id,
            content=state.model_dump_json(),
            memory_type=MemoryType.WORKING_STATE,
            scope=MemoryScope.USER,
            metadata={"surface_id": scope_id},
        )
        self._memory_kernel.store(item)

    def apply_project_scope(self, items: list[Any], project_id: str | None) -> None:
        if not items or not project_id:
            return

        from dan.engine.memory_kernel import MemoryScope

        for item in items:
            item.scope = MemoryScope.PROJECT
            item.metadata = dict(item.metadata or {})
            item.metadata["project_id"] = project_id

    def list_domain_upgrade_items(self, domain: str) -> list[Any]:
        if not self._memory_kernel or not domain:
            return []

        from dan.engine.memory_kernel import MemoryType

        items: list[Any] = []
        for mem_type in (
            MemoryType.FACT,
            MemoryType.PREFERENCE,
            MemoryType.PRINCIPLE,
            MemoryType.WORKFLOW_PATTERN,
        ):
            items.extend(
                item
                for item in self._memory_kernel.list_by_type(mem_type)
                if "domain_knowledge" in (item.tags or [])
                and item.metadata.get("domain") == domain
            )
        return items

    def retrieve_memory_context(
        self,
        message: str,
        *,
        has_active_build: bool = False,
        project_id: str | None = None,
        max_chars: int = 1500,
    ) -> str:
        if not self._memory_kernel:
            return ""

        try:
            from dan.engine.memory_kernel import MemoryType, classify_task_type

            scored = self._memory_kernel.retrieve_by_task(
                message,
                task_type=classify_task_type(
                    message,
                    has_active_build=has_active_build,
                ),
                limit=10,
                project_id=project_id,
            )
            if not scored:
                return ""
            lines = ["Relevant context from memory:"]
            seen_contents: set[str] = set()
            for scored_item in scored[:8]:
                if scored_item.item.memory_type == MemoryType.WORKING_STATE:
                    continue
                content = scored_item.item.content[:200]
                if not content or content in seen_contents:
                    continue
                seen_contents.add(content)
                tag = scored_item.item.memory_type.value.upper()
                scope_hint = (
                    " (project)"
                    if scored_item.item.scope.value == "project"
                    else ""
                )
                lines.append(f"- [{tag}{scope_hint}] {content}")
            if len(lines) == 1:
                return ""
            block = "\n".join(lines)
            return block[:max_chars].rstrip() + ("..." if len(block) > max_chars else "")
        except Exception:
            logger.debug("Memory retrieval failed in concierge", exc_info=True)
            return ""

    def retrieve_domain_expertise(
        self,
        query: str,
        domain: str,
        project_id: str | None = None,
        *,
        max_items: int = 8,
        max_chars: int = 1200,
    ) -> str:
        if not self._memory_kernel or not domain:
            return ""

        try:
            from dan.engine.memory_kernel import MemoryScope, MemoryType, _TYPE_RANKERS

            type_rankers = {
                MemoryType.FACT: (_TYPE_RANKERS[MemoryType.FACT], 0.30),
                MemoryType.PREFERENCE: (_TYPE_RANKERS[MemoryType.PREFERENCE], 0.20),
                MemoryType.PRINCIPLE: (_TYPE_RANKERS[MemoryType.PRINCIPLE], 0.25),
                MemoryType.WORKFLOW_PATTERN: (
                    _TYPE_RANKERS[MemoryType.WORKFLOW_PATTERN],
                    0.25,
                ),
            }
            all_scored: list[tuple[float, Any]] = []
            for mem_type, (ranker, weight) in type_rankers.items():
                items = self._memory_kernel.list_by_type(mem_type)
                type_scored: list[tuple[float, Any]] = []
                for item in items:
                    if "domain_knowledge" not in (item.tags or []):
                        continue
                    if item.metadata.get("domain") != domain:
                        continue
                    if (
                        item.scope == MemoryScope.PROJECT
                        and item.metadata.get("project_id") != project_id
                    ):
                        continue
                    score = ranker(item, query) + 0.3
                    type_scored.append((min(score, 1.0), item))
                type_scored.sort(key=lambda value: value[0], reverse=True)
                if not type_scored:
                    continue
                type_limit = max(1, round(max_items * weight))
                all_scored.extend(type_scored[:type_limit])

            all_scored.sort(key=lambda value: value[0], reverse=True)
            if not all_scored:
                return ""
            lines = [f"[Domain Expertise: {domain}]"]
            for _score, item in all_scored[:max_items]:
                category = (item.metadata.get("category") or "general").upper()
                lines.append(f"- [{category}] {item.content[:200]}")
            block = "\n".join(lines)
            return block[:max_chars].rstrip() + ("..." if len(block) > max_chars else "")
        except Exception:
            logger.debug("Domain expertise retrieval failed", exc_info=True)
            return ""

    def extract_memory_candidates(
        self,
        message: str,
        response: str,
        goal_context: dict[str, Any] | None = None,
    ) -> list[Any]:
        if not self._memory_kernel:
            return []

        from dan.engine.memory_kernel import MemoryItem, MemoryScope, MemoryType

        candidates: list[MemoryItem] = []
        user_preview = " ".join(message.split()).strip()[:150]
        assistant_preview = " ".join(response.split()).strip()[:200]
        if user_preview:
            candidates.append(
                MemoryItem(
                    content=f"User: {user_preview}. Assistant: {assistant_preview}",
                    memory_type=MemoryType.EPISODE,
                    scope=MemoryScope.SESSION,
                )
            )
        if goal_context:
            errors = goal_context.get("error_history") or []
            description = goal_context.get("description", "")[:200]
            if errors and description:
                candidates.append(
                    MemoryItem(
                        content=(
                            f"Goal: {description}. "
                            f"Last error: {str(errors[-1])[:300]}"
                        ),
                        memory_type=MemoryType.FAILURE_PATTERN,
                        scope=MemoryScope.USER,
                        metadata=goal_context.get("metadata") or {},
                    )
                )
        return candidates

    def store_domain_validation_warnings(
        self,
        warnings: list[str],
        domain: str | None,
    ) -> None:
        if not self._memory_kernel or not warnings:
            return

        from dan.engine.memory_kernel import MemoryItem, MemoryScope, MemoryType

        for warning in warnings:
            self._memory_kernel.store(
                MemoryItem(
                    content=warning,
                    memory_type=MemoryType.FACT,
                    scope=MemoryScope.USER,
                    tags=["domain_validation_warning"],
                    metadata={"domain": domain or "unknown"},
                )
            )


def build_memory_services(*, memory_kernel: Any) -> MemoryServices:
    return KernelBackedMemoryServices(memory_kernel)
