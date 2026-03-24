from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def store_extracted_preferences(
    *,
    memory_kernel: Any,
    behavior_store: Any,
    user_message: str,
    assistant_message: str,
    project_id: str | None = None,
) -> None:
    """Persist preference signals without making runtime own engine imports."""
    if memory_kernel is None:
        return
    if os.environ.get("DAN_PREFERENCE_EXTRACTION", "1").strip() != "1":
        return

    try:
        from dan.domain_taxonomy import format_domain_label
        from dan.engine.preference_extractor import PreferenceExtractor
        from .domain_learning import get_domain_keyword_map

        def _domain_kw() -> dict[str, list[str]]:
            return get_domain_keyword_map(behavior_store)

        extractor = PreferenceExtractor(
            behavior_store=behavior_store,
            domain_keywords_provider=_domain_kw,
        )
        messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ]
        prefs = extractor.extract_from_messages(messages)
        if not prefs:
            return
        for key, value in prefs.items():
            if not value:
                continue
            if isinstance(value, dict):
                for sub_key, sub_val in value.items():
                    memory_kernel.store_preference(
                        content=f"{key}: {sub_key} -> {sub_val}",
                        tags=[key, sub_key],
                        project_id=project_id,
                    )
            elif isinstance(value, list):
                for entry in value:
                    content_value = (
                        format_domain_label(entry)
                        if key == "domains"
                        else str(entry)
                    )
                    memory_kernel.store_preference(
                        content=f"{key}: {content_value}",
                        tags=[key],
                        project_id=project_id,
                    )
            elif isinstance(value, str) and value:
                memory_kernel.store_preference(
                    content=f"{key}: {value}",
                    tags=[key],
                    project_id=project_id,
                )
    except Exception:
        logger.debug("Preference extraction failed", exc_info=True)


def remember_search_dirs_from_candidates(
    *,
    user_profile: Any,
    candidates: list[Any],
) -> None:
    """Persist search-directory facts extracted from memory candidates."""
    if user_profile is None or not hasattr(user_profile, "merge_search_dirs"):
        return

    directories: list[str] = []
    for candidate in candidates:
        if getattr(candidate, "memory_type", None) != "fact":
            continue
        candidate_tags = list(getattr(candidate, "tags", None) or [])
        candidate_metadata = dict(getattr(candidate, "metadata", None) or {})
        if not (
            candidate_metadata.get("is_directory")
            or "search_dir" in candidate_tags
        ):
            continue
        path_value = str(candidate_metadata.get("path", "") or "").strip()
        if not path_value and ":" in str(getattr(candidate, "content", "")):
            path_value = str(candidate.content).split(":", 1)[1].strip()
        if path_value:
            directories.append(path_value)
    if not directories:
        return

    try:
        changed = bool(user_profile.merge_search_dirs(directories))
        if changed:
            from dan.engine.user_profile import save_user_profile

            save_user_profile(user_profile)
    except Exception:
        logger.debug("Failed to persist extracted search directories", exc_info=True)


def store_extracted_memories(
    *,
    memory_kernel: Any,
    user_profile: Any,
    user_message: str,
    assistant_message: str,
    goal_context: dict[str, Any] | None = None,
    project_id: str | None = None,
    domain: str | None = None,
    main_loop: asyncio.AbstractEventLoop | None = None,
    run_coroutine_sync: Callable[..., Any],
) -> None:
    """Run LLM-backed memory extraction outside the main runtime module."""
    if memory_kernel is None:
        return
    if os.environ.get("DAN_MEMORY_EXTRACTION", "1").strip() != "1":
        return

    try:
        from dan.engine.memory_extractor import MemoryExtractor

        extractor = MemoryExtractor()
        tool_activity = (goal_context or {}).get("metadata", {}).get("tool_calls")
        candidates = run_coroutine_sync(
            extractor.extract_with_llm(
                user_message=user_message,
                assistant_message=assistant_message,
                tool_calls=tool_activity if isinstance(tool_activity, list) else None,
                goal_context=goal_context,
            ),
            loop=main_loop,
        )
        remember_search_dirs_from_candidates(
            user_profile=user_profile,
            candidates=candidates,
        )
        for candidate in candidates:
            candidate_tags = list(candidate.tags or [])
            candidate_metadata = dict(candidate.metadata or {})
            if domain:
                if "domain_knowledge" not in candidate_tags:
                    candidate_tags.append("domain_knowledge")
                candidate_metadata.setdefault("domain", domain)
            if candidate.memory_type == "fact":
                memory_kernel.store_fact(
                    candidate.content,
                    tags=candidate_tags,
                    project_id=project_id,
                    metadata=candidate_metadata,
                )
            elif candidate.memory_type == "preference":
                memory_kernel.store_preference(
                    candidate.content,
                    tags=candidate_tags,
                    project_id=project_id,
                    metadata=candidate_metadata,
                )
    except Exception:
        logger.debug("Memory extraction failed", exc_info=True)
