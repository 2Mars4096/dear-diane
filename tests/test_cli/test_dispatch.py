from __future__ import annotations

import pytest

import diane.cli.dispatch as dispatch
from diane.cli.dispatch import resolve_intent_signal, select_orchestrator


def test_dispatch_public_exports_keep_fallback_cue_lists_private() -> None:
    assert "BUILD_INTENT_CUES" not in dispatch.__all__
    assert "WEBSITE_INTENT_CUES" not in dispatch.__all__
    assert "READ_ONLY_INTENT_CUES" not in dispatch.__all__
    assert "WEBSITE_WORKSPACE_PATCH_CUES" not in dispatch.__all__
    assert not hasattr(dispatch, "BUILD_INTENT_CUES")
    assert not hasattr(dispatch, "WEBSITE_INTENT_CUES")
    assert not hasattr(dispatch, "READ_ONLY_INTENT_CUES")
    assert not hasattr(dispatch, "WEBSITE_WORKSPACE_PATCH_CUES")
    assert "IntentSignal" in dispatch.__all__
    assert "resolve_intent_signal" in dispatch.__all__


def test_select_orchestrator_for_super_organism_uses_generic_lane() -> None:
    website = select_orchestrator("build a website with HTML and CSS", {"command": "super-organism"})
    generic = select_orchestrator("implement a small feature", {"command": "super-organism"})

    assert website.orchestrator_id == "super-dan-live-general"
    assert "website" in website.matched_cues
    assert website.tool_policy["profile"] == "generic"
    assert generic.orchestrator_id == "super-dan-live-general"
    assert generic.tool_policy["profile"] == "generic"
    assert website.intent_signal.artifact_target == "website"
    assert generic.intent_signal.artifact_target == "workspace"


def test_select_orchestrator_for_super_organism_keeps_computer_tools_nested_by_default() -> None:
    choice = select_orchestrator(
        "verify this workflow",
        {
            "command": "super-organism",
            "intent_signal": {
                "operation": "mutate",
                "artifact_target": "workspace",
                "mutation_permission": True,
            },
        },
    )

    allowed = choice.tool_policy["allowed_tool_ids"]
    assert len(allowed) == len(set(allowed))
    assert "browser_inspect" not in allowed
    assert "browser_click" not in allowed
    assert "desktop_observe" not in allowed
    assert "desktop_click" not in allowed


def test_select_orchestrator_for_super_organism_expands_computer_tools_from_policy_pack() -> None:
    choice = select_orchestrator(
        "verify this workflow through the active UI",
        {
            "command": "super-organism",
            "intent_signal": {
                "operation": "mutate",
                "artifact_target": "workspace",
                "mutation_permission": True,
            },
            "surface_policy": {
                "capability_packs": ["computer_control"],
            },
        },
    )

    allowed = choice.tool_policy["allowed_tool_ids"]
    preferred = choice.tool_policy["preferred_tool_ids"]
    assert len(allowed) == len(set(allowed))
    assert {
        "browser_tabs",
        "browser_inspect",
        "browser_open",
        "browser_wait",
        "browser_extract",
        "browser_screenshot",
        "browser_click",
        "browser_fill",
        "browser_type",
        "browser_select",
        "browser_download",
        "desktop_observe",
        "desktop_focus",
        "desktop_click",
        "desktop_type",
        "desktop_hotkey",
    }.issubset(set(allowed))
    assert "browser_inspect" in preferred
    assert "desktop_observe" in preferred
    assert "clipboard" not in allowed


def test_select_orchestrator_reads_capability_packs_from_surface_context_policy() -> None:
    choice = select_orchestrator(
        "use the browser UI",
        {
            "command": "super-organism",
            "intent_signal": {
                "operation": "mutate",
                "artifact_target": "workspace",
                "mutation_permission": True,
            },
            "surface_context": {
                "surface_policy": {
                    "capability_packs": ["browser_control"],
                }
            },
        },
    )

    allowed = choice.tool_policy["allowed_tool_ids"]
    assert "browser_inspect" in allowed
    assert "browser_click" in allowed
    assert "desktop_observe" not in allowed


def test_resolve_intent_signal_is_explainable_before_lane_selection() -> None:
    signal = resolve_intent_signal(
        "can you think harder, the layout now is completely messy",
        {
            "command": "super-organism",
            "execution_family": "general_operator",
            "existing_website_workspace": True,
        },
    )

    assert signal.operation == "mutate"
    assert signal.artifact_target == "website"
    assert signal.mutation_permission is True
    assert signal.confidence > 0
    assert "existing artifact context" in signal.rationale
    assert "existing_artifact:website" in signal.evidence


def test_select_orchestrator_accepts_explicit_intent_signal_without_text_cues() -> None:
    choice = select_orchestrator(
        "把这里处理一下",
        {
            "command": "super-organism",
            "intent_signal": {
                "operation": "mutate",
                "artifact_target": "workspace",
                "mutation_permission": True,
                "confidence": 0.91,
                "source": "test-classifier",
                "rationale": "external classifier selected a workspace mutation",
                "evidence": ["language_agnostic_classifier"],
            },
        },
    )

    assert choice.orchestrator_id == "super-dan-live-general"
    assert choice.intent_signal.source == "test-classifier"
    assert choice.intent_signal.rationale == "external classifier selected a workspace mutation"


