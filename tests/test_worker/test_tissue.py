from __future__ import annotations

import asyncio

import pytest

from dan.worker import (
    CellAddress,
    CellAuthorityLimits,
    CellBudgetLimits,
    CellHandoffPacket,
    CompletionSignal,
    ContinuationHooks,
    CrossCellTraceLog,
    EscalationSignal,
    HandoffTask,
    OutputContract,
    SignalTrace,
    TissueMember,
    TissueMergeMode,
    TissuePoolLimits,
    WorkerAuthority,
    WorkerCoreExecutor,
    WorkerDefinition,
    execute_tissue_pattern,
    parallel_worker_pool,
    retrieval_enrichment_pool,
    review_quorum_pool,
)
from dan.worker.tissue import build_tissue_member_packet
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse


def _planner_address() -> CellAddress:
    return CellAddress(
        cell_id="planner-cell",
        tissue_id="coordination",
        organ_id="brain",
        organism_id="demo-organism",
    )


def _coordinator_packet(
    *,
    task_id: str,
    instruction: str,
    tissue_id: str,
    budget_limits: CellBudgetLimits | None = None,
) -> CellHandoffPacket:
    return CellHandoffPacket(
        trace=SignalTrace(trace_id=f"trace:{task_id}", root_task_id=task_id),
        sender=_planner_address(),
        recipient=CellAddress(
            cell_id=f"{tissue_id}-coordinator",
            tissue_id=tissue_id,
            organ_id="brain",
            organism_id="demo-organism",
        ),
        task=HandoffTask(
            task_id=task_id,
            instruction=instruction,
            scope=tissue_id,
            hard_constraints=["Stay within the provided packet scope."],
            input_payload={"seed": task_id},
        ),
        output_contract=OutputContract(
            definition_of_done="Return a compact tissue-level result.",
            expected_return_shape="Compact JSON-like result.",
        ),
        budget_limits=budget_limits
        or CellBudgetLimits(
            max_selected_refs_per_source=1,
            max_expanded_refs_per_source=1,
            max_completion_rounds=1,
            max_runtime_seconds=20,
        ),
        authority_limits=CellAuthorityLimits(
            acting_authority=WorkerAuthority.DELEGATE,
            max_spawned_cells=0,
            allow_delegate=False,
        ),
        continuation_hooks=ContinuationHooks(
            reply_to_cell_id="planner-cell",
            status_topic="organism.status",
            completion_topic="organism.completion",
            escalation_topic="organism.escalation",
        ),
    )


def _member(
    member_id: str,
    *,
    tissue_id: str,
    role: str,
    worker_id: str | None = None,
) -> TissueMember:
    worker_name = worker_id or member_id
    return TissueMember(
        member_id=member_id,
        address=CellAddress(
            cell_id=worker_name,
            tissue_id=tissue_id,
            organ_id="brain",
            organism_id="demo-organism",
        ),
        worker=WorkerDefinition(
            id=worker_name,
            role=role,
            instruction=f"{role} within the tissue boundary.",
            model="stub-model",
        ),
    )


def test_build_tissue_member_packet_prefers_member_output_contract_override() -> None:
    packet = _coordinator_packet(
        task_id="analysis-override",
        instruction="Summarize the evidence.",
        tissue_id="analysis",
    )
    pattern = retrieval_enrichment_pool(
        "analysis-pool",
        members=[
            TissueMember(
                member_id="alpha",
                address=CellAddress(
                    cell_id="analysis-a",
                    tissue_id="analysis",
                    organ_id="brain",
                    organism_id="demo-organism",
                ),
                worker=WorkerDefinition(
                    id="analysis-a",
                    role="analyst",
                    instruction="Return a compact note.",
                    model="stub-model",
                ),
                output_contract_override=OutputContract(
                    definition_of_done="Return one compact member note.",
                    expected_return_shape='{"finding": "<required>"}',
                ),
            )
        ],
        limits=TissuePoolLimits(max_concurrency=1, max_failures=0),
    )

    member_packet = build_tissue_member_packet(
        packet=packet,
        pattern=pattern,
        member=pattern.members[0],
        parent_signal_id="signal-1",
    )

    assert member_packet.output_contract.definition_of_done == "Return one compact member note."
    assert member_packet.output_contract.expected_return_shape == '{"finding": "<required>"}'


class _TissueCompletionProvider:
    def __init__(
        self,
        responses: dict[str, str],
        *,
        delays: dict[str, float] | None = None,
    ) -> None:
        self._responses = dict(responses)
        self._delays = dict(delays or {})
        self.requests: list[CompletionRequest] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")
        self.requests.append(request)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            delay = self._delays.get(worker_id, 0.0)
            if delay:
                await asyncio.sleep(delay)
            return CompletionResponse(
                text=self._responses[worker_id],
                raw={"worker_id": worker_id},
            )
        finally:
            self.in_flight -= 1


