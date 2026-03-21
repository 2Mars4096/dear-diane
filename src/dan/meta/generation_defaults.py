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
import os
import uuid as _uuid
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_LEGACY_MODEL_TIER_TO_TASK_TIER: dict[str, str] = {
    "micro": "micro",
    "routine": "routine",
    "standard": "reasoning",
    "reasoning": "reasoning",
    "premium": "critical",
    "critical": "critical",
}

_TASK_TIER_TO_LEGACY_MODEL_TIER: dict[str, str] = {
    "micro": "micro",
    "routine": "routine",
    "reasoning": "standard",
    "critical": "premium",
}


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


def normalize_task_tier(value: Any) -> str | None:
    """Normalize legacy generation tier labels to runtime task tiers."""
    if value is None:
        return None
    tier = str(value).strip().lower()
    return _LEGACY_MODEL_TIER_TO_TASK_TIER.get(tier)


def legacy_model_tier_label(task_tier: Any) -> str | None:
    """Map runtime task tiers back to the legacy generation labels."""
    normalized = normalize_task_tier(task_tier)
    if normalized is None:
        return None
    return _TASK_TIER_TO_LEGACY_MODEL_TIER.get(normalized, normalized)


def _configured_llm_providers() -> list[str]:
    providers: list[str] = []
    if os.environ.get("DAN_ANTHROPIC_API_KEY", "").strip():
        providers.append("anthropic")
    if os.environ.get("DAN_OPENAI_API_KEY", "").strip():
        providers.append("openai")
    if os.environ.get("DAN_GOOGLE_API_KEY", "").strip():
        providers.append("google")
    return providers


