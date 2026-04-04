from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dan.domain_taxonomy import normalize_domain_list, normalize_domain_name

logger = logging.getLogger(__name__)

# Use same DAN_DIR as cli/__init__.py
DAN_DIR = Path.home() / ".dan"
DEFAULT_PROFILE_PATH = DAN_DIR / "profile.json"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _normalize_search_dir(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    return str(Path(raw).expanduser())


def _normalize_common_domains(profile: "UserProfile") -> bool:
    normalized = normalize_domain_list(profile.common_domains)
    if normalized == list(profile.common_domains):
        return False
    profile.common_domains = normalized
    profile.updated_at = datetime.now(timezone.utc)
    return True


def resolve_profile_path(path: Path | None = None) -> Path:
    """Resolve the profile path, honoring DAN_PROFILE_PATH when set."""
    if path is not None:
        return Path(path).expanduser()
    env_path = str(os.environ.get("DAN_PROFILE_PATH", "") or "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_PROFILE_PATH


class RecentWorkflow(BaseModel):
    workflow_id: str
    opened_at: float = Field(default_factory=time.time)


class UserProfile(BaseModel):
    user_id: str = "local"
    display_name: str = ""
    preferred_timezone: str = ""
    preferred_models: dict[str, str] = Field(default_factory=dict)
    # e.g. {"drafting": "claude-sonnet-4-6", "review": "gpt-4o", "coding": "claude-sonnet-4-6"}
    preferred_output_format: str = ""  # "markdown", "json", "latex", or empty
    common_domains: list[str] = Field(default_factory=list)
    # e.g. ["paper_rendering", "equity_research"]
    model_overrides: dict[str, str] = Field(default_factory=dict)
    # per-workflow model preferences: {"workflow_id": "model_name"}
    action_policy_overrides: dict[str, str] = Field(default_factory=dict)
    search_dirs: list[str] = Field(default_factory=list)
    recent_workflows: list[RecentWorkflow] = Field(default_factory=list)
    session_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch_workflow(self, workflow_id: str) -> None:
        """Record a workflow as recently used. Keeps last 10, deduped."""
        self.recent_workflows = [
            rw for rw in self.recent_workflows if rw.workflow_id != workflow_id
        ]
        self.recent_workflows.insert(0, RecentWorkflow(workflow_id=workflow_id))
        self.recent_workflows = self.recent_workflows[:10]
        self.updated_at = datetime.now(timezone.utc)

    def increment_session(self) -> None:
        self.session_count += 1
        self.updated_at = datetime.now(timezone.utc)

    def merge_preferences(
        self,
        models: dict[str, str] | None = None,
        domains: list[str] | None = None,
        output_format: str | None = None,
    ) -> None:
        """Merge extracted preferences into profile without overwriting existing."""
        _normalize_common_domains(self)
        if models:
            for k, v in models.items():
                if k not in self.preferred_models:
                    self.preferred_models[k] = v
        if domains:
            existing = normalize_domain_list(self.common_domains)
            additions = normalize_domain_list(domains)
            seen = set(existing)
            for domain in additions:
                if domain in seen:
                    continue
                existing.append(domain)
                seen.add(domain)
            self.common_domains = existing
        if output_format and not self.preferred_output_format:
            self.preferred_output_format = output_format
        self.updated_at = datetime.now(timezone.utc)

    def merge_search_dirs(
        self,
        dirs: list[str] | None = None,
        *,
        max_entries: int = 20,
    ) -> bool:
        """Merge frequently used directories into the profile without duplicates."""
        if not dirs:
            return False
        existing = [_normalize_search_dir(d) for d in self.search_dirs]
        existing = [d for d in existing if d]
        seen = set(existing)
        changed = False
        for entry in dirs:
            normalized = _normalize_search_dir(entry)
            if not normalized or normalized in seen:
                continue
            existing.append(normalized)
            seen.add(normalized)
            changed = True
        if not changed:
            return False
        self.search_dirs = existing[-max_entries:]
        self.updated_at = datetime.now(timezone.utc)
        return True


def load_user_profile(path: Path | None = None) -> UserProfile:
    """Load profile from disk, or return fresh default."""
    p = resolve_profile_path(path)
    if p.exists():
        try:
            profile = UserProfile.model_validate_json(p.read_text(encoding="utf-8"))
            if _normalize_common_domains(profile):
                try:
                    save_user_profile(profile, p)
                except Exception:
                    logger.debug("Failed to persist normalized profile domains", exc_info=True)
            return profile
        except Exception:
            logger.warning("Failed to load profile from %s, using defaults", p)
    return UserProfile()


def save_user_profile(profile: UserProfile, path: Path | None = None) -> None:
    """Persist profile to disk (atomic write)."""
    p = resolve_profile_path(path)
    _normalize_common_domains(profile)
    _atomic_write_text(p, profile.model_dump_json(indent=2))


def format_recent_workflows(profile: UserProfile) -> str:
    """Format recent workflows for display on startup."""
    if not profile.recent_workflows:
        return ""
    lines = ["Recent workflows:"]
    now = time.time()
    for i, rw in enumerate(profile.recent_workflows[:5], 1):
        age = now - rw.opened_at
        if age < 3600:
            ago = f"{int(age / 60)}m ago"
        elif age < 86400:
            ago = f"{int(age / 3600)}h ago"
        else:
            ago = f"{int(age / 86400)}d ago"
        lines.append(f"  ({i}) {rw.workflow_id} [{ago}]")
    lines.append("  Resume? [1-5/new]")
    return "\n".join(lines)
