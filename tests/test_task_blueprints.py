from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from diane.task_blueprints import (
    AcceptanceCriterion,
    BlueprintEdge,
    BlueprintNode,
    BlueprintPatchOperation,
    ContractAmendment,
    ExecutionResult,
    LoopPolicy,
    TaskContract,
    apply_blueprint_patch,
    create_blueprint,
    execution_attempt_for_blueprint,
    make_blueprint_patch,
    replay_blueprint_events,
    revise_execution_attempt,
    sync_blueprint_graph,
)

FAMILIES = (
    "direct",
    "debugging",
    "research",
    "design",
    "meeting",
    "manufacturing",
    "general",
)


def _criterion(
    criterion_id: str = "done",
    *,
    description: str = "The requested outcome is demonstrably complete.",
    required: bool = True,
    evidence_required: tuple[str, ...] = ("validation",),
    approval_required: bool = False,
) -> AcceptanceCriterion:
    return AcceptanceCriterion(
        criterion_id=criterion_id,
        description=description,
        required=required,
        evidence_required=evidence_required,
        approval_required=approval_required,
    )


def _contract(
    *,
    permissions: tuple[str, ...] = ("workspace:read", "workspace:write"),
    criteria: tuple[AcceptanceCriterion, ...] | None = None,
) -> TaskContract:
    return TaskContract(
        goal="Deliver a verified result without changing the goal.",
        non_goals=("Do not publish externally.",),
        constraints=("Preserve provenance.",),
        permissions=permissions,
        acceptance_criteria=criteria if criteria is not None else (_criterion(),),
    )


def _operation(op: str, **data: object) -> BlueprintPatchOperation:
    return BlueprintPatchOperation(op=op, data=data)


def _apply(
    current, operation: BlueprintPatchOperation, *, reason: str = "test revision"
):
    patch = make_blueprint_patch(
        current,
        (operation,),
        author="test",
        reason=reason,
    )
    return apply_blueprint_patch(current, patch)


@pytest.mark.parametrize("family", FAMILIES)
def test_all_task_families_build_typed_completeable_topologies(family: str) -> None:
    first = create_blueprint(
        task_id=f"task-{family}", contract=_contract(), family=family
    )
    second = create_blueprint(
        task_id=f"task-{family}", contract=_contract(), family=family
    )

    assert first.schema_version == "dan_task_blueprint_v1"
    assert first.model_dump(mode="json")["schema"] == "dan_task_blueprint_v1"
    assert first.family == family
    assert first.nodes
    assert first.derived_state.uncovered_criterion_ids == ()
    assert tuple(node.node_id for node in first.nodes) == tuple(
        node.node_id for node in second.nodes
    )
    assert tuple(edge.edge_id for edge in first.edges) == tuple(
        edge.edge_id for edge in second.edges
    )
    assert all(edge.source_node_id != edge.target_node_id for edge in first.edges)


@pytest.mark.parametrize("family", ("debugging", "design", "manufacturing"))
def test_iterative_families_use_only_explicit_bounded_feedback(family: str) -> None:
    blueprint = create_blueprint(
        task_id=f"loop-{family}", contract=_contract(), family=family
    )
    loops = {node.node_id: node for node in blueprint.nodes if node.kind == "loop"}
    feedback = [edge for edge in blueprint.edges if edge.kind == "feedback"]

    assert loops
    assert feedback
    assert blueprint.derived_state.bounded_loop_node_ids == tuple(loops)
    for edge in feedback:
        assert edge.loop_node_id in loops
        assert loops[edge.loop_node_id].loop_policy.max_iterations > 0
        assert loops[edge.loop_node_id].loop_policy.exit_condition


def test_unbounded_dependency_cycle_is_rejected() -> None:
    left = BlueprintNode(node_id="left", title="Left", topology_role="work")
    right = BlueprintNode(node_id="right", title="Right", topology_role="work")
    edges = (
        BlueprintEdge(
            edge_id="left-right", source_node_id="left", target_node_id="right"
        ),
        BlueprintEdge(
            edge_id="right-left", source_node_id="right", target_node_id="left"
        ),
    )

    with pytest.raises(ValueError, match="unbounded dependency cycle"):
        create_blueprint(
            task_id="cycle",
            contract=_contract(criteria=()),
            family="general",
            nodes=(left, right),
            edges=edges,
        )


