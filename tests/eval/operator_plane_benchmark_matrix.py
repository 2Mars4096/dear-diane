from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OperatorPlaneBenchmarkCase:
    benchmark_id: str
    family: str
    prompt: str
    mode: str
    available_tool_families: tuple[str, ...]
    available_adapters: tuple[str, ...]
    expected_operator_use_case_pack: str
    expected_safety_envelope: str
    expected_supervision_policy: str
    expected_execution_target: str
    expected_capability_sets: tuple[str, ...]
    expected_deterministic_adapters: tuple[str, ...]
    minimum_supervision_loops: int = 2
    scoring_dimensions: tuple[str, ...] = (
        "uncertainty_narrowing",
        "delta_quality",
        "busywork_avoidance",
    )
    fixture_style: str = "family_fixture"
    cross_surface_chain: bool = False


def _benchmark_cases() -> list[OperatorPlaneBenchmarkCase]:
    return [
        OperatorPlaneBenchmarkCase(
            benchmark_id="ask-research-grounded-brief",
            family="ask_research",
            prompt=(
                "Read the local design notes, verify the current API policy online, "
                "and answer with grounded caveats."
            ),
            mode="agent",
            available_tool_families=("files", "web", "browser", "research"),
            available_adapters=(),
            expected_operator_use_case_pack="knowledge_local_context",
            expected_safety_envelope="read_only",
            expected_supervision_policy="continue_with_evidence",
            expected_execution_target="inline_or_specialist",
            expected_capability_sets=("local_context_readers", "grounded_web_readers"),
            expected_deterministic_adapters=(),
        ),
        OperatorPlaneBenchmarkCase(
            benchmark_id="local-mutate-repo-patch",
            family="local_mutate",
            prompt=(
                "Patch the repo, run the focused tests, inspect git state, and prepare "
                "a bounded commit summary."
            ),
            mode="mutate",
            available_tool_families=("files", "shell", "git", "code"),
            available_adapters=(),
            expected_operator_use_case_pack="local_operator",
            expected_safety_envelope="local_mutation",
            expected_supervision_policy="continue_with_local_guards",
            expected_execution_target="bounded_operator_lane",
            expected_capability_sets=("workspace_mutation", "shell_git"),
            expected_deterministic_adapters=(),
        ),
        OperatorPlaneBenchmarkCase(
            benchmark_id="browser-download-artifact",
            family="browser_download",
            prompt=(
                "Open the vendor portal, download the latest artifact, and save it into "
                "the workspace for review."
            ),
            mode="agent",
            available_tool_families=("browser", "files"),
            available_adapters=(),
            expected_operator_use_case_pack="browser_download",
            expected_safety_envelope="local_mutation",
            expected_supervision_policy="continue_with_local_guards",
            expected_execution_target="bounded_operator_lane",
            expected_capability_sets=("browser_navigation", "artifact_downloads"),
            expected_deterministic_adapters=(),
        ),
        OperatorPlaneBenchmarkCase(
            benchmark_id="desktop-messaging-followup",
            family="desktop_messaging",
            prompt=(
                "Focus Telegram Desktop, prepare the follow-up message, and send the update "
                "to the on-call thread."
            ),
            mode="agent",
            available_tool_families=("desktop", "adapters"),
            available_adapters=("telegram",),
            expected_operator_use_case_pack="desktop_messaging",
            expected_safety_envelope="external_side_effect",
            expected_supervision_policy="approval_gate_for_external_side_effects",
            expected_execution_target="bounded_operator_lane",
            expected_capability_sets=("desktop_control", "messaging_adapters"),
            expected_deterministic_adapters=("telegram",),
        ),
        OperatorPlaneBenchmarkCase(
            benchmark_id="cross-surface-download-patch-send",
            family="cross_surface_operator",
            prompt=(
                "Download the incident artifact from the browser, patch the local config, "
                "and send the summary on Telegram."
            ),
            mode="agent",
            available_tool_families=("files", "shell", "git", "browser", "desktop", "adapters"),
            available_adapters=("telegram",),
            expected_operator_use_case_pack="cross_surface_operator",
            expected_safety_envelope="external_side_effect",
            expected_supervision_policy="approval_gate_for_external_side_effects",
            expected_execution_target="bounded_operator_lane",
            expected_capability_sets=(
                "workspace_mutation",
                "shell_git",
                "browser_navigation",
                "artifact_downloads",
                "desktop_control",
                "messaging_adapters",
            ),
            expected_deterministic_adapters=("telegram",),
            cross_surface_chain=True,
        ),
    ]
