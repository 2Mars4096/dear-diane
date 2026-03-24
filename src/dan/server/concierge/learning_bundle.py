from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .feature_gates import engine_feature_enabled

logger = logging.getLogger(__name__)


@dataclass
class ConciergeLearningBundle:
    """Engine-owned learning services injected into concierge at composition time."""

    behavior_store: Any = None
    behavior_changelog: Any = None
    param_registry: Any = None
    param_logger: Any = None
    pattern_accumulator: Any = None
    correction_store: Any = None
    adaptation_registry: Any = None
    prompt_tracker: Any = None
    feature_enabled: Callable[[str], bool] | None = None
    analyze_turn_feedback: Callable[..., Any] | None = None


def build_concierge_learning_bundle(
    *,
    chat_manager: Any,
    telemetry_store: Any = None,
    memory_kernel: Any = None,
    runtime_prompt_key: str = "prompts/runtime.unified_system",
) -> ConciergeLearningBundle:
    """Build concierge learning helpers outside the main runtime module."""
    bundle = ConciergeLearningBundle(feature_enabled=engine_feature_enabled)

    try:
        from .correction_feedback import analyze_turn_feedback

        bundle.analyze_turn_feedback = analyze_turn_feedback
    except Exception:
        logger.debug("Turn-feedback analyzer import failed", exc_info=True)

    try:
        from dan.engine.behavior_store import (
            AdaptableParameterRegistry,
            BehaviorChangeLog,
            BehaviorStore,
            ParameterDecisionLogger,
            PatternAccumulator,
        )
        from dan.engine.behavior_seeds import register_all_seeds
        from dan.chat_prompts import UNIFIED_SYSTEM_PROMPT

        bundle.behavior_store = BehaviorStore()
        bundle.behavior_changelog = BehaviorChangeLog()
        bundle.param_registry = AdaptableParameterRegistry()

        def _param_decision_event(
            *,
            parameter_key: str,
            parameter_value: str,
            metadata: dict[str, Any],
        ) -> Any:
            from dan.telemetry_api import TelemetryEvent

            return TelemetryEvent(
                event_type="parameter_decision",
                parameter_key=parameter_key,
                parameter_value=parameter_value,
                metadata=metadata,
            )

        bundle.param_logger = ParameterDecisionLogger(
            telemetry_store=telemetry_store,
            build_event=_param_decision_event,
        )
        bundle.pattern_accumulator = PatternAccumulator(bundle.behavior_store)
        register_all_seeds(bundle.behavior_store, bundle.param_registry)

        from .domain_learning import register_seed_domains

        register_seed_domains(bundle.behavior_store)

        from dan.providers.costs import register_seed_cost_table
        from dan.providers.tier_defaults import register_seed_tier_maps

        register_seed_tier_maps(bundle.behavior_store)
        register_seed_cost_table(bundle.behavior_store)
        bundle.behavior_store.register_seed(
            runtime_prompt_key,
            UNIFIED_SYSTEM_PROMPT,
        )
        if hasattr(chat_manager, "set_behavior_store"):
            chat_manager.set_behavior_store(bundle.behavior_store)
    except Exception as exc:
        logger.warning("BehaviorStore init failed: %s", exc)
        bundle = ConciergeLearningBundle(
            feature_enabled=engine_feature_enabled,
            analyze_turn_feedback=bundle.analyze_turn_feedback,
        )

    try:
        from dan.engine.adaptation_registry import AdaptationRegistry
        from dan.engine.correction_memory import CorrectionStore
        from dan.engine.outcome_trackers import PromptTracker

        behavior_base = getattr(bundle.behavior_store, "base_path", None)
        correction_path = (
            behavior_base / "runtime_corrections.jsonl"
            if behavior_base is not None
            else None
        )
        adaptation_path = (
            behavior_base / "runtime_adaptations.json"
            if behavior_base is not None
            else None
        )

        bundle.correction_store = CorrectionStore(path=correction_path)
        bundle.adaptation_registry = AdaptationRegistry(
            changelog=bundle.behavior_changelog,
            param_registry=bundle.param_registry,
            path=adaptation_path,
        )
        if memory_kernel is not None:
            bundle.prompt_tracker = PromptTracker(memory_kernel)
    except Exception as exc:
        logger.warning("Learning loop init failed: %s", exc)
        bundle.correction_store = None
        bundle.adaptation_registry = None
        bundle.prompt_tracker = None

    return bundle