def test_feedback_requires_a_real_bounded_loop_node() -> None:
    left = BlueprintNode(node_id="left", title="Left", topology_role="work")
    right = BlueprintNode(node_id="right", title="Right", topology_role="work")
    fake_loop = BlueprintNode(node_id="fake", title="Not a loop", topology_role="work")
    edge = BlueprintEdge(
        edge_id="feedback",
        source_node_id="right",
        target_node_id="left",
        kind="feedback",
        loop_node_id="fake",
    )

    with pytest.raises(ValueError, match="not bound to a bounded loop"):
        create_blueprint(
            task_id="fake-loop",
            contract=_contract(criteria=()),
            nodes=(left, right, fake_loop),
            edges=(edge,),
        )

    with pytest.raises(ValidationError, match="greater than 0"):
        LoopPolicy(max_iterations=0, exit_condition="done")


def test_bounded_feedback_does_not_create_a_dependency_cycle() -> None:
    draft = BlueprintNode(node_id="draft", title="Draft", topology_role="work")
    review = BlueprintNode(
        node_id="review", title="Review", topology_role="gate", kind="gate"
    )
    loop = BlueprintNode(
        node_id="loop",
        title="Revise",
        topology_role="bounded_loop",
        kind="loop",
        loop_policy=LoopPolicy(max_iterations=2, exit_condition="review passes"),
    )
    blueprint = create_blueprint(
        task_id="bounded-loop",
        contract=_contract(criteria=()),
        nodes=(draft, review, loop),
        edges=(
            BlueprintEdge(
                edge_id="draft-review", source_node_id="draft", target_node_id="review"
            ),
            BlueprintEdge(
                edge_id="review-loop", source_node_id="review", target_node_id="loop"
            ),
            BlueprintEdge(
                edge_id="loop-draft",
                source_node_id="loop",
                target_node_id="draft",
                kind="feedback",
                loop_node_id="loop",
            ),
        ),
    )

    assert blueprint.derived_state.bounded_loop_node_ids == ("loop",)


def test_stale_patch_is_rejected_and_prior_revision_remains_immutable() -> None:
    original = create_blueprint(task_id="stale", contract=_contract(), family="general")
    extra = BlueprintNode(node_id="extra", title="Extra check", topology_role="check")
    patch = make_blueprint_patch(
        original,
        (_operation("add_node", node=extra),),
        author="planner",
        reason="new evidence needs another check",
    )
    updated = apply_blueprint_patch(original, patch).current

    assert original.revision == 1
    assert original.parent_revision_ids == ()
    assert "extra" not in {node.node_id for node in original.nodes}
    assert updated.revision == 2
    assert updated.parent_revision_ids == (original.revision_id,)
    with pytest.raises(ValueError, match="stale blueprint patch"):
        apply_blueprint_patch(updated, patch)
    with pytest.raises((ValidationError, FrozenInstanceError, TypeError)):
        original.revision = 99


def test_ordinary_patch_can_narrow_but_not_widen_permissions() -> None:
    original = create_blueprint(
        task_id="permissions", contract=_contract(), family="direct"
    )
    narrowed = _apply(
        original,
        _operation("narrow_permissions", permissions=("workspace:read",)),
    ).current

    assert narrowed.contract.permissions == ("workspace:read",)
    assert original.contract.permissions == ("workspace:read", "workspace:write")
    with pytest.raises(ValueError, match="only narrow permissions"):
        _apply(
            narrowed,
            _operation(
                "narrow_permissions",
                permissions=("workspace:read", "external:publish"),
            ),
        )


def test_permission_widening_requires_explicit_contract_approval_bound_to_revision() -> (
    None
):
    original = create_blueprint(
        task_id="approved-permissions", contract=_contract(), family="direct"
    )
    amendment = ContractAmendment(
        permissions=("workspace:read", "workspace:write", "external:publish"),
        authority="user",
        reason="User explicitly approved publication for this task.",
        approval_id="approval-1",
        approval_scope_hash=original.contract_hash,
    )
    patch = make_blueprint_patch(
        original,
        (),
        author="user",
        reason="Approved authority expansion.",
        contract_amendment=amendment,
    )
    updated = apply_blueprint_patch(original, patch).current

    assert "external:publish" in updated.contract.permissions
    stale_approval = amendment.model_copy(update={"approval_scope_hash": "wrong"})
    stale_patch = make_blueprint_patch(
        original,
        (),
        author="user",
        reason="Bad scope.",
        contract_amendment=stale_approval,
    )
    with pytest.raises(ValueError, match="not bound to the current contract"):
        apply_blueprint_patch(original, stale_patch)


