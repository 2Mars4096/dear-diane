from __future__ import annotations

from diane.worker.brief import RoleSpec, WorkerBrief, render_brief_prompt, request_from_brief
from diane.worker.cell import UNIVERSAL_CELL_SYSTEM_PROMPT, build_cell
from diane.worker.contracts import output_shapes
from diane.worker.contracts.templates import coding_brief, review_brief
from diane.worker.core.contracts import OutputContract, ToolUseContract
from diane.worker.core.executor import WorkerCoreExecutor


def test_universal_cell_keeps_task_specific_text_out_of_system_prompt() -> None:
    cell = build_cell("stub-model", {"profile": "deterministic"}, "website builder")

    assert cell.llm_hints is not None
    assert cell.llm_hints.system_prompt == UNIVERSAL_CELL_SYSTEM_PROMPT
    assert "website" not in cell.llm_hints.system_prompt.lower()
    assert cell.instruction == ""
    assert cell.persona == ""
    assert cell.role == "website builder"


def test_brief_renderer_owns_task_specific_prompt() -> None:
    brief = coding_brief(
        role=RoleSpec(role_label="builder", responsibility="change files"),
        task="Build the requested CLI feature.",
        scope="src/diane/cli",
        pacing_policy={"safe_file_write_line_limit": 33},
        allowed_tool_ids=["file_read", "file_edit"],
        hard_constraints=["Keep argparse stable."],
    )
    request = request_from_brief(brief)
    cell = build_cell("stub-model", brief.sampling_policy, brief.role.role_label)

    assert "Build the requested CLI feature." in request.metadata["brief_rendered_user_prompt"]
    assert "33 lines" in request.metadata["brief_rendered_user_prompt"]
    assert "Build the requested CLI feature." not in cell.llm_hints.system_prompt
    assert WorkerCoreExecutor._build_user_prompt(request, cell.llm_hints) == request.metadata["brief_rendered_user_prompt"]


def test_request_from_brief_loads_workspace_agents_md(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "# Project Instructions\n\n- Read docs/todo.md before editing.\n- Update docs/changelog.md after changes.\n",
        encoding="utf-8",
    )
    brief = WorkerBrief(
        role=RoleSpec(role_label="workspace_worker", responsibility="change files"),
        task="Make the requested project change.",
        input_payload={"workspace_root": str(tmp_path), "objective": "Make the change."},
    )

    request = request_from_brief(brief)
    prompt = request.metadata["brief_rendered_user_prompt"]

    assert "Workspace instructions loaded from AGENTS.md" in prompt
    assert "Read docs/todo.md before editing." in prompt
    assert "Diane safety and tool boundaries override these workspace instructions." in prompt
    assert request.metadata["workspace_instructions"]["relative_path"] == "AGENTS.md"
    assert len(request.metadata["workspace_instructions"]["content_sha256_prefix"]) == 16


def test_request_from_brief_can_disable_workspace_agents_md(tmp_path, monkeypatch) -> None:
    (tmp_path / "AGENTS.md").write_text("# Project Instructions\n\nDo project tracking.\n", encoding="utf-8")
    monkeypatch.setenv("DAN_WORKSPACE_INSTRUCTIONS", "0")
    brief = WorkerBrief(
        role=RoleSpec(role_label="workspace_worker", responsibility="change files"),
        task="Make the requested project change.",
        input_payload={"workspace_root": str(tmp_path)},
    )

    request = request_from_brief(brief)
    prompt = request.metadata["brief_rendered_user_prompt"]

    assert "Workspace instructions loaded from AGENTS.md" not in prompt
    assert "workspace_instructions" not in request.metadata


def test_reviewer_read_only_policy_is_explicit_not_role_inferred() -> None:
    brief = WorkerBrief(
        role=RoleSpec(role_label="anything", responsibility="review only"),
        task="Review the candidate.",
        tool_policy=ToolUseContract(allowed_tool_ids=["file_read"], preferred_tool_ids=["file_read"]),
        output_contract=OutputContract(
            definition_of_done="Return validation",
            expected_return_shape=output_shapes.validation_v1(),
            output_schema=output_shapes.validation_v1_schema(),
        ),
    )

    request = request_from_brief(brief)

    assert request.tooling.allowed_tool_ids == ["file_read"]
    assert request.output_contract.output_schema == output_shapes.validation_v1_schema()
    assert "Role:" in render_brief_prompt(brief)


def test_template_review_brief_uses_deterministic_sampling() -> None:
    brief = review_brief(
        role={"role_label": "accessibility auditor"},
        task="Audit the page.",
        allowed_tool_ids=["file_read"],
    )

    assert brief.sampling_policy.profile == "deterministic"
    assert brief.tool_policy.allowed_tool_ids == ["file_read"]