@pytest.mark.asyncio
async def test_parallel_worker_pool_reuses_cell_handoffs_and_honors_shared_concurrency() -> None:
    provider = _TissueCompletionProvider(
        {
            "analysis-a": "Alpha summary",
            "analysis-b": "Beta summary",
            "analysis-c": "Gamma summary",
        },
        delays={
            "analysis-a": 0.02,
            "analysis-b": 0.02,
            "analysis-c": 0.02,
        },
    )
    executor = WorkerCoreExecutor(completion_provider=provider)
    pattern = parallel_worker_pool(
        "analysis-pool",
        members=[
            _member("alpha", tissue_id="analysis", role="analyst", worker_id="analysis-a"),
            _member("beta", tissue_id="analysis", role="analyst", worker_id="analysis-b"),
            _member("gamma", tissue_id="analysis", role="analyst", worker_id="analysis-c"),
        ],
        limits=TissuePoolLimits(max_concurrency=2, max_failures=0),
        merge_mode=TissueMergeMode.APPEND,
    )
    packet = _coordinator_packet(
        task_id="analysis-pass",
        instruction="Summarize the evidence from each analysis cell.",
        tissue_id="analysis",
    )
    trace_log = CrossCellTraceLog()

    run = await execute_tissue_pattern(
        executor=executor,
        pattern=pattern,
        packet=packet,
        trace_log=trace_log,
    )

    assert run.result.status == "completed"
    assert provider.max_in_flight == 2
    assert run.result.outputs["result"]["alpha"]["result"] == "Alpha summary"
    assert run.result.outputs["result"]["beta"]["result"] == "Beta summary"
    assert run.result.outputs["result"]["gamma"]["result"] == "Gamma summary"
    assert all(child.sender.cell_id == packet.recipient.cell_id for child in run.member_packets)
    assert all(child.continuation_hooks.reply_to_cell_id == packet.recipient.cell_id for child in run.member_packets)
    assert all(child.trace.parent_packet_id == packet.packet_id for child in run.member_packets)
    assert all(child.trace.parent_signal_id == run.signals[0].signal_id for child in run.member_packets)
    assert run.result.budget_envelope.total_completion_rounds == 3
    assert isinstance(run.signals[-1], CompletionSignal)
    assert len(trace_log.packets_for_trace(packet.trace.trace_id)) == 4
    assert {request.metadata["tissue_member_id"] for request in provider.requests} == {"alpha", "beta", "gamma"}


@pytest.mark.asyncio
async def test_parallel_worker_pool_can_finish_when_one_member_times_out_within_failure_budget() -> None:
    provider = _TissueCompletionProvider(
        {
            "analysis-fast-a": "Alpha summary",
            "analysis-fast-b": "Beta summary",
            "analysis-slow": "Late summary",
        },
        delays={
            "analysis-slow": 1.2,
        },
    )
    executor = WorkerCoreExecutor(completion_provider=provider)
    pattern = parallel_worker_pool(
        "analysis-timeout-pool",
        members=[
            _member("alpha", tissue_id="analysis", role="analyst", worker_id="analysis-fast-a"),
            _member("beta", tissue_id="analysis", role="analyst", worker_id="analysis-fast-b"),
            _member("gamma", tissue_id="analysis", role="analyst", worker_id="analysis-slow"),
        ],
        limits=TissuePoolLimits(max_concurrency=3, max_failures=1),
        merge_mode=TissueMergeMode.APPEND,
    )
    packet = _coordinator_packet(
        task_id="analysis-timeout-pass",
        instruction="Summarize the evidence from each analysis cell.",
        tissue_id="analysis",
        budget_limits=CellBudgetLimits(
            max_selected_refs_per_source=1,
            max_expanded_refs_per_source=1,
            max_completion_rounds=1,
            max_runtime_seconds=1,
        ),
    )

    run = await execute_tissue_pattern(
        executor=executor,
        pattern=pattern,
        packet=packet,
    )

    assert run.result.status == "completed"
    assert run.result.outputs["result"]["alpha"]["result"] == "Alpha summary"
    assert run.result.outputs["result"]["beta"]["result"] == "Beta summary"
    assert run.result.outputs["failed_members"] == {
        "gamma": "runtime_budget_exceeded: analysis-slow exceeded 1s"
    }
    assert run.result.metadata["failed_member_ids"] == ["gamma"]


