"""Surface request contract shared by the Diane control plane."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class ChatMentionRef(BaseModel):
    type: str
    identifier: str


class ChatMessageRequest(BaseModel):
    workflow_id: str
    message: str
    thread_id: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list)
    client_graph_revision: str | None = None
    mode: str = "agent"
    mentions: list[ChatMentionRef] = Field(default_factory=list)
    surface: str | None = None
    surface_type: str | None = None
    surface_id: str | None = None
    session_id: str | None = None
    attachment_path: str | None = None
    surface_context: dict[str, Any] = Field(default_factory=dict)
    control_plane_mode: str | None = None

    @model_validator(mode="after")
    def normalize_surface_request(self) -> "ChatMessageRequest":
        self.thread_id = (self.thread_id or self.session_id or "").strip() or None
        self.session_id = (self.session_id or self.thread_id or "").strip() or None

        surface_type = (self.surface_type or "").strip()
        surface_id = (self.surface_id or "").strip()
        surface = (self.surface or "").strip()
        if surface and ":" in surface:
            parsed_type, parsed_id = surface.split(":", 1)
            surface_type = surface_type or parsed_type
            surface_id = surface_id or parsed_id
        if bool(surface_type) != bool(surface_id):
            raise ValueError("surface_type and surface_id must be provided together")
        if surface_type and surface_id:
            canonical = f"{surface_type}:{surface_id}"
            if surface and surface != canonical:
                raise ValueError("surface must match surface_type and surface_id")
            self.surface = canonical
            self.surface_type = surface_type
            self.surface_id = surface_id

        self.history = [
            {"role": str(item.get("role")), "content": str(item.get("content"))}
            for item in self.history
            if item.get("role") in {"user", "assistant"}
            and str(item.get("content") or "").strip()
        ]
        return self
