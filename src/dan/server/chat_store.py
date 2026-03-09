"""Filesystem-based chat persistence — stores chat threads per workflow."""

from __future__ import annotations

import json
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
    dry_run_result: dict[str, Any] | None = None
    mutation_id: str | None = None
    mutation_status: Literal[
        "proposed", "applied", "partial", "rejected", "reverted"
    ] | None = None
    token_usage: dict[str, int] | None = None
    estimated_cost: float | None = None
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

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------

    def _checkpoints_dir(self, workflow_id: str) -> Path:
        d = self.base_dir / "chats" / workflow_id / "checkpoints"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_checkpoint(
        self,
        workflow_id: str,
        thread_id: str,
        message_id: str,
        graph_snapshot: dict[str, Any],
    ) -> str:
        """Save a graph state checkpoint associated with a chat message.

        Returns the checkpoint filename.
        """
        cp_dir = self._checkpoints_dir(workflow_id)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        filename = f"{thread_id}_{message_id}_{ts}.json"
        payload = {
            "thread_id": thread_id,
            "message_id": message_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "graph_snapshot": graph_snapshot,
        }
        (cp_dir / filename).write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        logger.debug("Saved checkpoint %s for thread %s", filename, thread_id)
        return filename

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_thread_markdown(
        self, workflow_id: str, thread_id: str
    ) -> str | None:
        """Export a thread as readable Markdown."""
        thread = self.get_thread(workflow_id, thread_id)
        if thread is None:
            return None
        title = thread.title or "Untitled Chat"
        lines: list[str] = [f"# {title}\n"]
        for msg in thread.messages:
            ts = msg.timestamp.strftime("%Y-%m-%d %H:%M UTC")
            role_label = msg.role.capitalize()
            lines.append(f"### {role_label}  \n*{ts}*\n")
            lines.append(msg.content + "\n")
            if msg.mutation_plan and msg.mutation_status:
                lines.append(
                    f"> Mutation: {msg.mutation_status}"
                    f" — {(msg.mutation_plan or {}).get('description', '')}\n"
                )
        return "\n".join(lines)

    def export_thread_json(
        self, workflow_id: str, thread_id: str
    ) -> dict[str, Any] | None:
        """Export full thread data as JSON dict."""
        thread = self.get_thread(workflow_id, thread_id)
        if thread is None:
            return None
        return thread.model_dump(mode="json")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_threads(
        self, query: str, workflow_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Search message content across threads (case-insensitive substring)."""
        query_lower = query.lower()
        results: list[dict[str, Any]] = []
        search_dirs: list[Path] = []
        chats_root = self.base_dir / "chats"
        if workflow_id:
            wf_dir = chats_root / workflow_id
            if wf_dir.exists():
                search_dirs.append(wf_dir)
        elif chats_root.exists():
            search_dirs.extend(
                d for d in chats_root.iterdir()
                if d.is_dir() and d.name != "checkpoints"
            )
        for wf_dir in search_dirs:
            for p in wf_dir.glob("*.json"):
                try:
                    thread = ChatThread.model_validate_json(
                        p.read_text(encoding="utf-8")
                    )
                except (ValueError, OSError):
                    continue
                for msg in thread.messages:
                    if query_lower in msg.content.lower():
                        preview = msg.content[:120].replace("\n", " ")
                        results.append({
                            "thread_id": thread.id,
                            "thread_title": thread.title or "Untitled",
                            "workflow_id": thread.workflow_id,
                            "message_id": msg.id,
                            "message_preview": preview,
                            "timestamp": msg.timestamp.isoformat(),
                        })
        return results

    # ------------------------------------------------------------------
    # Pin support
    # ------------------------------------------------------------------

    def _meta_path(self, workflow_id: str, thread_id: str) -> Path:
        return self._chats_dir(workflow_id) / f"{thread_id}.meta.json"

    def get_thread_meta(
        self, workflow_id: str, thread_id: str
    ) -> dict[str, Any]:
        path = self._meta_path(workflow_id, thread_id)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def set_thread_meta(
        self, workflow_id: str, thread_id: str, meta: dict[str, Any]
    ) -> None:
        path = self._meta_path(workflow_id, thread_id)
        path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    _VALID_MODES = {"ask", "agent", "plan", "debug", "auto"}
    _MODE_ALIASES = {"build": "agent", "mutate": "agent"}

    @classmethod
    def _normalize_mode(cls, mode: str | None) -> str:
        """Normalize stored mode to a canonical value, defaulting to 'agent'."""
        if not mode:
            return "agent"
        mode = cls._MODE_ALIASES.get(mode, mode)
        return mode if mode in cls._VALID_MODES else "agent"

    def set_mode(
        self, workflow_id: str, thread_id: str, mode: str
    ) -> bool:
        thread = self.get_thread(workflow_id, thread_id)
        if thread is None:
            return False
        meta = self.get_thread_meta(workflow_id, thread_id)
        meta["mode"] = self._normalize_mode(mode)
        self.set_thread_meta(workflow_id, thread_id, meta)
        return True

    def set_pinned(
        self, workflow_id: str, thread_id: str, pinned: bool
    ) -> bool:
        thread = self.get_thread(workflow_id, thread_id)
        if thread is None:
            return False
        meta = self.get_thread_meta(workflow_id, thread_id)
        meta["pinned"] = pinned
        self.set_thread_meta(workflow_id, thread_id, meta)
        return True

    def list_threads(self, workflow_id: str) -> list[dict[str, Any]]:
        """List thread summaries, including pin status from metadata."""
        chats_dir = self.base_dir / "chats" / workflow_id
        if not chats_dir.exists():
            return []
        results: list[dict[str, Any]] = []
        for p in sorted(chats_dir.glob("*.json")):
            if p.name.endswith(".meta.json"):
                continue
            try:
                thread = ChatThread.model_validate_json(
                    p.read_text(encoding="utf-8")
                )
                meta = self.get_thread_meta(workflow_id, thread.id)
                results.append({
                    "id": thread.id,
                    "title": thread.title,
                    "workflow_id": thread.workflow_id,
                    "message_count": len(thread.messages),
                    "created_at": thread.created_at.isoformat(),
                    "updated_at": thread.updated_at.isoformat(),
                    "pinned": meta.get("pinned", False),
                    "mode": self._normalize_mode(meta.get("mode")),
                })
            except (ValueError, OSError):
                continue
        return results
