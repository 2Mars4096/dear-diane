"""Composable brief templates for universal cells."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from diane.worker.brief import RoleSpec, WorkerBrief
from diane.worker.contracts import fail_predicates, output_shapes, recovery_hints, snippets
from diane.worker.contracts.sampling import resolve_sampling_policy
from diane.worker.core.contracts import OutputContract, ToolUseContract


def role_brief(
    *,
    role: RoleSpec | Mapping[str, Any],
    task: str,
    scope: str | None = None,
    hard_constraints: Sequence[str] | None = None,
    soft_constraints: Sequence[str] | None = None,
    tool_policy: ToolUseContract | Mapping[str, Any] | None = None,
    output_contract: OutputContract | Mapping[str, Any] | None = None,
    sampling_policy: Mapping[str, Any] | str | None = None,
    contract_snippets: Sequence[str] | None = None,
    fail_predicates_: Sequence[str] | None = None,
    recovery_hints_: Sequence[str] | None = None,
    runtime_policy: Mapping[str, Any] | None = None,
    validation_policy: Mapping[str, Any] | None = None,
    prompt_slots: Mapping[str, Any] | None = None,
    evidence: Sequence[Any] | None = None,
    context_packet: Mapping[str, Any] | None = None,
    input_payload: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> WorkerBrief:
    resolved_role = role if isinstance(role, RoleSpec) else RoleSpec.model_validate(dict(role))
    resolved_tool_policy = (
        tool_policy if isinstance(tool_policy, ToolUseContract) else ToolUseContract.model_validate(tool_policy or {})
    )
    resolved_output_contract = (
        output_contract
        if isinstance(output_contract, OutputContract)
        else OutputContract.model_validate(output_contract or {})
    )
    return WorkerBrief(
        role=resolved_role,
        task=task,
        scope=scope,
        hard_constraints=list(hard_constraints or []),
        soft_constraints=list(soft_constraints or []),
        tool_policy=resolved_tool_policy,
        output_contract=resolved_output_contract,
        sampling_policy=resolve_sampling_policy(sampling_policy),
        contract_snippets=list(contract_snippets or []),
        fail_predicates=list(fail_predicates_ or []),
        recovery_hints=list(recovery_hints_ or []),
        runtime_policy=dict(runtime_policy or {}),
        validation_policy=dict(validation_policy or {}),
        prompt_slots=dict(prompt_slots or {}),
        evidence=list(evidence or []),
        context_packet=dict(context_packet or {}),
        input_payload=dict(input_payload or {}),
        metadata=dict(metadata or {}),
    )


def coding_brief(
    *,
    role: RoleSpec | Mapping[str, Any],
    task: str,
    pacing_policy: Mapping[str, Any] | None = None,
    allowed_tool_ids: Sequence[str] | None = None,
    **kwargs: Any,
) -> WorkerBrief:
    tool_policy = kwargs.pop("tool_policy", None) or {
        "allowed_tool_ids": list(allowed_tool_ids or []),
        "preferred_tool_ids": ["file_edit", "file_write", "file_read"],
    }
    return role_brief(
        role=role,
        task=task,
        tool_policy=tool_policy,
        output_contract=kwargs.pop(
            "output_contract",
            OutputContract(
                definition_of_done="Return a material coding candidate that satisfies the brief.",
                expected_return_shape=output_shapes.coding_v1(),
                output_schema=output_shapes.coding_v1_schema(),
            ),
        ),
        contract_snippets=[
            snippets.pacing_contract(pacing_policy),
            snippets.incremental_edit_contract(),
            snippets.no_scratch_files_contract(),
            *list(kwargs.pop("contract_snippets", []) or []),
        ],
        recovery_hints_=[
            recovery_hints.split_oversized_write(),
            recovery_hints.prefer_file_edit(),
            recovery_hints.switch_to_grounded_read(),
            *list(kwargs.pop("recovery_hints_", []) or []),
        ],
        **kwargs,
    )


def review_brief(
    *,
    role: RoleSpec | Mapping[str, Any],
    task: str,
    allowed_tool_ids: Sequence[str] | None = None,
    failure_phrases: Sequence[str] | None = None,
    **kwargs: Any,
) -> WorkerBrief:
    return role_brief(
        role=role,
        task=task,
        tool_policy=kwargs.pop(
            "tool_policy",
            {
                "allowed_tool_ids": list(allowed_tool_ids or []),
                "preferred_tool_ids": list(allowed_tool_ids or []),
            },
        ),
        output_contract=kwargs.pop(
            "output_contract",
            OutputContract(
                definition_of_done="Return a read-only validation report.",
                expected_return_shape=output_shapes.validation_v1(),
                output_schema=output_shapes.validation_v1_schema(),
            ),
        ),
        contract_snippets=[
            snippets.read_only_contract(allowed_tool_ids),
            *list(kwargs.pop("contract_snippets", []) or []),
        ],
        fail_predicates_=[
            fail_predicates.anti_template_predicate(failure_phrases or []),
            *list(kwargs.pop("fail_predicates_", []) or []),
        ],
        sampling_policy=kwargs.pop("sampling_policy", "deterministic"),
        **kwargs,
    )


def research_brief(
    *,
    role: RoleSpec | Mapping[str, Any],
    task: str,
    allowed_tool_ids: Sequence[str] | None = None,
    **kwargs: Any,
) -> WorkerBrief:
    return role_brief(
        role=role,
        task=task,
        tool_policy=kwargs.pop(
            "tool_policy",
            {
                "allowed_tool_ids": list(allowed_tool_ids or []),
                "preferred_tool_ids": list(allowed_tool_ids or []),
            },
        ),
        output_contract=kwargs.pop(
            "output_contract",
            OutputContract(
                definition_of_done="Return grounded evidence notes with explicit uncertainty.",
                expected_return_shape=output_shapes.research_evidence_v1(),
            ),
        ),
        contract_snippets=[
            snippets.read_only_contract(allowed_tool_ids),
            "Ground claims in supplied or retrieved evidence and label uncertainty explicitly.",
            *list(kwargs.pop("contract_snippets", []) or []),
        ],
        **kwargs,
    )


def scheduler_brief(
    *,
    role: RoleSpec | Mapping[str, Any],
    task: str,
    **kwargs: Any,
) -> WorkerBrief:
    return role_brief(
        role=role,
        task=task,
        output_contract=kwargs.pop(
            "output_contract",
            OutputContract(
                definition_of_done="Return an admissible scheduling proposal or a stop decision.",
                expected_return_shape=output_shapes.scheduler_proposal_v1(),
            ),
        ),
        sampling_policy=kwargs.pop("sampling_policy", "deterministic"),
        **kwargs,
    )
