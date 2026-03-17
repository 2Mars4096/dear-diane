"""Adapters to bridge existing DAN memory stores into the unified MemoryKernel.

Each adapter reads from an existing store and converts items to MemoryItem format.
These enable incremental migration: the kernel wraps existing stores rather than
replacing them immediately.

Part of Phase 19 (plan 29-1, task 5).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from dan.engine.domain_taxonomy import format_domain_label, normalize_domain_name
from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryType,
    MemoryScope,
    MemoryLifecycle,
    MemoryKernel,
    Provenance,
)

logger = logging.getLogger(__name__)


class ExperienceAdapter:
    """Converts WorkflowExperience objects to WORKFLOW_ASSET MemoryItems."""

    @staticmethod
    def to_memory_item(experience: Any) -> MemoryItem:
        desc_parts = [experience.name or experience.workflow_id]
        if experience.description:
            desc_parts.append(experience.description)
        if experience.tags:
            desc_parts.append(f"Tags: {', '.join(experience.tags)}")
        if experience.node_types_used:
            desc_parts.append(f"Nodes: {', '.join(experience.node_types_used)}")
        if experience.tools_used:
            desc_parts.append(f"Tools: {', '.join(experience.tools_used)}")

        success_rate = (
            experience.success_count / experience.run_count
            if experience.run_count > 0 else 0.0
        )

        return MemoryItem(
            id=f"exp:{experience.workflow_id}",
            content="\n".join(desc_parts),
            memory_type=MemoryType.WORKFLOW_ASSET,
            scope=MemoryScope.USER,
            lifecycle=MemoryLifecycle.DURABLE,
            importance=min(0.3 + success_rate * 0.5 + min(experience.run_count / 20, 0.2), 1.0),
            created_at=getattr(experience, "created_at", time.time()),
            updated_at=getattr(experience, "updated_at", time.time()),
            access_count=experience.run_count,
            tags=list(experience.tags) if experience.tags else [],
            metadata={
                "workflow_id": experience.workflow_id,
                "success_rate": success_rate,
                "run_count": experience.run_count,
                "success_count": experience.success_count,
                "avg_elapsed": experience.avg_elapsed_seconds,
                "avg_cost": experience.avg_total_cost,
                "success_patterns": experience.success_patterns[:5] if experience.success_patterns else [],
                "failure_patterns": experience.failure_patterns[:5] if experience.failure_patterns else [],
            },
        )

    @staticmethod
    async def import_all(experience_store: Any, kernel: MemoryKernel) -> int:
        count = 0
        try:
            experiences = await experience_store.list_experiences()
            for exp in experiences:
                item = ExperienceAdapter.to_memory_item(exp)
                kernel.store(item)
                count += 1
        except Exception:
            logger.warning("Failed to import experiences", exc_info=True)
        return count


class ProfileAdapter:
    """Converts UserProfile fields to PREFERENCE and FACT MemoryItems."""

    @staticmethod
    def to_memory_items(profile: Any) -> list[MemoryItem]:
        items: list[MemoryItem] = []
        now = time.time()

        if profile.preferred_models:
            for task, model in profile.preferred_models.items():
                items.append(MemoryItem(
                    id=f"pref:model:{task}",
                    content=f"Preferred model for {task}: {model}",
                    memory_type=MemoryType.PREFERENCE,
                    scope=MemoryScope.USER,
                    lifecycle=MemoryLifecycle.DURABLE,
                    importance=0.8,
                    created_at=now,
                    provenance=Provenance(confirmed_by_user=True),
                    tags=["model_preference", task],
                ))

        if profile.preferred_output_format:
            items.append(MemoryItem(
                id="pref:output_format",
                content=f"Preferred output format: {profile.preferred_output_format}",
                memory_type=MemoryType.PREFERENCE,
                scope=MemoryScope.USER,
                lifecycle=MemoryLifecycle.DURABLE,
                importance=0.7,
                created_at=now,
                provenance=Provenance(confirmed_by_user=True),
                tags=["output_format"],
            ))

        for domain in (profile.common_domains or []):
            canonical_domain = normalize_domain_name(domain) or domain
            items.append(MemoryItem(
                id=f"fact:domain:{canonical_domain}",
                content=f"User works in domain: {format_domain_label(canonical_domain)}",
                memory_type=MemoryType.FACT,
                scope=MemoryScope.USER,
                lifecycle=MemoryLifecycle.DURABLE,
                importance=0.6,
                created_at=now,
                tags=["domain", canonical_domain],
            ))

        for d in (profile.search_dirs or []):
            items.append(MemoryItem(
                id=f"fact:search_dir:{hash(d) & 0xFFFF:04x}",
                content=f"Frequently used directory: {d}",
                memory_type=MemoryType.FACT,
                scope=MemoryScope.USER,
                lifecycle=MemoryLifecycle.DURABLE,
                importance=0.5,
                created_at=now,
                tags=["search_dir"],
            ))

        return items

    @staticmethod
    def import_profile(profile: Any, kernel: MemoryKernel) -> int:
        items = ProfileAdapter.to_memory_items(profile)
        if items:
            kernel.store_many(items)
        return len(items)


class ConversationAdapter:
    """Converts ConversationSummary objects to EPISODE MemoryItems."""

    @staticmethod
    def to_memory_item(summary: Any) -> MemoryItem:
        return MemoryItem(
            id=f"conv:{summary.id}",
            content=summary.summary,
            memory_type=MemoryType.EPISODE,
            scope=MemoryScope.USER,
            lifecycle=MemoryLifecycle.DURABLE,
            importance=0.4,
            created_at=summary.timestamp,
            updated_at=summary.timestamp,
            last_accessed=summary.timestamp,
            tags=list(summary.topic_tags) if summary.topic_tags else [],
            metadata={"workflow_id": summary.workflow_id} if summary.workflow_id else {},
        )

    @staticmethod
    def import_all(conv_store: Any, kernel: MemoryKernel) -> int:
        count = 0
        try:
            entries = conv_store.get_recent(200)
            for entry in entries:
                item = ConversationAdapter.to_memory_item(entry)
                kernel.store(item)
                count += 1
        except Exception:
            logger.warning("Failed to import conversation memory", exc_info=True)
        return count


class PrincipleAdapter:
    """Converts CausalPrinciple objects to PRINCIPLE MemoryItems."""

    @staticmethod
    def to_memory_item(principle: Any) -> MemoryItem:
        content = f"IF {principle.condition} THEN {principle.action}"
        if principle.reason:
            content += f" BECAUSE {principle.reason}"

        return MemoryItem(
            id=f"prin:{principle.id[:12]}",
            content=content,
            memory_type=MemoryType.PRINCIPLE,
            scope=MemoryScope.GLOBAL if not principle.workflow_id else MemoryScope.WORKFLOW,
            lifecycle=MemoryLifecycle.DURABLE,
            importance=principle.confidence,
            created_at=principle.created_at,
            updated_at=principle.updated_at,
            tags=list(principle.tags) if principle.tags else [],
            metadata={
                "confidence": principle.confidence,
                "repair_level": principle.repair_level,
                "workflow_id": principle.workflow_id,
                "source_run_ids": principle.source_run_ids[:5],
            },
        )

    @staticmethod
    async def import_all(principle_store: Any, workflow_ids: list[str], kernel: MemoryKernel) -> int:
        count = 0
        for wf_id in workflow_ids:
            try:
                principles = await principle_store.load_principles(wf_id)
                for p in principles:
                    item = PrincipleAdapter.to_memory_item(p)
                    kernel.store(item)
                    count += 1
            except Exception:
                logger.warning("Failed to import principles for %s", wf_id, exc_info=True)
        return count


class ErrorAdapter:
    """Converts ErrorRecord objects to FAILURE_PATTERN MemoryItems."""

    @staticmethod
    def to_memory_item(record: Any) -> MemoryItem:
        content = f"[{record.error_category}] {record.error_message}"
        if record.node_type:
            content = f"Node type '{record.node_type}': {content}"

        return MemoryItem(
            id=f"err:{record.run_id[:8]}:{record.node_id[:8]}",
            content=content,
            memory_type=MemoryType.FAILURE_PATTERN,
            scope=MemoryScope.WORKFLOW if record.workflow_id else MemoryScope.GLOBAL,
            lifecycle=MemoryLifecycle.ACTIVE,
            importance=0.5 + (0.2 if record.severity == "high" else 0.0),
            created_at=record.timestamp if hasattr(record, "timestamp") else time.time(),
            tags=[record.error_category, record.node_type] if record.node_type else [record.error_category],
            metadata={
                "workflow_id": record.workflow_id,
                "node_id": record.node_id,
                "run_id": record.run_id,
                "severity": getattr(record, "severity", "medium"),
            },
        )


async def import_all_to_kernel(
    kernel: MemoryKernel,
    experience_store: Any | None = None,
    profile: Any | None = None,
    conversation_store: Any | None = None,
    principle_store: Any | None = None,
    workflow_ids: list[str] | None = None,
) -> dict[str, int]:
    """Bulk import from all existing stores into the kernel."""
    counts: dict[str, int] = {}

    if experience_store:
        counts["experiences"] = await ExperienceAdapter.import_all(experience_store, kernel)

    if profile:
        counts["profile"] = ProfileAdapter.import_profile(profile, kernel)

    if conversation_store:
        counts["conversations"] = ConversationAdapter.import_all(conversation_store, kernel)

    if principle_store and workflow_ids:
        counts["principles"] = await PrincipleAdapter.import_all(
            principle_store, workflow_ids, kernel
        )

    logger.info("Imported into memory kernel: %s", counts)
    return counts
