"""Smart generation defaults and domain generation profiles.

Provides:
- ``DefaultProfile`` — tier of automatic defaults applied to generated workflows
- ``GenerationDefaults`` — per-rule config for default injection
- ``DefaultsEnricher`` — post-generation graph enrichment (safety net)
- ``DomainGenerationProfile`` — per-domain generation configuration
- ``get_domain_profile()`` — profile registry with user override support
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default profiles
# ---------------------------------------------------------------------------

class DefaultProfile(str, Enum):
    minimal = "minimal"
    standard = "standard"
    robust = "robust"


class RetryDefaults(BaseModel):
    llm_max_retries: int = 2
    llm_backoff: str = "exponential"
    llm_base_delay: float = 1.0
    tool_max_retries: int = 3
    tool_backoff: str = "exponential"
    tool_base_delay: float = 2.0


class GenerationDefaults(BaseModel):
    """Per-rule enabled/config flags for default injection."""
    profile: DefaultProfile = DefaultProfile.standard
    retry_on_llm: bool = True
    retry_on_tools: bool = True
    validation_gate: bool = True
    review_on_content: bool = False
    model_tiering: bool = False
    error_notification: bool = False
    retry_config: RetryDefaults = Field(default_factory=RetryDefaults)

    @classmethod
    def from_profile(cls, profile: DefaultProfile) -> GenerationDefaults:
        if profile == DefaultProfile.minimal:
            return cls(
                profile=profile,
                retry_on_llm=False,
                retry_on_tools=False,
                validation_gate=False,
                review_on_content=False,
                model_tiering=False,
                error_notification=False,
            )
        elif profile == DefaultProfile.robust:
            return cls(
                profile=profile,
                retry_on_llm=True,
                retry_on_tools=True,
                validation_gate=True,
                review_on_content=True,
                model_tiering=True,
                error_notification=True,
            )
        else:  # standard
            return cls(profile=profile)


# ---------------------------------------------------------------------------
# Suppression detection
# ---------------------------------------------------------------------------

_SUPPRESSION_KEYWORDS: dict[str, list[str]] = {
    "retry_on_llm": ["no retry", "without retry", "skip retry"],
    "retry_on_tools": ["no retry", "without retry", "skip retry"],
    "validation_gate": ["no validation", "skip validation", "without validation"],
    "review_on_content": ["no review", "without review", "minimal"],
    "model_tiering": ["same model", "single model", "no tiering"],
}


def detect_suppressions(user_text: str) -> dict[str, bool]:
    """Detect which defaults the user wants to suppress."""
    text_lower = user_text.lower()
    result: dict[str, bool] = {}
    for field, keywords in _SUPPRESSION_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            result[field] = False
    return result


# ---------------------------------------------------------------------------
# Defaults enricher (post-generation safety net)
# ---------------------------------------------------------------------------

from dan.builder._constants import CONTENT_KEYWORDS as _CONTENT_KEYWORDS

_EXTERNAL_TOOLS = frozenset({
    "web_search", "web_fetch", "send_email", "http_request",
})


class DefaultsEnricher:
    """Post-generation graph enrichment — adds missing defaults non-destructively.

    Operates on graph dict (not the Pydantic model) to allow lightweight
    modifications without full recompilation.
    """

    def __init__(self, defaults: GenerationDefaults | None = None) -> None:
        self.defaults = defaults or GenerationDefaults()

    def enrich(self, graph_dict: dict[str, Any]) -> dict[str, Any]:
        """Apply defaults to a graph dict. Returns the modified dict."""
        if self.defaults.profile == DefaultProfile.minimal:
            return graph_dict

        nodes = graph_dict.get("nodes", [])

        if self.defaults.retry_on_llm:
            self._add_llm_retry(nodes)

        if self.defaults.retry_on_tools:
            self._add_tool_retry(nodes)

        if self.defaults.model_tiering and len(self._llm_nodes(nodes)) > 3:
            self._apply_model_tiering(nodes)

        if self.defaults.validation_gate:
            self._ensure_validation_gate(graph_dict)

        if self.defaults.review_on_content and self.has_content_workflow(nodes):
            self._ensure_review_on_content(graph_dict)

        return graph_dict

    def _add_llm_retry(self, nodes: list[dict]) -> None:
        rc = self.defaults.retry_config
        policy = {
            "max_retries": rc.llm_max_retries,
            "backoff": rc.llm_backoff,
            "base_delay": rc.llm_base_delay,
        }
        for node in nodes:
            if node.get("node_type") == "llm_operator":
                config = node.get("config", {})
                if "retry_policy" not in config:
                    config["retry_policy"] = policy
                    node["config"] = config

    def _add_tool_retry(self, nodes: list[dict]) -> None:
        rc = self.defaults.retry_config
        policy = {
            "max_retries": rc.tool_max_retries,
            "backoff": rc.tool_backoff,
            "base_delay": rc.tool_base_delay,
        }
        for node in nodes:
            if node.get("node_type") == "tool_operator":
                tool_id = node.get("config", {}).get("tool_id", "")
                if tool_id in _EXTERNAL_TOOLS:
                    config = node.get("config", {})
                    if "retry_policy" not in config:
                        config["retry_policy"] = policy
                        node["config"] = config

    def _apply_model_tiering(self, nodes: list[dict]) -> None:
        """Assign model tiers based on node position heuristics."""
        llm_nodes = self._llm_nodes(nodes)
        if len(llm_nodes) < 2:
            return

        for i, node in enumerate(llm_nodes):
            config = node.get("config", {})
            if config.get("model_tier"):
                continue
            if i == len(llm_nodes) - 1:
                config["model_tier"] = "premium"
            elif i == 0:
                config["model_tier"] = "routine"
            else:
                config["model_tier"] = "standard"
            node["config"] = config

    @staticmethod
    def _llm_nodes(nodes: list[dict]) -> list[dict]:
        return [n for n in nodes if n.get("node_type") == "llm_operator"]

    def _ensure_validation_gate(self, graph_dict: dict[str, Any]) -> None:
        """If no validator node exists before the terminal node, insert one."""
        nodes = graph_dict.get("nodes", [])
        if not nodes:
            return

        has_validator = any(n.get("node_type") == "validator" for n in nodes)
        if has_validator:
            return

        raw_edges = graph_dict.get("edges", [])
        data_edges = (
            raw_edges.get("data", [])
            if isinstance(raw_edges, dict)
            else (raw_edges if isinstance(raw_edges, list) else [])
        )
        if not data_edges:
            return

        source_ids = {e["source_node_id"] for e in data_edges if isinstance(e, dict)}
        all_node_ids = {n.get("id") for n in nodes if n.get("id")}

        terminal_ids = all_node_ids - source_ids
        if not terminal_ids:
            last_id = nodes[-1].get("id")
            terminal_ids = {last_id} if last_id else set()

        for tid in terminal_ids:
            terminal_node = next((n for n in nodes if n.get("id") == tid), None)
            if terminal_node is None or terminal_node.get("node_type") != "llm_operator":
                continue

            incoming = [e for e in data_edges if e["target_node_id"] == tid]
            if not incoming:
                continue

            val_id = f"auto_validator_{_uuid.uuid4().hex[:6]}"
            validator = {
                "id": val_id,
                "node_type": "validator",
                "config": {
                    "name": f"validate_before_{tid}",
                    "validation_rules": [{"rule_type": "format_check", "config": {}}],
                    "on_failure": "route",
                    "strict_mode": False,
                },
            }
            nodes.append(validator)

            for edge in incoming:
                edge["target_node_id"] = val_id
                edge["target_port"] = "data"

            edges = graph_dict.get("edges")
            if not isinstance(edges, list):
                edges = edges.get("data", []) if isinstance(edges, dict) else []
                graph_dict["edges"] = edges
            edges.append({
                "source_node_id": val_id,
                "source_port": "valid",
                "target_node_id": tid,
                "target_port": "text",
            })
            break

    def _ensure_review_on_content(self, graph_dict: dict[str, Any]) -> None:
        """If a content workflow has no review loop, add a reviewer after the last content node."""
        nodes = graph_dict.get("nodes", [])
        has_review = any(
            n.get("node_type") in ("while_loop", "goal_loop")
            or "review" in n.get("config", {}).get("name", "").lower()
            for n in nodes
        )
        if has_review:
            return

        content_node = None
        for n in reversed(nodes):
            if n.get("node_type") != "llm_operator":
                continue
            prompt = n.get("config", {}).get("prompt_template", "").lower()
            if any(kw in prompt for kw in _CONTENT_KEYWORDS):
                content_node = n
                break
        if content_node is None:
            return

        cid = content_node.get("id")
        if not cid:
            return

        rev_id = f"auto_reviewer_{_uuid.uuid4().hex[:6]}"
        reviewer = {
            "id": rev_id,
            "node_type": "llm_operator",
            "config": {
                "name": f"review_{cid}",
                "prompt_template": (
                    "Review the following content for quality, accuracy, and completeness. "
                    "Provide a quality score (1-10) and specific feedback."
                ),
                "system_prompt": "",
                "temperature": 0.3,
            },
        }
        nodes.append(reviewer)

        edges = graph_dict.get("edges")
        if not isinstance(edges, list):
            edges = edges.get("data", []) if isinstance(edges, dict) else []
            graph_dict["edges"] = edges
        edges.append({
            "source_node_id": cid,
            "source_port": "text",
            "target_node_id": rev_id,
            "target_port": "text",
        })

    @staticmethod
    def has_content_workflow(nodes: list[dict]) -> bool:
        """Check if any LLM node's prompt suggests long-form content."""
        for node in nodes:
            if node.get("node_type") != "llm_operator":
                continue
            prompt = node.get("config", {}).get("prompt_template", "").lower()
            if any(kw in prompt for kw in _CONTENT_KEYWORDS):
                return True
        return False


