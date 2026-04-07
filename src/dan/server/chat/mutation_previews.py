"""Chat-thread mutation preview state helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def persist_latest_mutation_preview(
    chat_store: Any,
    workflow_id: str,
    thread_id: str | None,
    *,
    message_id: str,
    mutation_plan: dict[str, Any],
    dry_run_result: dict[str, Any],
) -> None:
    if chat_store is None or not thread_id:
        return
    try:
        meta = chat_store.get_thread_meta(workflow_id, thread_id)
        if not isinstance(meta, dict):
            meta = {}
        meta["latest_mutation_preview"] = {
            "message_id": message_id,
            "mutation_plan": mutation_plan,
            "dry_run_result": dry_run_result,
        }
        chat_store.set_thread_meta(workflow_id, thread_id, meta)
    except Exception:
        logger.debug("Failed to persist latest mutation preview", exc_info=True)


def clear_latest_mutation_preview(
    chat_store: Any,
    workflow_id: str,
    thread_id: str | None,
) -> None:
    if chat_store is None or not thread_id:
        return
    try:
        meta = chat_store.get_thread_meta(workflow_id, thread_id)
        if not isinstance(meta, dict) or "latest_mutation_preview" not in meta:
            return
        meta.pop("latest_mutation_preview", None)
        chat_store.set_thread_meta(workflow_id, thread_id, meta)
    except Exception:
        logger.debug("Failed to clear latest mutation preview", exc_info=True)


def has_latest_mutation_preview(
    chat_store: Any,
    workflow_id: str,
    thread_id: str | None,
) -> bool:
    if chat_store is None or not thread_id:
        return False
    try:
        thread = chat_store.get_thread(workflow_id, thread_id)
    except Exception:
        thread = None
    if thread is not None:
        for msg in reversed(getattr(thread, "messages", []) or []):
            mutation_plan = getattr(msg, "mutation_plan", None)
            if not isinstance(mutation_plan, dict) or not mutation_plan:
                continue
            if getattr(msg, "mutation_status", None) in (None, "proposed"):
                return True
    try:
        meta = chat_store.get_thread_meta(workflow_id, thread_id)
    except Exception:
        meta = {}
    preview = meta.get("latest_mutation_preview") if isinstance(meta, dict) else None
    return isinstance(preview, dict) and isinstance(preview.get("mutation_plan"), dict)


def get_latest_mutation_preview(
    chat_store: Any,
    workflow_id: str,
    thread_id: str | None,
    *,
    message_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if chat_store is None or not thread_id:
        return None, "No chat thread context is available for applying a workflow preview."

    target_message_id = str(message_id or "").strip() or None
    thread = None
    try:
        thread = chat_store.get_thread(workflow_id, thread_id)
    except Exception:
        thread = None

    if thread is not None:
        saw_thread_mutation = False
        for msg in reversed(getattr(thread, "messages", []) or []):
            if target_message_id and getattr(msg, "id", None) != target_message_id:
                continue
            mutation_plan = getattr(msg, "mutation_plan", None)
            if not isinstance(mutation_plan, dict) or not mutation_plan:
                continue
            saw_thread_mutation = True
            status = getattr(msg, "mutation_status", None)
            if status not in (None, "proposed"):
                continue
            return {
                "message_id": getattr(msg, "id", None),
                "mutation_plan": mutation_plan,
                "dry_run_result": getattr(msg, "dry_run_result", None),
            }, None
        if target_message_id:
            return None, f"No proposed workflow preview with message_id `{target_message_id}` was found in this chat."
        if saw_thread_mutation:
            return None, (
                "This chat does not currently have a proposed workflow preview to apply. "
                "The latest preview may already be applied or rejected."
            )

    try:
        meta = chat_store.get_thread_meta(workflow_id, thread_id)
    except Exception:
        meta = {}
    preview = meta.get("latest_mutation_preview") if isinstance(meta, dict) else None
    if isinstance(preview, dict) and isinstance(preview.get("mutation_plan"), dict):
        preview_message_id = str(preview.get("message_id") or "").strip() or None
        if target_message_id and preview_message_id != target_message_id:
            return None, f"No proposed workflow preview with message_id `{target_message_id}` was found in this chat."
        return {
            "message_id": preview_message_id,
            "mutation_plan": preview.get("mutation_plan"),
            "dry_run_result": preview.get("dry_run_result"),
        }, None

    return None, (
        "No proposed workflow preview is available in this chat yet. "
        "Ask me to build or modify the workflow first."
    )


def mark_mutation_preview_applied(
    chat_store: Any,
    workflow_id: str,
    thread_id: str | None,
    *,
    message_id: str | None,
) -> None:
    if chat_store is None or not thread_id:
        return
    try:
        thread = chat_store.get_thread(workflow_id, thread_id)
        if thread is not None:
            changed = False
            for msg in reversed(getattr(thread, "messages", []) or []):
                if message_id and getattr(msg, "id", None) != message_id:
                    continue
                mutation_plan = getattr(msg, "mutation_plan", None)
                if not isinstance(mutation_plan, dict) or not mutation_plan:
                    continue
                if getattr(msg, "mutation_status", None) in (None, "proposed"):
                    msg.mutation_status = "applied"
                    changed = True
                    break
            if changed:
                thread.updated_at = datetime.now(timezone.utc)
                chat_store.save_thread(thread)
    except Exception:
        logger.debug("Failed to mark mutation preview as applied", exc_info=True)

    try:
        meta = chat_store.get_thread_meta(workflow_id, thread_id)
        if isinstance(meta, dict):
            preview = meta.get("latest_mutation_preview")
            preview_message_id = (
                str(preview.get("message_id") or "").strip()
                if isinstance(preview, dict)
                else ""
            )
            if not message_id or not preview_message_id or preview_message_id == message_id:
                meta.pop("latest_mutation_preview", None)
                chat_store.set_thread_meta(workflow_id, thread_id, meta)
    except Exception:
        logger.debug("Failed to clear latest mutation preview metadata", exc_info=True)