def resolve_default_llm_model(*, tier: str = "routine") -> str:
    """Resolve a provider-aware default LLM model for generated workflows."""
    explicit = os.environ.get("DAN_LLM_MODEL", "").strip()
    if explicit:
        return explicit

    try:
        from dan.providers.tier_defaults import resolve_tier_map

        tier_map = resolve_tier_map(_configured_llm_providers())
        model = tier_map.get(tier) or tier_map.get("routine")
        if model:
            return model
    except Exception:
        logger.debug("Falling back to static default LLM model", exc_info=True)

    return "claude-sonnet-4-6"


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

    @staticmethod
    def _node_payload(node: dict[str, Any]) -> dict[str, Any]:
        config = node.get("config")
        return config if isinstance(config, dict) else node

    @classmethod
    def _get_node_value(cls, node: dict[str, Any], key: str, default: Any = None) -> Any:
        return cls._node_payload(node).get(key, default)

    @classmethod
    def _set_node_value(cls, node: dict[str, Any], key: str, value: Any) -> None:
        cls._node_payload(node)[key] = value

    @classmethod
    def _node_uses_nested_config(cls, node: dict[str, Any]) -> bool:
        return isinstance(node.get("config"), dict)

    def _add_llm_retry(self, nodes: list[dict]) -> None:
        rc = self.defaults.retry_config
        policy = {
            "max_retries": rc.llm_max_retries,
            "backoff": rc.llm_backoff,
            "base_delay": rc.llm_base_delay,
        }
        for node in nodes:
            if node.get("node_type") == "llm_operator":
                payload = self._node_payload(node)
                if "retry_policy" not in payload:
                    self._set_node_value(node, "retry_policy", dict(policy))

    def _add_tool_retry(self, nodes: list[dict]) -> None:
        rc = self.defaults.retry_config
        policy = {
            "max_retries": rc.tool_max_retries,
            "backoff": rc.tool_backoff,
            "base_delay": rc.tool_base_delay,
        }
        for node in nodes:
            if node.get("node_type") == "tool_operator":
                tool_id = str(self._get_node_value(node, "tool_id", ""))
                if tool_id in _EXTERNAL_TOOLS:
                    payload = self._node_payload(node)
                    if "retry_policy" not in payload:
                        self._set_node_value(node, "retry_policy", dict(policy))

    def _apply_model_tiering(self, nodes: list[dict]) -> None:
        """Assign model tiers based on node position heuristics."""
        llm_nodes = self._llm_nodes(nodes)
        if len(llm_nodes) < 2:
            return

        for i, node in enumerate(llm_nodes):
            payload = self._node_payload(node)
            if i == len(llm_nodes) - 1:
                legacy_tier = "premium"
            elif i == 0:
                legacy_tier = "routine"
            else:
                legacy_tier = "standard"

            task_tier = normalize_task_tier(payload.get("task_tier") or payload.get("model_tier"))
            if task_tier is None:
                task_tier = normalize_task_tier(legacy_tier)

            if not payload.get("model_tier"):
                self._set_node_value(node, "model_tier", legacy_tier)
            if task_tier and not payload.get("task_tier"):
                self._set_node_value(node, "task_tier", task_tier)
            if task_tier and not payload.get("model_policy"):
                self._set_node_value(node, "model_policy", {"strategy": "tier"})
            if task_tier and not payload.get("model"):
                self._set_node_value(
                    node,
                    "model",
                    resolve_default_llm_model(tier=task_tier),
                )

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
            validator = self._make_validator_node(
                val_id=val_id,
                target_id=tid,
                target_node=terminal_node,
            )
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
            or "review" in str(self._get_node_value(n, "name", "")).lower()
            for n in nodes
        )
        if has_review:
            return

        content_node = None
        for n in reversed(nodes):
            if n.get("node_type") != "llm_operator":
                continue
            prompt = str(self._get_node_value(n, "prompt_template", "")).lower()
            if any(kw in prompt for kw in _CONTENT_KEYWORDS):
                content_node = n
                break
        if content_node is None:
            return

        cid = content_node.get("id")
        if not cid:
            return

        rev_id = f"auto_reviewer_{_uuid.uuid4().hex[:6]}"
        reviewer = self._make_reviewer_node(rev_id=rev_id, content_node=content_node)
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
            payload = node.get("config", {}) if isinstance(node.get("config"), dict) else node
            prompt = str(payload.get("prompt_template", "")).lower()
            if any(kw in prompt for kw in _CONTENT_KEYWORDS):
                return True
        return False

    def _make_validator_node(
        self,
        *,
        val_id: str,
        target_id: str,
        target_node: dict[str, Any],
    ) -> dict[str, Any]:
        if self._node_uses_nested_config(target_node):
            return {
                "id": val_id,
                "node_type": "validator",
                "config": {
                    "name": f"validate_before_{target_id}",
                    "validation_rules": [{"rule_type": "format_check", "config": {}}],
                    "on_failure": "route",
                    "strict_mode": False,
                },
            }

        return {
            "id": val_id,
            "name": f"validate_before_{target_id}",
            "node_type": "validator",
            "validation_rules": [{"rule_type": "format_check", "config": {}}],
            "on_failure": "route",
            "strict_mode": False,
        }

    def _make_reviewer_node(
        self,
        *,
        rev_id: str,
        content_node: dict[str, Any],
    ) -> dict[str, Any]:
        reviewer_task_tier = normalize_task_tier(
            self._get_node_value(content_node, "task_tier")
            or self._get_node_value(content_node, "model_tier")
        )
        reviewer_model = str(
            self._get_node_value(
                content_node,
                "model",
                resolve_default_llm_model(tier=reviewer_task_tier or "routine"),
            )
        )
        reviewer_model_policy = self._get_node_value(content_node, "model_policy")
        reviewer_legacy_tier = self._get_node_value(content_node, "model_tier")
        if reviewer_legacy_tier is None and reviewer_task_tier is not None:
            reviewer_legacy_tier = legacy_model_tier_label(reviewer_task_tier)
        reviewer_prompt = (
            "Review the following content for quality, accuracy, and completeness. "
            "Provide a quality score (1-10) and specific feedback."
        )
        if self._node_uses_nested_config(content_node):
            reviewer = {
                "id": rev_id,
                "node_type": "llm_operator",
                "config": {
                    "name": f"review_{content_node.get('id')}",
                    "model": reviewer_model,
                    "prompt_template": reviewer_prompt,
                    "system_prompt": "",
                    "temperature": 0.3,
                },
            }
            reviewer_config = reviewer["config"]
            if reviewer_model_policy is not None:
                reviewer_config["model_policy"] = reviewer_model_policy
            if reviewer_task_tier is not None:
                reviewer_config["task_tier"] = reviewer_task_tier
            if reviewer_legacy_tier is not None:
                reviewer_config["model_tier"] = reviewer_legacy_tier
            return reviewer

        reviewer = {
            "id": rev_id,
            "name": f"review_{content_node.get('id')}",
            "node_type": "llm_operator",
            "model": reviewer_model,
            "prompt_template": reviewer_prompt,
            "system_prompt": "",
            "temperature": 0.3,
        }
        if reviewer_model_policy is not None:
            reviewer["model_policy"] = reviewer_model_policy
        if reviewer_task_tier is not None:
            reviewer["task_tier"] = reviewer_task_tier
        if reviewer_legacy_tier is not None:
            reviewer["model_tier"] = reviewer_legacy_tier
        return reviewer


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
