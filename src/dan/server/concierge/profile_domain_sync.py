"""Persist and synchronize saved profile domains through engine-backed stores."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def sync_profile_domain_memory(user_profile: Any, memory_kernel: Any = None) -> None:
    if memory_kernel is None:
        return
    try:
        from dan.engine.memory_adapters import ProfileAdapter
        from dan.engine.memory_kernel import MemoryScope, MemoryType

        existing_domain_ids = {
            item.id
            for item in memory_kernel.list_by_type(
                MemoryType.FACT,
                scope=MemoryScope.USER,
                limit=500,
            )
            if str(getattr(item, "id", "")).startswith("fact:domain:")
        }
        desired_items = [
            item
            for item in ProfileAdapter.to_memory_items(user_profile)
            if item.id.startswith("fact:domain:")
        ]
        desired_ids = {item.id for item in desired_items}
        for stale_id in existing_domain_ids - desired_ids:
            memory_kernel.delete(stale_id, hard=True)
        if desired_items:
            memory_kernel.store_many(desired_items)
    except Exception:
        logger.debug("Failed to sync profile domains into memory kernel", exc_info=True)


def persist_profile_domains(user_profile: Any, memory_kernel: Any = None) -> None:
    from dan.engine.user_profile import save_user_profile

    save_user_profile(user_profile)
    sync_profile_domain_memory(user_profile, memory_kernel)