@pytest.mark.asyncio
async def test_review_quorum_pool_merges_majority_reviews_into_one_reusable_decision() -> None:
    provider = _TissueCompletionProvider(
        {
            "review-a": "approve",
            "review-b": "approve",
            "review-c": "reject",
        }
    )
    executor = WorkerCoreExecutor(completion_provider=provider)
    pattern = review_quorum_pool(
        "review-quorum",
        members=[
            _member("r1", tissue_id="review", role="reviewer", worker_id="review-a"),
            _member("r2", tissue_id="review", role="reviewer", worker_id="review-b"),
            _member("r3", tissue_id="review", role="reviewer", worker_id="review-c"),
        ],
        required_agreement=2,
        limits=TissuePoolLimits(max_concurrency=3, max_failures=1),
    )
    packet = _coordinator_packet(
        task_id="review-pass",
        instruction="Review the draft and return a one-word verdict.",
        tissue_id="review",
    )

    run = await execute_tissue_pattern(
        executor=executor,
        pattern=pattern,
        packet=packet,
    )

    assert run.result.status == "completed"
    assert run.result.quorum is not None
    assert run.result.quorum.decision == "approve"
    assert run.result.quorum.agreement_count == 2
    assert run.result.outputs["result"]["decision"] == "approve"
    assert run.result.outputs["result"]["resolved_by"] == "quorum"
    assert run.result.outputs["result"]["vote_counts"] == {"approve": 2, "reject": 1}
    assert isinstance(run.signals[-1], CompletionSignal)


@pytest.mark.asyncio
async def test_review_quorum_pool_escalates_when_tie_cannot_self_resolve() -> None:
    provider = _TissueCompletionProvider(
        {
            "review-left": "approve",
            "review-right": "reject",
        }
    )
    executor = WorkerCoreExecutor(completion_provider=provider)
    pattern = review_quorum_pool(
        "review-tie",
        members=[
            _member("left", tissue_id="review", role="reviewer", worker_id="review-left"),
            _member("right", tissue_id="review", role="reviewer", worker_id="review-right"),
        ],
        required_agreement=2,
        limits=TissuePoolLimits(max_concurrency=2, max_failures=0),
    )
    packet = _coordinator_packet(
        task_id="review-tie-pass",
        instruction="Review the draft and return a one-word verdict.",
        tissue_id="review",
    )

    run = await execute_tissue_pattern(
        executor=executor,
        pattern=pattern,
        packet=packet,
    )

    assert run.result.status == "escalated"
    assert run.result.quorum is not None
    assert run.result.quorum.vote_counts == {"approve": 1, "reject": 1}
    assert isinstance(run.signals[-1], EscalationSignal)
    assert run.signals[-1].reason == "quorum_tie"


@pytest.mark.asyncio
async def test_review_quorum_pool_normalizes_long_form_votes_via_tie_break_priority() -> None:
    provider = _TissueCompletionProvider(
        {
            "review-left": "repair - needs another bounded pass",
            "review-right": "repair",
        }
    )
    executor = WorkerCoreExecutor(completion_provider=provider)
    pattern = review_quorum_pool(
        "review-normalized",
        members=[
            _member("left", tissue_id="review", role="reviewer", worker_id="review-left"),
            _member("right", tissue_id="review", role="reviewer", worker_id="review-right"),
        ],
        required_agreement=2,
        tie_break_priority=["repair", "pass"],
        limits=TissuePoolLimits(max_concurrency=2, max_failures=0),
    )
    packet = _coordinator_packet(
        task_id="review-normalized-pass",
        instruction="Review the draft and return a one-word verdict.",
        tissue_id="review",
    )

    run = await execute_tissue_pattern(
        executor=executor,
        pattern=pattern,
        packet=packet,
    )

    assert run.result.status == "completed"
    assert run.result.quorum is not None
    assert run.result.quorum.decision == "repair"
    assert run.result.quorum.vote_counts == {"repair": 2}
    assert run.result.outputs["result"]["resolved_by"] == "quorum"


@pytest.mark.asyncio
async def test_review_quorum_pool_falls_back_to_priority_for_nonempty_unrecognized_vote_text() -> None:
    provider = _TissueCompletionProvider(
        {
            "review-left": "this still needs another bounded iteration before merge",
            "review-right": "repair",
        }
    )
    executor = WorkerCoreExecutor(completion_provider=provider)
    pattern = review_quorum_pool(
        "review-fallback-normalized",
        members=[
            _member("left", tissue_id="review", role="reviewer", worker_id="review-left"),
            _member("right", tissue_id="review", role="reviewer", worker_id="review-right"),
        ],
        required_agreement=2,
        tie_break_priority=["repair", "pass"],
        limits=TissuePoolLimits(max_concurrency=2, max_failures=0),
    )
    packet = _coordinator_packet(
        task_id="review-fallback-normalized-pass",
        instruction="Review the draft and return a one-word verdict.",
        tissue_id="review",
    )

    run = await execute_tissue_pattern(
        executor=executor,
        pattern=pattern,
        packet=packet,
    )

    assert run.result.status == "completed"
    assert run.result.quorum is not None
    assert run.result.quorum.decision == "repair"
    assert run.result.quorum.vote_counts == {"repair": 2}
    assert run.result.outputs["result"]["resolved_by"] == "quorum"