# ---------------------------------------------------------------------------
# Domain generation profiles
# ---------------------------------------------------------------------------

_SEED_PROFILE_DIR = Path(__file__).resolve().parent.parent / "data" / "generation_profiles"
_USER_PROFILE_DIR = Path("~/.dan/generation_profiles").expanduser()


class DomainGenerationProfile(BaseModel):
    """Per-domain generation configuration."""
    domain: str
    description: str = ""
    preferred_tools: list[str] = Field(default_factory=list)
    preferred_patterns: list[str] = Field(default_factory=list)
    model_tier_hints: dict[str, str] = Field(default_factory=dict)
    default_profile: str = "standard"
    validation_hints: list[str] = Field(default_factory=list)
    prompt_hints: list[str] = Field(default_factory=list)


_profile_cache: dict[str, DomainGenerationProfile] = {}


def get_domain_profile(domain: str) -> DomainGenerationProfile | None:
    """Load a domain generation profile. User overrides take precedence over seeds."""
    if domain in _profile_cache:
        return _profile_cache[domain]

    # Check user override first
    user_path = _USER_PROFILE_DIR / f"{domain}.json"
    if user_path.exists():
        try:
            profile = DomainGenerationProfile.model_validate_json(user_path.read_text())
            _profile_cache[domain] = profile
            return profile
        except Exception:
            logger.warning("Failed to load user profile for %s", domain)

    # Fall back to seed
    seed_path = _SEED_PROFILE_DIR / f"{domain}.json"
    if seed_path.exists():
        try:
            profile = DomainGenerationProfile.model_validate_json(seed_path.read_text())
            _profile_cache[domain] = profile
            return profile
        except Exception:
            logger.warning("Failed to load seed profile for %s", domain)

    return None


def clear_profile_cache() -> None:
    """Clear the profile cache (for testing)."""
    _profile_cache.clear()


def build_domain_prompt_context(profile: DomainGenerationProfile) -> str:
    """Format a domain profile into advisory context for codegen prompts."""
    sections = [f"## Domain Context: {profile.description or profile.domain}"]

    if profile.preferred_tools:
        tools_str = ", ".join(profile.preferred_tools)
        sections.append(f"For this {profile.domain} workflow, consider using: {tools_str}")

    if profile.preferred_patterns:
        patterns_str = ", ".join(profile.preferred_patterns)
        sections.append(f"Common patterns for {profile.domain} workflows: {patterns_str}")

    if profile.prompt_hints:
        sections.append("Domain guidance:")
        for hint in profile.prompt_hints:
            sections.append(f"  - {hint}")

    if profile.validation_hints:
        sections.append("Validation considerations:")
        for hint in profile.validation_hints:
            sections.append(f"  - {hint}")

    return "\n".join(sections)
