"""Computer control safety policy — config, classification, allowlists, audit.

Provides the policy layer for 31-17 computer control & browser automation:
chunked capability policies (observe/browser/input/window/files/system),
domain/app allowlists, action classification, approval requirements,
session overrides, and an in-memory audit log.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action classification
# ---------------------------------------------------------------------------

ActionType = Literal[
    "read_only",
    "benign_input",
    "sensitive_input",
    "destructive",
    "system_level",
]

_ACTION_MAP: dict[str, ActionType] = {
    "screenshot": "read_only",
    "ocr": "read_only",
    "list_windows": "read_only",
    "list_tabs": "read_only",
    "extract_text": "read_only",
    "clipboard_read": "read_only",
    "open": "benign_input",
    "click": "benign_input",
    "scroll": "benign_input",
    "switch_tab": "benign_input",
    "focus_window": "benign_input",
    "wait_for": "read_only",
    "type_text": "sensitive_input",
    "fill": "sensitive_input",
    "select": "sensitive_input",
    "clipboard_write": "sensitive_input",
    "hotkey": "sensitive_input",
    "download": "destructive",
    "delete": "destructive",
    "submit": "destructive",
    "close": "destructive",
    "launch_app": "system_level",
    "shell": "system_level",
    "install": "system_level",
}

_DESTRUCTIVE_TARGETS = re.compile(
    r"(delete|remove|submit|purchase|send|pay|checkout|confirm.*(order|payment))",
    re.IGNORECASE,
)


def classify_action(
    action_name: str,
    target: str | None = None,
) -> ActionType:
    """Classify a computer-use action by risk level."""
    base = _ACTION_MAP.get(action_name, "benign_input")
    if target and _DESTRUCTIVE_TARGETS.search(target):
        if base in ("read_only", "benign_input", "sensitive_input"):
            return "destructive"
    return base


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class BrowserDomainRule(BaseModel):
    """Single domain allowlist entry for the browser chunk."""

    pattern: str
    includes_subdomains: bool = True
    allow_redirect_targets: bool = True
    allow_downloads: bool = True


class ChunkPolicy(BaseModel):
    """Policy for a single capability chunk."""

    enabled: bool = True
    allowed_apps: list[str] = Field(default_factory=list)
    allowed_domains: list[BrowserDomainRule] = Field(default_factory=list)


class ChunkPolicies(BaseModel):
    """Per-chunk policies for the six capability areas."""

    observe: ChunkPolicy = Field(default_factory=lambda: ChunkPolicy(enabled=True))
    browser: ChunkPolicy = Field(default_factory=ChunkPolicy)
    input: ChunkPolicy = Field(default_factory=lambda: ChunkPolicy(enabled=False))
    window: ChunkPolicy = Field(default_factory=lambda: ChunkPolicy(enabled=False))
    files: ChunkPolicy = Field(default_factory=ChunkPolicy)
    system: ChunkPolicy = Field(default_factory=lambda: ChunkPolicy(enabled=False))


class VisionExportPolicy(BaseModel):
    """Controls whether screenshots may be sent to external vision models.

    Disabled by default.  When enabled, PII protection and redaction are
    required to mitigate data-leak risk.  The ``DAN_PII_PROTECTION``
    environment variable must be set to ``1`` for export to be allowed.
    """

    enabled: bool = False
    require_pii_protection: bool = True
    require_redaction: bool = True


def check_vision_export(config: "ComputerControlConfig") -> bool:
    """Return True if vision export (sending screenshots to external LLMs) is allowed.

    Blocked when:
    - ``VisionExportPolicy.enabled`` is False (default), or
    - ``require_pii_protection`` is True but ``DAN_PII_PROTECTION`` != ``1``, or
    - ``require_redaction`` is True (v1 always blocks because local redaction
      is not yet implemented — follow-on slice).
    """
    policy = config.vision_export
    if not policy.enabled:
        return False
    if policy.require_pii_protection:
        if os.environ.get("DAN_PII_PROTECTION", "0").strip() != "1":
            return False
    if policy.require_redaction:
        # v1: local redaction/cropping is not yet implemented, so this
        # always blocks when require_redaction=True (the default).
        # Follow-on: once pre-send redaction is implemented, this check
        # will verify the redaction pipeline is active instead of blocking.
        return False
    return True


# ---------------------------------------------------------------------------
# File safety policy (Task 6-6)
# ---------------------------------------------------------------------------


class FileSafetyPolicy(BaseModel):
    """File-level safety rules for computer-use downloads and uploads.

    Restricts where files may be saved/read and applies cleanup policies
    to prevent unbounded disk growth from screenshots and temp files.
    """

    allowed_download_dirs: list[str] = Field(
        default_factory=lambda: ["~/Downloads", "~/.dan/downloads"],
    )
    allowed_upload_roots: list[str] = Field(
        default_factory=lambda: ["~/Documents", "~/Desktop"],
    )
    require_overwrite_confirmation: bool = True
    auto_open_downloads: bool = False
    screenshot_ttl_hours: float = 24.0
    temp_crop_ttl_hours: float = 1.0
    download_ttl_hours: float = 0.0  # 0 = no auto-cleanup


class ComputerControlConfig(BaseModel):
    """Top-level computer control configuration."""

    enabled: bool = False
    foreground_only: bool = True
    chunk_policies: ChunkPolicies = Field(default_factory=ChunkPolicies)
    vision_export: VisionExportPolicy = Field(default_factory=VisionExportPolicy)
    file_safety: FileSafetyPolicy = Field(default_factory=FileSafetyPolicy)

    @classmethod
    def load(cls) -> ComputerControlConfig:
        """Load config from ``~/.dan/computer_control.json`` with env overrides."""
        env_override = os.environ.get("DAN_COMPUTER_CONTROL")
        config_path = os.path.expanduser("~/.dan/computer_control.json")

        config = cls()
        if os.path.isfile(config_path):
            try:
                with open(config_path) as f:
                    data = json.load(f)
                config = cls.model_validate(data)
            except Exception:
                logger.warning("Failed to load %s, using defaults", config_path)

        if env_override is not None:
            config.enabled = env_override.strip().lower() in ("1", "true", "yes")

        return config


# ---------------------------------------------------------------------------
# Session overrides
# ---------------------------------------------------------------------------


class SessionOverride(BaseModel):
    """Temporary per-session approval or denial for a capability chunk."""

    chunk: str
    granted: bool
    expires_at: datetime | None = None


# ---------------------------------------------------------------------------
# Approval logic
# ---------------------------------------------------------------------------


def requires_approval(
    action_type: ActionType,
    config: ComputerControlConfig,
    session_overrides: list[SessionOverride] | None = None,
) -> bool:
    """Return True if *action_type* requires user approval under *config*."""
    if action_type == "read_only":
        return False
    if action_type in ("destructive", "system_level"):
        if session_overrides:
            now = datetime.now(timezone.utc)
            for ov in session_overrides:
                if ov.granted and (ov.expires_at is None or ov.expires_at > now):
                    if ov.chunk == "system" and action_type == "system_level":
                        return False
                    if ov.chunk == "destructive" and action_type == "destructive":
                        return False
        return True
    if action_type == "sensitive_input":
        if session_overrides:
            now = datetime.now(timezone.utc)
            for ov in session_overrides:
                if ov.granted and ov.chunk == "input" and (
                    ov.expires_at is None or ov.expires_at > now
                ):
                    return False
        return True
    return False


# ---------------------------------------------------------------------------
# Domain / app allowlist helpers
# ---------------------------------------------------------------------------


def is_domain_allowed(url: str, rules: list[BrowserDomainRule]) -> bool:
    """Check whether *url* is allowed by any of the domain *rules*."""
    if not rules:
        return True
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
    except Exception:
        return False
    if not hostname:
        return False
    for rule in rules:
        pattern = rule.pattern.lower()
        if hostname == pattern:
            return True
        if rule.includes_subdomains and hostname.endswith("." + pattern):
            return True
    return False


def is_app_allowed(app_name: str, chunk: ChunkPolicy) -> bool:
    """Check whether *app_name* is in the chunk's allowed-apps list.

    An empty list means "all apps allowed".
    """
    if not chunk.allowed_apps:
        return True
    lower = app_name.lower()
    return any(a.lower() == lower for a in chunk.allowed_apps)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """Single audit record for a computer-use action."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    action: str
    target: str | None = None
    action_type: ActionType = "benign_input"
    approved: bool = True
    result: Literal["success", "failed", "denied"] = "success"


class AuditLog:
    """In-memory audit log for computer-use actions."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def add(self, entry: AuditEntry) -> None:
        self._entries.append(entry)

    def recent(self, n: int = 20) -> list[AuditEntry]:
        return self._entries[-n:]

    def __len__(self) -> int:
        return len(self._entries)

    def format_summary(self) -> str:
        entries = self.recent()
        if not entries:
            return "No computer-use actions recorded."
        lines = [f"Last {len(entries)} actions:"]
        for e in entries:
            status = e.result
            ts = e.timestamp.strftime("%H:%M:%S")
            target_str = f" -> {e.target}" if e.target else ""
            lines.append(f"  [{ts}] {e.action}{target_str} ({e.action_type}) [{status}]")
        return "\n".join(lines)
