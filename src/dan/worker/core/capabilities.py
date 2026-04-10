"""Capability manifest models and helpers for progressive tool loading."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CapabilityCostTier(str, Enum):
    """Coarse execution/cost hint for capability selection."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CapabilitySummary(BaseModel):
    """Compact discovery-time view of one capability."""

    capability_id: str
    title: str
    purpose: str = ""
    family: str = "other"
    permission_scope: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    cost_tier: CapabilityCostTier = CapabilityCostTier.MEDIUM
    retry_safe: bool | None = None
    idempotent: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityManifest(CapabilitySummary):
    """Expanded manifest with the detail needed to safely call a capability."""

    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    output_contract: str = ""
    examples: list[Any] = Field(default_factory=list)
    details: str = ""
    governance_notes: list[str] = Field(default_factory=list)

    def compact(self) -> CapabilitySummary:
        return CapabilitySummary.model_validate(self.model_dump(mode="json"))

    def to_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.capability_id,
                "description": self.purpose or self.details or self.title,
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }


_READ_ONLY_TOOL_IDS = {
    "audio_transcribe",
    "browser_extract",
    "browser_screenshot",
    "browser_wait",
    "clipboard",
    "csv_read",
    "current_datetime",
    "file_read",
    "git_diff",
    "git_log",
    "git_status",
    "image_describe",
    "json_extract",
    "list_directory",
    "pdf_read",
    "regex_match",
    "spreadsheet_read",
    "text_chunk",
    "text_diff",
    "text_translate",
    "web_fetch",
    "web_search",
}
_FILESYSTEM_WRITE_TOOL_IDS = {
    "compress",
    "file_copy",
    "file_delete",
    "file_edit",
    "file_move",
    "file_write",
    "git_worktree",
}
_PROCESS_TOOL_IDS = {
    "git_branch",
    "git_commit",
    "python_eval",
    "shell_command",
}
_NETWORK_TOOL_IDS = {
    "browser_click",
    "browser_download",
    "browser_fill",
    "browser_open",
    "browser_type",
    "http_request",
    "send_email",
    "web_fetch",
    "web_search",
}
_BROWSER_TOOL_IDS = {
    "browser_click",
    "browser_download",
    "browser_extract",
    "browser_fill",
    "browser_open",
    "browser_screenshot",
    "browser_type",
    "browser_wait",
}


def capability_manifest_from_descriptor(tool_id: str, descriptor: dict[str, Any] | None = None) -> CapabilityManifest:
    """Normalize raw tool metadata or light descriptors into a typed manifest."""

    descriptor = dict(descriptor or {})
    category = str(descriptor.get("category") or descriptor.get("family") or "other").strip() or "other"
    purpose = str(descriptor.get("summary") or descriptor.get("description") or "").strip()
    details = str(descriptor.get("details") or descriptor.get("description") or purpose).strip()
    return CapabilityManifest(
        capability_id=tool_id,
        title=str(descriptor.get("title") or descriptor.get("name") or tool_id),
        purpose=purpose,
        family=category,
        permission_scope=_derive_permission_scope(tool_id, category),
        side_effects=_derive_side_effects(tool_id, category),
        cost_tier=_derive_cost_tier(tool_id, category),
        retry_safe=_derive_retry_safe(tool_id),
        idempotent=_derive_idempotent(tool_id),
        input_schema=_normalize_schema(descriptor.get("parameters")),
        output_contract=str(descriptor.get("returns") or descriptor.get("output_contract") or "").strip(),
        examples=list(descriptor.get("examples") or []),
        details=details,
        governance_notes=_derive_governance_notes(tool_id, category),
        metadata={
            key: value
            for key, value in descriptor.items()
            if key
            not in {
                "id",
                "name",
                "title",
                "summary",
                "description",
                "details",
                "category",
                "family",
                "parameters",
                "returns",
                "output_contract",
                "examples",
            }
        },
    )


def _normalize_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {"type": "object", "properties": {}}


def _derive_permission_scope(tool_id: str, category: str) -> list[str]:
    scopes: list[str] = []
    if tool_id in _READ_ONLY_TOOL_IDS or category in {"file", "web", "text"}:
        scopes.append("read")
    if tool_id in _FILESYSTEM_WRITE_TOOL_IDS:
        scopes.append("filesystem:write")
    if tool_id in _PROCESS_TOOL_IDS:
        scopes.append("process:execute")
    if tool_id in _NETWORK_TOOL_IDS or category == "web":
        scopes.append("network:read")
    if tool_id == "send_email":
        scopes.append("network:write")
    if tool_id in _BROWSER_TOOL_IDS:
        scopes.append("browser:automate")
    if tool_id.startswith("git_"):
        scopes.append("git")
    deduped: list[str] = []
    seen: set[str] = set()
    for scope in scopes:
        if scope not in seen:
            seen.add(scope)
            deduped.append(scope)
    return deduped


def _derive_side_effects(tool_id: str, category: str) -> list[str]:
    side_effects: list[str] = []
    if tool_id in _FILESYSTEM_WRITE_TOOL_IDS:
        side_effects.append("filesystem")
    if tool_id in _NETWORK_TOOL_IDS or category == "web":
        side_effects.append("network")
    if tool_id in _PROCESS_TOOL_IDS:
        side_effects.append("process")
    if tool_id in _BROWSER_TOOL_IDS:
        side_effects.append("browser")
    if tool_id == "send_email":
        side_effects.append("external_delivery")
    if tool_id.startswith("git_"):
        side_effects.append("git")
    return side_effects


def _derive_cost_tier(tool_id: str, category: str) -> CapabilityCostTier:
    if tool_id in _PROCESS_TOOL_IDS or tool_id in _BROWSER_TOOL_IDS or tool_id == "send_email":
        return CapabilityCostTier.HIGH
    if tool_id in _NETWORK_TOOL_IDS or category in {"web", "browser"}:
        return CapabilityCostTier.MEDIUM
    return CapabilityCostTier.LOW


def _derive_retry_safe(tool_id: str) -> bool | None:
    if tool_id in _READ_ONLY_TOOL_IDS:
        return True
    if tool_id in _PROCESS_TOOL_IDS or tool_id in _FILESYSTEM_WRITE_TOOL_IDS or tool_id == "send_email":
        return False
    return None


def _derive_idempotent(tool_id: str) -> bool | None:
    if tool_id in _READ_ONLY_TOOL_IDS:
        return True
    if tool_id in {"file_edit", "file_write", "file_delete", "send_email", "git_commit"}:
        return False
    return None


def _derive_governance_notes(tool_id: str, category: str) -> list[str]:
    notes: list[str] = []
    if tool_id in _READ_ONLY_TOOL_IDS:
        notes.append("read-mostly")
    if tool_id in _NETWORK_TOOL_IDS or category == "web":
        notes.append("external-network-dependency")
    if tool_id in _PROCESS_TOOL_IDS:
        notes.append("host-process-execution")
    if tool_id in _FILESYSTEM_WRITE_TOOL_IDS:
        notes.append("mutates-workspace-state")
    if tool_id == "send_email":
        notes.append("irreversible-external-side-effect")
    return notes