def test_select_orchestrator_accepts_explicit_super_workspace_deliverable_mutation() -> None:
    choice = select_orchestrator(
        "research the semiconductor supply chain and return a markdown report",
        {
            "command": "super-organism",
            "execution_family": "research",
            "intent_signal": {
                "operation": "mutate",
                "artifact_target": "workspace",
                "mutation_permission": True,
                "confidence": 0.9,
                "source": "super-dan-live-context",
                "rationale": "explicit live context should execute a workspace report artifact",
            },
        },
    )

    assert choice.orchestrator_id == "super-dan-live-general"
    assert choice.tool_policy["profile"] == "generic"
    assert "web_search" in choice.tool_policy["allowed_tool_ids"]
    assert "workspace_check" in choice.tool_policy["allowed_tool_ids"]
    assert choice.intent_signal.operation == "mutate"


def test_select_orchestrator_accepts_explicit_operation_without_permission_field() -> None:
    choice = select_orchestrator(
        "把这里处理一下",
        {
            "command": "super-organism",
            "operation": "mutate",
            "artifact_target": "workspace",
            "intent_source": "test-classifier",
        },
    )

    assert choice.orchestrator_id == "super-dan-live-general"
    assert choice.intent_signal.operation == "mutate"
    assert choice.intent_signal.mutation_permission is True


def test_select_orchestrator_treats_artifact_only_context_as_routing_evidence() -> None:
    choice = select_orchestrator(
        "build this",
        {
            "command": "super-organism",
            "artifact_kind": "website",
        },
    )

    assert choice.orchestrator_id == "super-dan-live-general"
    assert choice.tool_policy["profile"] == "generic"
    assert choice.intent_signal.operation == "mutate"
    assert choice.intent_signal.artifact_target == "website"
    assert "artifact_context:website" in choice.intent_signal.evidence


def test_select_orchestrator_explicit_read_only_intent_blocks_website_context_mutation() -> None:
    choice = select_orchestrator(
        "please continue here",
        {
            "command": "super-organism",
            "existing_website_workspace": True,
            "intent_signal": {
                "operation": "read_only",
                "artifact_target": "website",
                "mutation_permission": False,
                "confidence": 0.93,
                "source": "test-classifier",
                "rationale": "external classifier selected read-only review",
            },
        },
    )

    assert choice.orchestrator_id == "super-dan-showcase"
    assert choice.intent_signal.operation == "read_only"
    assert choice.intent_signal.mutation_permission is False


@pytest.mark.parametrize(
    ("intent", "context_updates"),
    [
        (
            "can you think harder, the layout now is completely messy",
            {"existing_website_workspace": True},
        ),
        (
            "tighten the hero spacing so the first screen feels intentional",
            {"existing_website": True},
        ),
        (
            "polish the mobile alignment and visual style",
            {"workspace_kind": "website"},
        ),
    ],
)
def test_select_orchestrator_uses_existing_artifact_context(
    intent: str,
    context_updates: dict[str, object],
) -> None:
    context = {
        "command": "super-organism",
        "execution_family": "general_operator",
        **context_updates,
    }
    choice = select_orchestrator(
        intent,
        context,
    )

    assert choice.orchestrator_id == "super-dan-live-general"
    assert choice.tool_policy["profile"] == "generic"
    assert "intent signal" in choice.rationale
    assert choice.intent_signal.artifact_target == "website"
    assert choice.intent_signal.mutation_permission is True


def test_select_orchestrator_does_not_infer_mutation_from_ui_words_without_context() -> None:
    choice = select_orchestrator(
        "can you think harder, the layout now is completely messy",
        {
            "command": "super-organism",
            "execution_family": "general_operator",
        },
    )

    assert choice.orchestrator_id == "super-dan-showcase"
    assert choice.tool_policy["mode"] == "read-only"
    assert choice.acceptance_policy["requires_live_artifact"] is False
    assert choice.intent_signal.operation == "unknown"
    assert choice.intent_signal.mutation_permission is False


def test_select_orchestrator_ignores_super_organism_research_family_for_website_workspace() -> None:
    choice = select_orchestrator(
        "research visual design evidence for this homepage",
        {
            "command": "super-organism",
            "execution_family": "research",
            "existing_website_workspace": True,
        },
    )

    assert choice.orchestrator_id == "super-dan-live-general"
    assert choice.tool_policy["profile"] == "generic"
    assert "visual" not in choice.matched_cues
    assert choice.intent_signal.operation == "mutate"
    assert choice.intent_signal.artifact_target == "website"


def test_select_orchestrator_does_not_use_super_organism_execution_family_as_gate() -> None:
    research = select_orchestrator(
        "compare market evidence",
        {"command": "super-organism", "execution_family": "research"},
    )
    coding = select_orchestrator(
        "fix the failing test",
        {"command": "super-organism", "execution_family": "code"},
    )

    assert research.orchestrator_id == "super-dan-showcase"
    assert research.acceptance_policy["requires_live_artifact"] is False
    assert research.intent_signal.operation == "unknown"
    assert coding.orchestrator_id == "super-dan-live-general"


def test_select_orchestrator_allows_super_organism_build_cues_on_generic_lane() -> None:
    choice = select_orchestrator(
        "build a market evidence map",
        {"command": "super-organism", "execution_family": "research"},
    )

    assert choice.orchestrator_id == "super-dan-live-general"
    assert choice.tool_policy["profile"] == "generic"
    assert choice.acceptance_policy["requires_live_artifact"] is True


def test_select_orchestrator_fallback_website_cues_are_deterministic() -> None:
    first = select_orchestrator("please create a landing page")
    second = select_orchestrator("please create a landing page")

    assert first == second
    assert first.rationale.endswith("without an LLM classifier")
