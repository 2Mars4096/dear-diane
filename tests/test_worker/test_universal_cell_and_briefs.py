from __future__ import annotations

from dan.worker.brief import RoleSpec, WorkerBrief, render_brief_prompt, request_from_brief
from dan.worker.cell import UNIVERSAL_CELL_SYSTEM_PROMPT, build_cell
from dan.worker.contracts import output_shapes
from dan.worker.contracts.templates import coding_brief, review_brief
from dan.worker.core.contracts import OutputContract, ToolUseContract
from dan.worker.core.executor import WorkerCoreExecutor


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
        scope="src/dan/cli",
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
