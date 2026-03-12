"""Helpers for auto-generated chat thread titles."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from dan.providers.registry import ProviderRegistry
from dan.providers.tier_defaults import resolve_tier_map

from .chat_store import ChatStore, ChatThread

logger = logging.getLogger(__name__)

_FALLBACK_TITLE_CHARS = 50
_GENERATED_TITLE_CHARS = 60
_TITLE_SYSTEM_PROMPT = (
    "Write a very short chat thread title from the user's first message only. "
    "Use 2 to 6 words when possible. Be concrete, not generic. "
    "Do not use quotes, markdown, prefixes, or ending punctuation."
)


def _first_user_message(thread: ChatThread) -> str:
    for msg in thread.messages:
        if msg.role == "user":
            return " ".join(msg.content.split()).strip()
    return ""


def _truncate_title(text: str, max_chars: int = _FALLBACK_TITLE_CHARS) -> str:
    clean = " ".join(text.split()).strip()
    if not clean:
        return "New chat"
    if len(clean) <= max_chars:
        return clean
    cutoff = max_chars - 3
    prefix = clean[:cutoff].rstrip()
    split_at = prefix.rfind(" ")
    if split_at > 0:
        prefix = prefix[:split_at].rstrip()
    return prefix + "..."


def _sanitize_generated_title(text: str, max_chars: int = _GENERATED_TITLE_CHARS) -> str:
    clean = " ".join(text.split()).strip()
    clean = clean.strip("\"'`")
    clean = re.sub(r"^(title|thread title)\s*:\s*", "", clean, flags=re.IGNORECASE)
    clean = clean.strip(" \t\r\n-–—:;,.")
    if len(clean) > max_chars:
        clean = clean[:max_chars].rstrip(" -–—:;,.")
    return clean or ""


def ensure_fallback_title(
    store: ChatStore,
    workflow_id: str,
    thread_id: str,
) -> str | None:
    thread = store.get_thread(workflow_id, thread_id)
    if thread is None:
        return None

    meta = store.get_thread_meta(workflow_id, thread_id)
    title_source = str(meta.get("title_source", "") or "").strip()
    if title_source in {"manual", "generated"} and thread.title.strip():
        return thread.title

    first_user = _first_user_message(thread)
    if not first_user:
        return None

    fallback = _truncate_title(first_user)
    if thread.title != fallback:
        thread.title = fallback
        thread.updated_at = datetime.now(timezone.utc)
        store.save_thread(thread)

    meta["title_source"] = "fallback"
    meta.setdefault("title_generation_started", False)
    store.set_thread_meta(workflow_id, thread_id, meta)
    return fallback


def mark_manual_title(
    store: ChatStore,
    workflow_id: str,
    thread_id: str,
    title: str,
) -> bool:
    thread = store.get_thread(workflow_id, thread_id)
    if thread is None:
        return False

    clean = " ".join(title.split()).strip()
    thread.title = clean
    thread.updated_at = datetime.now(timezone.utc)
    store.save_thread(thread)

    meta = store.get_thread_meta(workflow_id, thread_id)
    if clean:
        meta["title_source"] = "manual"
        meta["title_locked"] = True
    else:
        meta.pop("title_source", None)
        meta.pop("title_locked", None)
    meta["title_generation_started"] = False
    meta.pop("title_generation_failed", None)
    store.set_thread_meta(workflow_id, thread_id, meta)
    return True


def apply_generated_title(
    store: ChatStore,
    workflow_id: str,
    thread_id: str,
    title: str,
) -> bool:
    thread = store.get_thread(workflow_id, thread_id)
    if thread is None:
        return False

    meta = store.get_thread_meta(workflow_id, thread_id)
    if meta.get("title_locked") or meta.get("title_source") == "manual":
        meta["title_generation_started"] = False
        store.set_thread_meta(workflow_id, thread_id, meta)
        return False

    clean = _sanitize_generated_title(title)
    if not clean:
        meta["title_generation_started"] = False
        meta["title_generation_failed"] = True
        store.set_thread_meta(workflow_id, thread_id, meta)
        return False

    thread.title = clean
    thread.updated_at = datetime.now(timezone.utc)
    store.save_thread(thread)

    meta["title_source"] = "generated"
    meta["title_generation_started"] = False
    meta.pop("title_generation_failed", None)
    store.set_thread_meta(workflow_id, thread_id, meta)
    return True


class ThreadTitleGenerator:
    """Generate compact thread titles using the cheapest available model."""

    def __init__(
        self,
        providers: ProviderRegistry | None,
        *,
        chat_model: str = "",
    ) -> None:
        self._providers = providers
        self._chat_model = (chat_model or "").strip()

    def resolve_model(self) -> str:
        if self._providers is None:
            return self._chat_model or "gpt-4o-mini"

        provider_names = self._providers.provider_names()
        if set(provider_names) == {"default"} and self._chat_model:
            return self._chat_model

        try:
            tier_map = resolve_tier_map(provider_names)
            micro_model = str(tier_map.get("micro", "") or "").strip()
            if micro_model:
                return micro_model
        except Exception:
            logger.debug("Falling back to chat model for title generation", exc_info=True)

        return self._chat_model or "gpt-4o-mini"

    async def generate_title(self, user_message: str) -> str | None:
        clean_user_message = " ".join(user_message.split()).strip()
        if not clean_user_message or self._providers is None:
            return None

        model = self.resolve_model()
        try:
            provider = self._providers.resolve(model)
            result = await provider.complete(
                messages=[
                    {"role": "system", "content": _TITLE_SYSTEM_PROMPT},
                    {"role": "user", "content": clean_user_message},
                ],
                model=model,
                temperature=0.0,
                max_tokens=24,
            )
        except Exception:
            logger.debug("Thread title generation failed", exc_info=True)
            return None

        title = _sanitize_generated_title(getattr(result, "text", "") or "")
        return title or None


async def autogenerate_thread_title(
    store: ChatStore,
    workflow_id: str,
    thread_id: str,
    *,
    providers: ProviderRegistry | None,
    chat_model: str = "",
    mark_started: bool = True,
) -> bool:
    thread = store.get_thread(workflow_id, thread_id)
    if thread is None:
        return False

    meta = store.get_thread_meta(workflow_id, thread_id)
    if meta.get("title_locked") or meta.get("title_source") == "manual":
        meta["title_generation_started"] = False
        store.set_thread_meta(workflow_id, thread_id, meta)
        return False

    first_user = _first_user_message(thread)
    if not first_user:
        meta["title_generation_started"] = False
        store.set_thread_meta(workflow_id, thread_id, meta)
        return False

    ensure_fallback_title(store, workflow_id, thread_id)

    meta = store.get_thread_meta(workflow_id, thread_id)
    if meta.get("title_source") == "generated":
        meta["title_generation_started"] = False
        store.set_thread_meta(workflow_id, thread_id, meta)
        return True
    if mark_started:
        if meta.get("title_generation_started"):
            return False
        meta["title_generation_started"] = True
        store.set_thread_meta(workflow_id, thread_id, meta)

    generator = ThreadTitleGenerator(providers, chat_model=chat_model)
    title = await generator.generate_title(first_user)
    if not title:
        meta = store.get_thread_meta(workflow_id, thread_id)
        meta["title_generation_started"] = False
        meta["title_generation_failed"] = True
        store.set_thread_meta(workflow_id, thread_id, meta)
        return False

    return apply_generated_title(store, workflow_id, thread_id, title)