def test_acceptance_criteria_cannot_be_removed_weakened_or_rewritten() -> None:
    original = create_blueprint(
        task_id="criteria", contract=_contract(), family="direct"
    )
    before = original.contract.acceptance_criteria[0]

    for replacement in (
        (),
        (before.model_copy(update={"required": False}),),
        (before.model_copy(update={"description": "Something easier."}),),
        (before.model_copy(update={"evidence_required": ()}),),
    ):
        amendment = ContractAmendment(
            acceptance_criteria=replacement,
            authority="user",
            reason="Attempted weakening.",
            approval_id="approval-criteria",
            approval_scope_hash=original.contract_hash,
        )
        patch = make_blueprint_patch(
            original,
            (),
            author="user",
            reason="Attempted criterion change.",
            contract_amendment=amendment,
        )
        with pytest.raises(ValueError, match="cannot|weakened"):
            apply_blueprint_patch(original, patch)


def test_planned_node_revision_cannot_detach_a_previously_covered_criterion() -> None:
    original = create_blueprint(
        task_id="criterion-coverage", contract=_contract(), family="direct"
    )
    criterion_node = next(node for node in original.nodes if node.criterion_ids)
    detached = criterion_node.model_copy(update={"criterion_ids": ()})
    patch = make_blueprint_patch(
        original,
        (_operation("revise_node", node=detached),),
        author="planner",
        reason="Attempted criterion detachment.",
    )

    with pytest.raises(ValueError, match="criterion|acceptance|coverage|weaken"):
        apply_blueprint_patch(original, patch)


@pytest.mark.parametrize("state", ("active", "completed"))
def test_active_and_completed_nodes_are_pinned_against_semantic_rewrite(
    state: str,
) -> None:
    original = create_blueprint(
        task_id=f"pinned-{state}",
        contract=_contract(),
        family="general",
    )
    target = original.nodes[0]
    pinned = sync_blueprint_graph(
        original,
        original.nodes,
        original.edges,
        author="runtime",
        reason=f"Mark node {state}.",
        node_states={target.node_id: state},
    ).current
    rewritten = target.model_copy(update={"title": "Silently rewritten"})
    proposed = tuple(
        rewritten if node.node_id == target.node_id else node for node in pinned.nodes
    )

    with pytest.raises(ValueError, match="pinned node"):
        sync_blueprint_graph(
            pinned,
            proposed,
            pinned.edges,
            author="planner",
            reason="Unsafe hot rewrite.",
        )
    patch = make_blueprint_patch(
        pinned,
        (_operation("revise_node", node=rewritten),),
        author="planner",
        reason="Unsafe direct rewrite.",
    )
    with pytest.raises(ValueError, match="pinned to its execution attempt"):
        apply_blueprint_patch(pinned, patch)


def test_active_node_can_progress_to_completed_but_not_back_to_pending() -> None:
    original = create_blueprint(
        task_id="state-progression", contract=_contract(), family="general"
    )
    target = original.nodes[0]
    active = sync_blueprint_graph(
        original,
        original.nodes,
        original.edges,
        author="runtime",
        reason="Started.",
        node_states={target.node_id: "active"},
    ).current
    completed = sync_blueprint_graph(
        active,
        active.nodes,
        active.edges,
        author="runtime",
        reason="Finished.",
        node_states={target.node_id: "completed"},
    ).current

    assert target.node_id in completed.derived_state.completed_node_ids
    with pytest.raises(ValueError, match="cannot be downgraded"):
        sync_blueprint_graph(
            completed,
            completed.nodes,
            completed.edges,
            author="planner",
            reason="Unsafe downgrade.",
            node_states={target.node_id: "planned"},
        )


def test_supersede_split_and_merge_preserve_stable_lineage() -> None:
    base_nodes = (
        BlueprintNode(node_id="a", title="A", topology_role="work"),
        BlueprintNode(node_id="b", title="B", topology_role="work"),
        BlueprintNode(node_id="c", title="C", topology_role="work"),
    )
    original = create_blueprint(
        task_id="lineage",
        contract=_contract(criteria=()),
        nodes=base_nodes,
        edges=(),
    )
    superseded = _apply(
        original,
        _operation(
            "supersede_node",
            node_id="a",
            replacement=BlueprintNode(node_id="a2", title="A2", topology_role="work"),
        ),
    ).current
    by_id = {node.node_id: node for node in superseded.nodes}
    assert not by_id["a"].active
    assert by_id["a"].superseded_by == ("a2",)
    assert by_id["a2"].supersedes == ("a",)

    split = _apply(
        superseded,
        _operation(
            "split_node",
            node_id="b",
            nodes=(
                BlueprintNode(node_id="b1", title="B1", topology_role="work"),
                BlueprintNode(node_id="b2", title="B2", topology_role="work"),
            ),
        ),
    ).current
    by_id = {node.node_id: node for node in split.nodes}
    assert by_id["b"].superseded_by == ("b1", "b2")
    assert by_id["b1"].supersedes == ("b",)
    assert by_id["b2"].supersedes == ("b",)

    merged = _apply(
        split,
        _operation(
            "merge_nodes",
            node_ids=("b1", "b2"),
            merged_node=BlueprintNode(node_id="b12", title="B12", topology_role="work"),
        ),
    ).current
    by_id = {node.node_id: node for node in merged.nodes}
    assert set(by_id["b12"].supersedes) == {"b1", "b2"}
    assert by_id["b1"].superseded_by == ("b12",)
    assert by_id["b2"].superseded_by == ("b12",)
    assert original.revision == 1
    assert merged.revision == 4


