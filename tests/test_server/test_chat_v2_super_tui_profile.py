from dan.server.routers.chat_v2 import (
    AgentRunExecuteRequest,
    _execute_backend_name,
    _execute_overrides,
)


def test_super_tui_surface_profile_applies_shared_agent_defaults() -> None:
    request = AgentRunExecuteRequest(
        surface_profile="super-tui",
        profile_policy={"latency": "fast"},
        tool_policy={"max_tool_calls": 64},
        metadata={"surface": "gui:chunk-workspace"},
    )

    assert _execute_backend_name(request) == "super_dan"

    overrides = _execute_overrides(request)
    assert overrides["profile_policy"] == {"backend": "super_dan", "latency": "fast"}
    assert overrides["mutation_policy"]["mode"] == "workspace_mutation"
    assert overrides["mutation_policy"]["permission"] == "workspace_mutation"
    assert overrides["approval_policy"]["mode"] == "auto_within_workspace"
    assert overrides["tool_policy"] == {"max_tool_calls": 64}
    assert overrides["metadata"]["surface"] == "gui:chunk-workspace"
    assert overrides["metadata"]["surface_profile"] == "super_tui"
    assert overrides["metadata"]["gui_for"] == "dan super-tui"