@pytest.mark.asyncio
async def test_review_quorum_pool_still_escalates_when_vote_output_is_missing() -> None:
    provider = _TissueCompletionProvider(
        {
            "review-left": "",
            "review-right": "repair",
        }
    )
    executor = WorkerCoreExecutor(completion_provider=provider)
    pattern = review_quorum_pool(
        "review-missing-vote",
        members=[
            _member("left", tissue_id="review", role="reviewer", worker_id="review-left"),
            _member("right", tissue_id="review", role="reviewer", worker_id="review-right"),
        ],
        required_agreement=2,
        tie_break_priority=["repair", "pass"],
        limits=TissuePoolLimits(max_concurrency=2, max_failures=0),
    )
    packet = _coordinator_packet(
        task_id="review-missing-vote-pass",
        instruction="Review the draft and return a one-word verdict.",
        tissue_id="review",
    )

    run = await execute_tissue_pattern(
        executor=executor,
        pattern=pattern,
        packet=packet,
    )

    assert run.result.status == "escalated"
    assert run.result.quorum is not None
    assert run.result.quorum.vote_counts == {"repair": 1}
    assert run.result.quorum.member_votes["left"] == ""
    assert isinstance(run.signals[-1], EscalationSignal)
    assert run.signals[-1].reason == "quorum_not_reached"


@pytest.mark.asyncio
async def test_tissue_pool_escalates_before_dispatch_when_shared_budget_would_be_exceeded() -> None:
    provider = _TissueCompletionProvider(
        {
            "review-a": "approve",
            "review-b": "approve",
            "review-c": "approve",
        }
    )
    executor = WorkerCoreExecutor(completion_provider=provider)
    pattern = parallel_worker_pool(
        "review-budget",
        members=[
            _member("r1", tissue_id="review", role="reviewer", worker_id="review-a"),
            _member("r2", tissue_id="review", role="reviewer", worker_id="review-b"),
            _member("r3", tissue_id="review", role="reviewer", worker_id="review-c"),
        ],
        limits=TissuePoolLimits(
            max_concurrency=2,
            max_failures=0,
            max_total_completion_rounds=5,
        ),
    )
    packet = _coordinator_packet(
        task_id="review-budget-pass",
        instruction="Review the draft and return a one-word verdict.",
        tissue_id="review",
        budget_limits=CellBudgetLimits(
            max_selected_refs_per_source=1,
            max_expanded_refs_per_source=1,
            max_completion_rounds=2,
            max_runtime_seconds=20,
        ),
    )

    run = await execute_tissue_pattern(
        executor=executor,
        pattern=pattern,
        packet=packet,
    )

    assert run.result.status == "escalated"
    assert provider.requests == []
    assert run.signals[1].signal_type == "budget_pressure"
    assert isinstance(run.signals[-1], EscalationSignal)
    assert run.signals[-1].reason == "tissue_budget_exceeded"
    assert run.result.metadata["budget_breaches"]["max_total_completion_rounds"]["projected"] == 6


@pytest.mark.asyncio
async def test_retrieval_enrichment_pool_collects_output_refs_for_downstream_reuse() -> None:
    provider = _TissueCompletionProvider(
        {
            "retriever-a": "Evidence chunk A",
            "retriever-b": "Evidence chunk B",
        }
    )
    executor = WorkerCoreExecutor(completion_provider=provider)
    pattern = retrieval_enrichment_pool(
        "retrieval-pool",
        members=[
            _member("source-a", tissue_id="retrieval", role="retriever", worker_id="retriever-a"),
            _member("source-b", tissue_id="retrieval", role="retriever", worker_id="retriever-b"),
        ],
        limits=TissuePoolLimits(max_concurrency=2, max_failures=0),
    )
    packet = _coordinator_packet(
        task_id="retrieval-pass",
        instruction="Retrieve one evidence chunk from each source.",
        tissue_id="retrieval",
    )

    run = await execute_tissue_pattern(
        executor=executor,
        pattern=pattern,
        packet=packet,
    )

    assert run.result.status == "completed"
    assert len(run.result.outputs["output_refs"]) == 2
    assert len(run.result.outputs["result"]) == 2
    assert run.result.outputs["result"][0]["ref_id"].endswith(":output")
    assert isinstance(run.signals[-1], CompletionSignal)
    assert len(run.signals[-1].output_refs) == 2