def test_completed_node_reopens_as_new_work_without_rewriting_history() -> None:
    original = create_blueprint(
        task_id="reopen", contract=_contract(), family="general"
    )
    target = original.nodes[0]
    completed = sync_blueprint_graph(
        original,
        original.nodes,
        original.edges,
        author="runtime",
        reason="Completed original node.",
        node_states={target.node_id: "completed"},
    ).current
    reopened = BlueprintNode(
        node_id=f"{target.node_id}:reopen-1",
        title=f"Reopen {target.title}",
        topology_role="reopened_work",
    )
    updated = _apply(
        completed,
        _operation("reopen_node", node_id=target.node_id, reopened_node=reopened),
    ).current
    by_id = {node.node_id: node for node in updated.nodes}

    assert updated.derived_state.node_states[target.node_id] == "completed"
    assert by_id[reopened.node_id].supersedes == (target.node_id,)
    assert updated.derived_state.node_states[reopened.node_id] == "ready"


def test_revision_events_replay_to_the_same_immutable_snapshot() -> None:
    original = create_blueprint(
        task_id="replay", contract=_contract(criteria=()), family="general"
    )
    first = _apply(
        original,
        _operation(
            "add_node",
            node=BlueprintNode(node_id="audit", title="Audit", topology_role="audit"),
        ),
        reason="Add audit.",
    )
    second = _apply(
        first.current,
        _operation("attach_evidence", node_id="audit", evidence_refs=("evidence:1",)),
        reason="Attach evidence.",
    )

    replayed = replay_blueprint_events((first.event, second.event))
    assert replayed == second.current
    assert first.current.revision == 2
    with pytest.raises(ValueError, match="duplicate blueprint revision"):
        replay_blueprint_events((first.event, first.event))
    with pytest.raises(ValueError, match="lineage is discontinuous"):
        replay_blueprint_events((second.event, first.event))


def test_multiple_execution_attempts_remain_pinned_to_exact_blueprint_revisions() -> (
    None
):
    original = create_blueprint(
        task_id="attempts", contract=_contract(), family="direct"
    )
    attempt_one = execution_attempt_for_blueprint(
        original,
        attempt_id="attempt-1",
        run_id="run-1",
        worker_summary=("worker-a",),
        model_summary=("model-a",),
        tool_summary=("workspace-read",),
        max_retries=1,
    )
    changed = _apply(
        original,
        _operation(
            "add_node",
            node=BlueprintNode(
                node_id="extra-validation",
                title="Extra validation",
                topology_role="gate",
                kind="gate",
            ),
        ),
        reason="Evidence requires an extra validation node.",
    ).current
    attempt_two = execution_attempt_for_blueprint(
        changed,
        attempt_id="attempt-2",
        run_id="run-2",
        worker_summary=("worker-b", "worker-c"),
        model_summary=("model-b",),
    )
    running_one = revise_execution_attempt(
        attempt_one,
        status="running",
        phase="executing",
        now="2026-07-21T00:00:00+00:00",
    )
    completed_one = revise_execution_attempt(
        running_one,
        status="completed",
        results=(
            ExecutionResult(
                result_id="result-1",
                status="completed",
                artifact_refs=("artifact:one",),
                evidence_refs=("evidence:one",),
            ),
        ),
        now="2026-07-21T00:01:00+00:00",
    )

    assert attempt_one.blueprint_revision_id == original.revision_id
    assert attempt_two.blueprint_revision_id == changed.revision_id
    assert attempt_one.status == "pending"
    assert completed_one.status == "completed"
    assert completed_one.phase == "completed"
    assert completed_one.results[0].artifact_refs == ("artifact:one",)
    assert attempt_two.policy_snapshot.permission_scope == changed.contract.permissions
    assert attempt_one.policy_snapshot.contract_hash == original.contract_hash
