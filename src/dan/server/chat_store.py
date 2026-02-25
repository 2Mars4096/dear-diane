"""Filesystem-based chat persistence — stores chat threads per workflow."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

__all__ = [
    "Mention",
    "RunRef",
    "ChatMessage",
    "ChatThread",
    "ChatStore",
]


class Mention(BaseModel):
    name: str
    type: str
    id: str


class RunRef(BaseModel):
    run_id: str
    scope: str
    status: str
    target_node_id: str | None = None
    target_subgraph_key: str | None = None


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    role: Literal["user", "assistant", "system"]
    content: str
    mentions: list[Mention] = Field(default_factory=list)
    mutation_plan: dict[str, Any] | None = None
    mutation_id: str | None = None
    mutation_status: Literal[
        "proposed", "applied", "partial", "rejected", "reverted"
    ] | None = None
    token_usage: dict[str, int] | None = None
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    run_ref: RunRef | None = None


class ChatThread(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    workflow_id: str
    title: str = ""
    messages: list[ChatMessage] = Field(default_factory=list)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ChatStore:
    """CRUD operations for chat threads on disk.

    Storage layout:
        {base_dir}/chats/{workflow_id}/{thread_id}.json
    """

    def __init__(self, base_dir: str = "./graphs") -> None:
        self.base_dir = Path(base_dir)

    def _chats_dir(self, workflow_id: str) -> Path:
        d = self.base_dir / "chats" / workflow_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _thread_path(self, workflow_id: str, thread_id: str) -> Path:
        return self._chats_dir(workflow_id) / f"{thread_id}.json"

    def list_threads(self, workflow_id: str) -> list[dict[str, Any]]:
        chats_dir = self.base_dir / "chats" / workflow_id
        if not chats_dir.exists():
            return []
        results: list[dict[str, Any]] = []
        for p in sorted(chats_dir.glob("*.json")):
            try:
                thread = ChatThread.model_validate_json(
                    p.read_text(encoding="utf-8")
                )
                results.append({
                    "id": thread.id,
                    "title": thread.title,
                    "workflow_id": thread.workflow_id,
                    "message_count": len(thread.messages),
                    "created_at": thread.created_at.isoformat(),
                    "updated_at": thread.updated_at.isoformat(),
                })
            except (ValueError, OSError):
                continue
        return results

    def get_thread(
        self, workflow_id: str, thread_id: str
    ) -> ChatThread | None:
        path = self._thread_path(workflow_id, thread_id)
        if not path.exists():
            return None
        try:
            return ChatThread.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (ValueError, OSError):
            return None

    def create_thread(
        self, workflow_id: str, title: str = ""
    ) -> ChatThread:
        thread = ChatThread(workflow_id=workflow_id, title=title)
        self.save_thread(thread)
        return thread

    def save_thread(self, thread: ChatThread) -> None:
        path = self._thread_path(thread.workflow_id, thread.id)
        path.write_text(
            thread.model_dump_json(indent=2), encoding="utf-8"
        )

    def append_message(
        self, workflow_id: str, thread_id: str, message: ChatMessage
    ) -> ChatThread | None:
        thread = self.get_thread(workflow_id, thread_id)
        if thread is None:
            return None
        thread.messages.append(message)
        thread.updated_at = datetime.now(timezone.utc)
        self.save_thread(thread)
        return thread

    def update_thread_title(
        self, workflow_id: str, thread_id: str, title: str
    ) -> bool:
        thread = self.get_thread(workflow_id, thread_id)
        if thread is None:
            return False
        thread.title = title
        thread.updated_at = datetime.now(timezone.utc)
        self.save_thread(thread)
        return True

    def delete_thread(self, workflow_id: str, thread_id: str) -> bool:
        path = self._thread_path(workflow_id, thread_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def delete_all_threads(self, workflow_id: str) -> int:
        chats_dir = self.base_dir / "chats" / workflow_id
        if not chats_dir.exists():
            return 0
        count = 0
        for p in chats_dir.glob("*.json"):
            p.unlink()
            count += 1
        if not any(chats_dir.iterdir()):
            chats_dir.rmdir()
        return count

    def auto_title(self, thread: ChatThread) -> str:
        for msg in thread.messages:
            if msg.role == "user":
                text = msg.content.strip()
                if len(text) > 50:
                    return text[:50] + "..."
                return text
        return "New chat"
