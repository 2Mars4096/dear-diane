"""Seed registrations for behavioral parameters (31-22).

Central place where all behavioral constants get registered with
BehaviorStore and AdaptableParameterRegistry at startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dan.engine.behavior_store import AdaptableParameterRegistry, BehaviorStore


_SEED_HEURISTICS: dict[str, dict[str, Any]] = {
    "heuristics/classifier.fast_path_confidence": {
        "value": 0.85, "min_bound": 0.5, "max_bound": 0.99,
        "description": "LLM classifier skip threshold",
    },
    "heuristics/reuse.reuse_threshold": {
        "value": 0.8, "min_bound": 0.5, "max_bound": 0.99,
        "description": "Workflow reuse confidence threshold",
    },
    "heuristics/reuse.adapt_threshold": {
        "value": 0.4, "min_bound": 0.1, "max_bound": 0.8,
        "description": "Workflow adapt confidence threshold",
    },
    "heuristics/reuse.min_success_rate": {
        "value": 0.5, "min_bound": 0.1, "max_bound": 0.9,
        "description": "Minimum success rate for reuse consideration",
    },
    "heuristics/reuse.domain_boost": {
        "value": 0.15, "min_bound": 0.0, "max_bound": 0.5,
        "description": "Domain match scoring boost",
    },
    "heuristics/context.project_match": {
        "value": 0.45, "min_bound": 0.2, "max_bound": 0.8,
        "description": "Context resolver project match score",
    },
    "heuristics/context.task_match": {
        "value": 0.55, "min_bound": 0.2, "max_bound": 0.8,
        "description": "Context resolver task match score",
    },
    "heuristics/entity.confidence": {
        "value": 0.6, "min_bound": 0.3, "max_bound": 0.9,
        "description": "Entity grounding confidence threshold",
    },
    "heuristics/consolidation.decay_days": {
        "value": 60, "min_bound": 7, "max_bound": 365,
        "description": "Memory consolidation decay period in days",
    },
    "heuristics/continuity.handoff_threshold": {
        "value": 0.3, "min_bound": 0.1, "max_bound": 0.8,
        "description": "Cross-surface handoff match threshold",
    },
}

_SEED_PROMPT_KEYS: list[tuple[str, str, str]] = [
    ("prompts/classifier.classification_system", "medium", "Classifier system prompt"),
    ("prompts/classifier.solver_system", "medium", "Solver system prompt"),
    ("prompts/runtime.unified_system", "high", "Main unified system prompt"),
    ("prompts/codegen.system", "medium", "Code generation system prompt"),
    ("prompts/planner.system_template", "medium", "Planner system prompt template"),
]


def register_seed_prompts(
    store: BehaviorStore,
    registry: AdaptableParameterRegistry,
) -> None:
    """Register prompt keys as adaptable parameters.

    Actual prompt text is injected by each module via store.register_seed().
    """
    from dan.engine.behavior_store import AdaptableParameter

    for key, risk, desc in _SEED_PROMPT_KEYS:
        registry.register(AdaptableParameter(
            key=key,
            category="prompts",
            evidence_type="correction_attribution",
            min_evidence_count=20,
            risk_level=risk,
            description=desc,
        ))


def register_seed_heuristics(
    store: BehaviorStore,
    registry: AdaptableParameterRegistry,
) -> None:
    """Register threshold/weight seed defaults and their adaptable metadata."""
    from dan.engine.behavior_store import AdaptableParameter

    for key, meta in _SEED_HEURISTICS.items():
        store.register_seed(key, meta)
        registry.register(AdaptableParameter(
            key=key,
            category="thresholds",
            evidence_type="parameter_decision",
            min_evidence_count=10,
            risk_level="low",
            bounds={
                "min": meta.get("min_bound"),
                "max": meta.get("max_bound"),
                "max_step_pct": 0.20,
            },
            description=meta.get("description", ""),
        ))


def register_all_seeds(
    store: BehaviorStore,
    registry: AdaptableParameterRegistry,
) -> None:
    """Register all seed defaults — called once during Concierge startup."""
    register_seed_prompts(store, registry)
    register_seed_heuristics(store, registry)
    register_memory_ranking_params(registry)


def register_memory_ranking_params(registry: AdaptableParameterRegistry) -> None:
    """Register memory kernel ranking weight keys as adaptable parameters.

    These are not externalized yet — the actual weights stay as constants in
    memory_kernel.py. Registering them here makes the parameter registry aware
    of their existence so future retrieval-outcome correlation evidence can
    drive calibration proposals.
    """
    from dan.engine.behavior_store import AdaptableParameter

    params = [
        AdaptableParameter(
            key="heuristics/memory_ranking.preference",
            category="retrieval_policy",
            evidence_type="retrieval_correlation",
            min_evidence_count=15,
            risk_level="low",
            description="Preference ranking: base=0.3, confirmed_bonus=0.4, access_cap=0.2, recency=0.1",
        ),
        AdaptableParameter(
            key="heuristics/memory_ranking.fact",
            category="retrieval_policy",
            evidence_type="retrieval_correlation",
            min_evidence_count=15,
            risk_level="low",
            description="Fact ranking: importance=0.5, scope_bonus, recency=0.2, keyword=0.1",
        ),
        AdaptableParameter(
            key="heuristics/memory_ranking.workflow_pattern",
            category="retrieval_policy",
            evidence_type="retrieval_correlation",
            min_evidence_count=15,
            risk_level="low",
            description="Workflow pattern ranking: keyword=0.4, success_rate=0.3, recency=0.15, importance=0.15",
        ),
        AdaptableParameter(
            key="heuristics/memory_ranking.workflow_asset",
            category="retrieval_policy",
            evidence_type="retrieval_correlation",
            min_evidence_count=15,
            risk_level="low",
            description="Workflow asset ranking: keyword=0.35, success_rate=0.3, recency=0.15, importance=0.2",
        ),
        AdaptableParameter(
            key="heuristics/memory_ranking.failure_pattern",
            category="retrieval_policy",
            evidence_type="retrieval_correlation",
            min_evidence_count=15,
            risk_level="low",
            description="Failure pattern ranking: keyword=0.35, recurrence=0.3, recency=0.2, importance=0.15",
        ),
        AdaptableParameter(
            key="heuristics/memory_ranking.principle",
            category="retrieval_policy",
            evidence_type="retrieval_correlation",
            min_evidence_count=15,
            risk_level="low",
            description="Principle ranking: confidence=0.4, keyword=0.3, recurrence=0.2, recency=0.1",
        ),
        AdaptableParameter(
            key="heuristics/memory_ranking.episode",
            category="retrieval_policy",
            evidence_type="retrieval_correlation",
            min_evidence_count=15,
            risk_level="low",
            description="Episode ranking: recency=0.5, keyword=0.5",
        ),
    ]
    for param in params:
        registry.register(param)
