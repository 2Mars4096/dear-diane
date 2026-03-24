from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from dan.domain_taxonomy import normalize_domain_name

from .feature_gates import engine_feature_enabled

logger = logging.getLogger(__name__)


def propose_domain_discoveries(
    pattern_accumulator: Any,
    behavior_store: Any = None,
    adaptation_registry: Any = None,
    *,
    feature_enabled: Callable[[str], bool] | None = None,
) -> list[dict]:
    """Propose or auto-apply newly discovered domains from accumulated patterns."""
    from dan.engine.adaptation_registry import AdaptationCandidate
    from .domain_learning import apply_new_domain, get_domain_keyword_map

    feature_gate = feature_enabled or engine_feature_enabled
    clusters = pattern_accumulator.get_clusters("domain", min_count=3)
    if not clusters:
        return []

    existing_domains = set(get_domain_keyword_map(behavior_store).keys())
    proposals: list[dict] = []
    for cluster in clusters:
        keywords = cluster.get("keywords", [])
        domain_name = "_".join(word.lower() for word in keywords[:3])
        if domain_name in existing_domains:
            continue

        count = cluster.get("count", 0)
        examples = cluster.get("examples", [])
        proposal = {
            "name": domain_name,
            "keywords": keywords,
            "count": count,
            "examples": examples,
        }

        if feature_gate("domain_auto_discovery"):
            candidate = AdaptationCandidate(
                source="domain_discovery",
                auto_apply=True,
                parameter_key="domains/keyword_maps",
                description=(
                    f"Auto-discovered domain '{domain_name}' with keywords "
                    f"{keywords} from {count} unrecognized messages. "
                    f"Examples: {examples[:3]}"
                ),
                sample_size=count,
            )
            if adaptation_registry is not None:
                adaptation_registry.add(candidate)
            if behavior_store is not None:
                apply_new_domain(domain_name, keywords, behavior_store)
        elif feature_gate("domain_discovery_proposal"):
            candidate = AdaptationCandidate(
                source="domain_discovery",
                parameter_key="domains/keyword_maps",
                description=(
                    f"Propose new domain '{domain_name}' with keywords "
                    f"{keywords} from {count} unrecognized messages. "
                    f"Examples: {examples[:3]}"
                ),
                sample_size=count,
            )
            if adaptation_registry is not None:
                adaptation_registry.add(candidate)
        else:
            logger.debug(
                "Tier 0: discovered potential domain '%s' (%d occurrences)",
                domain_name,
                count,
            )

        proposals.append(proposal)
    return proposals


def propose_keyword_expansion(
    domain: str,
    message: str,
    task_succeeded: bool,
    behavior_store: Any = None,
    adaptation_registry: Any = None,
    *,
    feature_enabled: Callable[[str], bool] | None = None,
) -> dict | None:
    """Propose expanding a domain's keywords after a successful marginal match."""
    from dan.engine.adaptation_registry import AdaptationCandidate
    from .domain_learning import get_domain_keyword_map

    feature_gate = feature_enabled or engine_feature_enabled
    if not task_succeeded:
        return None

    kw_map = get_domain_keyword_map(behavior_store)
    domain = normalize_domain_name(domain) or domain
    current_keywords = kw_map.get(domain, [])
    if not current_keywords:
        return None

    msg_lower = message.lower()
    hit_count = sum(1 for keyword in current_keywords if keyword in msg_lower)
    if hit_count != 1:
        return None

    existing_lower = {keyword.lower() for keyword in current_keywords}
    word_freq: dict[str, int] = {}
    for word in re.findall(r"\b\w{4,}\b", msg_lower):
        if word not in existing_lower:
            word_freq[word] = word_freq.get(word, 0) + 1

    new_keywords = sorted(word_freq, key=word_freq.__getitem__, reverse=True)[:3]
    if not new_keywords:
        return None

    proposal: dict = {
        "domain": domain,
        "existing_keywords": current_keywords,
        "new_keywords": new_keywords,
        "message_preview": message[:200],
    }

    if feature_gate("domain_auto_discovery"):
        candidate = AdaptationCandidate(
            source="domain_discovery",
            auto_apply=True,
            parameter_key="domains/keyword_maps",
            description=(
                f"Auto-expand domain '{domain}' keywords with {new_keywords} "
                f"after successful marginal-match task"
            ),
        )
        if adaptation_registry is not None:
            adaptation_registry.add(candidate)
        if behavior_store is not None:
            kw_map[domain] = list(set(kw_map.get(domain, []) + new_keywords))
            behavior_store.set(
                "domains/keyword_maps",
                kw_map,
                reason=f"keyword expansion for domain '{domain}': +{new_keywords}",
            )
    elif feature_gate("domain_discovery_proposal"):
        candidate = AdaptationCandidate(
            source="domain_discovery",
            parameter_key="domains/keyword_maps",
            description=(
                f"Propose expanding domain '{domain}' keywords with "
                f"{new_keywords} after successful marginal-match task"
            ),
        )
        if adaptation_registry is not None:
            adaptation_registry.add(candidate)
    else:
        logger.debug(
            "Tier 0: potential keyword expansion for '%s': %s",
            domain,
            new_keywords,
        )

    return proposal
