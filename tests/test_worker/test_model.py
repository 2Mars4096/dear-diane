from __future__ import annotations

import pytest

from dan.models.context import (
    CompactionRule,
    ContextProjection,
    MergeStrategy,
    NodeLocalState,
)
from dan.models.control_flow import SpawnPolicy
from dan.models.graph import Graph
from dan.models.legacy import ValidationRule
from dan.models.node_taxonomy import (
    BODY_GRAPH_RUNTIME_NODE_TYPES,
    RUNTIME_NODE_TYPE_MAP,
    RUNTIME_NODE_TYPES,
)
from dan.worker import (
    AuthorityPolicy,
    CellAddress,
    CellAuthorityLimits,
    CellBudgetLimits,
    CellHandoffPacket,
    CommunicationChannelKind,
    CompletionSignal,
    ContinuationHooks,
    ControlFlowConfig,
    ContextBindings,
    CrossCellTraceLog,
    EvidenceRef,
    ExecutionSemantics,
    HandoffTask,
    LLMHints,
    OutputContract,
    SignalTrace,
    Worker,
    WorkerAuthority,
    WorkerCoreExecutor,
    WorkerDefinition,
    execute_cell_handoff,
    make_budget_pressure_signal,
    make_escalation_signal,
    make_status_signal,
    make_warning_signal,
)
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse


def test_bare_worker_defaults_to_input_and_result_ports() -> None:
    worker = Worker(id="w", name="Worker")

    assert worker.node_type == "worker"
    assert [port.name for port in worker.input_ports] == ["input"]
    assert [port.name for port in worker.output_ports] == ["result"]


def test_worker_round_trip_preserves_refs_and_policies() -> None:
    worker = Worker(
        id="reviewer",
        name="Reviewer",
        role="reviewer",
        instruction="Review carefully",
        authority=WorkerAuthority.DELEGATE,
        model="test-model",
        context=ContextBindings(
            instruction_profile_ref="review_profile",
            memory_policy_ref="memory_default",
            context_bundle_refs=["ops_bundle"],
            provider_policy_ref="economy",
            retry_policy_ref="retry_low",
            toolset_refs=["safe_tools"],
        ),
        authority_policy=AuthorityPolicy(
            max_spawned_workers=2,
            task_tier_cap="routine",
            allow_delegate=True,
            allow_memory_write_scopes=["memory.team"],
            allowed_toolset_refs=["safe_tools"],
        ),
        execution=ExecutionSemantics(
            resource_locks=["team_writer"],
            blocking_mode="exclusive",
            await_subworkers=True,
            parallelism_override=2,
        ),
        llm_hints=LLMHints(
            prompt_template="Review {input}",
            temperature=0.1,
            task_tier="routine",
        ),
        body_graph="review_body",
        sub_workers={"research": "review_research"},
        input_mappings={"draft": "inner_draft"},
        output_mappings={"inner_summary": "summary"},
        parallelism=2,
        merge_strategy=MergeStrategy.LAST_WRITE_WINS,
        spawn_policy=SpawnPolicy(max_spawns_per_node=2, max_total_children=4),
        validation_rules=[ValidationRule(rule_type="required_keys", config={"keys": ["summary"]})],
        input_ports=[{"name": "input", "required": False}],
        output_ports=[{"name": "result"}],
    )

    payload = worker.model_dump(mode="json", exclude_none=False)
    rebuilt = Worker.model_validate(payload)

    assert rebuilt.context is not None
    assert rebuilt.context.instruction_profile_ref == "review_profile"
    assert rebuilt.context.memory_policy_ref == "memory_default"
    assert rebuilt.context.context_bundle_refs == ["ops_bundle"]
    assert rebuilt.authority_policy is not None
    assert rebuilt.authority_policy.allow_memory_write_scopes == ["memory.team"]
    assert rebuilt.authority_policy.allowed_toolset_refs == ["safe_tools"]
    assert rebuilt.execution is not None
    assert rebuilt.execution.resource_locks == ["team_writer"]
    assert rebuilt.llm_hints is not None
    assert rebuilt.llm_hints.prompt_template == "Review {input}"
    assert rebuilt.body_graph == "review_body"
    assert rebuilt.sub_workers == {"research": "review_research"}
    assert rebuilt.input_mappings == {"draft": "inner_draft"}
    assert rebuilt.output_mappings == {"inner_summary": "summary"}
    assert rebuilt.parallelism == 2
    assert rebuilt.merge_strategy == MergeStrategy.LAST_WRITE_WINS
    assert rebuilt.spawn_policy is not None
    assert rebuilt.spawn_policy.max_spawns_per_node == 2
    assert rebuilt.validation_rules == [ValidationRule(rule_type="required_keys", config={"keys": ["summary"]})]


@pytest.mark.parametrize(
    ("variant", "payload"),
    [
        pytest.param(
            "llm",
            {
                "id": "draft",
                "name": "Draft",
                "model": "stub-model",
                "llm_hints": {
                    "prompt_template": "Draft {input}",
                    "system_prompt": "Stay concise.",
                    "temperature": 0.1,
                    "max_tokens": 128,
                    "task_tier": "routine",
                },
                "input_ports": [{"name": "input", "required": False}],
                "output_ports": [{"name": "text"}],
            },
            id="llm",
        ),
        pytest.param(
            "tool",
            {
                "id": "search",
                "name": "Search",
                "tool_ids": ["web_search"],
                "context": {
                    "toolset_refs": ["default_tools"],
                    "inherit_defaults": False,
                },
                "metadata": {"tool_config": {"query": "{input}", "limit": 3}},
                "input_ports": [{"name": "input", "required": False}],
                "output_ports": [{"name": "result"}],
            },
            id="tool",
        ),
        pytest.param(
            "code",
            {
                "id": "score",
                "name": "Score",
                "code": "result = score + 1",
                "language": "python",
                "input_ports": [{"name": "score", "required": True}],
                "output_ports": [{"name": "result"}],
            },
            id="code",
        ),
        pytest.param(
            "composite",
            {
                "id": "review",
                "name": "Review",
                "body_graph": "review_body",
                "sub_workers": {"critic": "review_critic"},
                "input_mappings": {"draft": "entry::draft"},
                "output_mappings": {"summary": "result"},
                "parallelism": 2,
                "merge_strategy": "last_write_wins",
                "spawn_policy": {"max_spawns_per_node": 2},
                "external_input_schema": {
                    "type": "object",
                    "properties": {"draft": {"type": "string"}},
                    "required": ["draft"],
                },
                "external_output_schema": {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                },
                "control_state_schema": {
                    "type": "object",
                    "properties": {"iteration": {"type": "integer"}},
                },
                "local_state": {
                    "json_schema": {
                        "type": "object",
                        "properties": {"history": {"type": "array"}},
                    },
                    "description": "Review-local state",
                },
                "compaction_rule": {
                    "strategy": "sliding_window",
                    "window_size": 2,
                },
                "failure_policy": {
                    "max_iterations": 3,
                    "stagnation_threshold": 2,
                },
                "projections": [
                    {
                        "name": "reviewer_view",
                        "context_keys": ["memory.team"],
                        "local_state_keys": ["history"],
                    }
                ],
                "boundary_contract": {
                    "external_input_schema": {"type": "object", "properties": {"draft": {"type": "string"}}},
                    "external_output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
                },
                "input_ports": [{"name": "draft", "required": False}],
                "output_ports": [{"name": "summary"}],
            },
            id="composite",
        ),
        pytest.param(
            "gate",
            {
                "id": "route",
                "name": "Route",
                "control_flow": {
                    "condition": "score > 0.5",
                    "gate_mode": "while",
                    "max_iterations": 4,
                    "state_schema": {"type": "object", "properties": {"score": {"type": "number"}}},
                    "state_defaults": {"score": 0.0},
                },
                "input_ports": [{"name": "score", "required": False}],
                "output_ports": [{"name": "continue"}, {"name": "done"}],
            },
            id="gate",
        ),
        pytest.param(
            "validator",
            {
                "id": "validate",
                "name": "Validate",
                "role": "validator",
                "validation_rules": [{"rule_type": "required_keys", "config": {"keys": ["summary"]}}],
                "metadata": {"validator_on_failure": "route", "validator_strict_mode": True},
                "input_ports": [{"name": "data", "required": False}],
                "output_ports": [{"name": "valid"}, {"name": "invalid"}],
            },
            id="validator",
        ),
    ],
)
def test_worker_json_round_trip_preserves_variant_payloads(
    variant: str,
    payload: dict[str, object],
) -> None:
    worker = Worker.model_validate(payload)

    serialized = worker.model_dump(mode="json", exclude_none=False)
    rebuilt = Worker.model_validate(serialized)

    assert rebuilt.model_dump(mode="json", exclude_none=False) == serialized, variant


def test_graph_union_deserializes_worker_nodes() -> None:
    graph = Graph.model_validate(
        {
            "nodes": [
                {
                    "node_type": "worker",
                    "id": "w1",
                    "name": "Worker 1",
                    "role": "reviewer",
                    "context": {
                        "instruction_profile_ref": "review_profile",
                    },
                    "authority_policy": {
                        "allow_memory_write_scopes": ["memory.public"],
                    },
                    "execution": {
                        "resource_locks": ["team_writer"],
                    },
                    "input_ports": [{"name": "input", "required": False}],
                    "output_ports": [{"name": "result"}],
                }
            ],
            "edges": [],
            "entry_points": ["w1"],
            "exit_points": ["w1"],
        }
    )

    assert isinstance(graph.nodes[0], Worker)
    assert graph.nodes[0].context is not None
    assert graph.nodes[0].context.instruction_profile_ref == "review_profile"


def test_worker_is_visible_in_runtime_taxonomy_and_body_graph_sets() -> None:
    assert "worker" in RUNTIME_NODE_TYPES
    assert RUNTIME_NODE_TYPE_MAP["worker"] is Worker
    assert "worker" in BODY_GRAPH_RUNTIME_NODE_TYPES


def test_worker_model_json_schema_exposes_context_and_policy_fields() -> None:
    schema = Worker.model_json_schema()
    properties = schema["properties"]

    assert properties["node_type"]["default"] == "worker"
    assert "context" in properties
    assert "authority_policy" in properties
    assert "execution" in properties
    assert "llm_hints" in properties
    assert "external_input_schema" in properties
    assert "external_output_schema" in properties
    assert "control_state_schema" in properties
    assert "local_state" in properties
    assert "compaction_rule" in properties
    assert "failure_policy" in properties
    assert "projections" in properties


def test_llm_hints_defaults_are_stable_and_optional() -> None:
    worker = Worker(id="w", name="Worker")
    hints = LLMHints()

    assert worker.llm_hints is None
    assert hints.prompt_template == ""
    assert hints.system_prompt == ""
    assert hints.temperature == 0.7
    assert hints.max_tool_rounds == 10


def test_worker_with_llm_hints_but_no_explicit_model_defaults_to_text_output() -> None:
    worker = Worker(
        id="draft",
        name="Draft",
        llm_hints=LLMHints(prompt_template="Draft {input}"),
    )

    assert [port.name for port in worker.output_ports] == ["text"]


def test_worker_flat_llm_fields_round_trip_into_llm_hints_and_compat_properties() -> None:
    worker = Worker(
        id="draft",
        name="Draft",
        model="test-model",
        prompt_template="Draft {input}",
        system_prompt="Stay concise.",
        temperature=0.2,
        max_tokens=128,
        output_json_schema={"type": "object"},
        tools=[{"type": "function", "function": {"name": "lookup"}}],
        max_tool_rounds=4,
        task_tier="routine",
    )

    assert worker.llm_hints is not None
    assert worker.llm_hints.prompt_template == "Draft {input}"
    assert worker.prompt_template == "Draft {input}"
    assert worker.system_prompt == "Stay concise."
    assert worker.temperature == 0.2
    assert worker.max_tokens == 128
    assert worker.output_json_schema == {"type": "object"}
    assert worker.tools == [{"type": "function", "function": {"name": "lookup"}}]
    assert worker.max_tool_rounds == 4
    assert worker.task_tier == "routine"
    assert [port.name for port in worker.output_ports] == ["text"]


def test_worker_flat_tool_fields_round_trip_into_tool_compat_properties() -> None:
    worker = Worker(
        id="fetch",
        name="Fetch",
        tool_id="web_search",
        tool_config={"query": "{input}", "limit": 3},
    )

    assert worker.tool_ids == ["web_search"]
    assert worker.tool_id == "web_search"
    assert worker.tool_config == {"query": "{input}", "limit": 3}


def test_worker_with_control_flow_defaults_to_gate_ports() -> None:
    worker = Worker(
        id="route",
        name="Route",
        control_flow=ControlFlowConfig(condition="score > 0.5"),
    )

    assert [port.name for port in worker.output_ports] == ["true", "false"]


def test_worker_composite_contract_fields_are_typed_and_stable() -> None:
    worker = Worker(
        id="review",
        name="Review",
        body_graph="review_body",
        external_input_schema={"type": "object", "required": ["draft"]},
        external_output_schema={"type": "object", "required": ["summary"]},
        control_state_schema={"type": "object", "properties": {"iteration": {"type": "integer"}}},
        local_state=NodeLocalState(
            json_schema={"type": "object", "properties": {"history": {"type": "array"}}},
            description="review state",
        ),
        compaction_rule=CompactionRule(strategy="sliding_window", window_size=2),
        projections=[ContextProjection(name="reviewer_view", local_state_keys=["history"])],
    )

    assert worker.external_input_schema == {"type": "object", "required": ["draft"]}
    assert worker.external_output_schema == {"type": "object", "required": ["summary"]}
    assert worker.control_state_schema["type"] == "object"
    assert worker.local_state.description == "review state"
    assert worker.compaction_rule is not None
    assert worker.compaction_rule.window_size == 2
    assert worker.failure_policy.max_iterations is None
    assert worker.projections[0].name == "reviewer_view"


def _planner_address() -> CellAddress:
    return CellAddress(
        cell_id="planner-cell",
        tissue_id="coordination",
        organ_id="brain",
        organism_id="demo-organism",
    )


def _research_packet(*, trace: SignalTrace | None = None) -> CellHandoffPacket:
    return CellHandoffPacket(
        trace=trace or SignalTrace(trace_id="trace:52-1", root_task_id="root-review"),
        sender=_planner_address(),
        recipient=CellAddress(
            cell_id="research-cell",
            tissue_id="analysis",
            organ_id="brain",
            organism_id="demo-organism",
        ),
        task=HandoffTask(
            task_id="research-task",
            instruction="Inspect the evidence and summarize the signaling relationship.",
            scope="cell-signaling",
            hard_constraints=["Use only supplied evidence."],
            soft_constraints=["Keep the answer to one sentence."],
            input_payload={"question": "Which kinase activates pathway B?"},
        ),
        evidence_refs=[
            EvidenceRef(
                ref_id="paper:1",
                label="Paper abstract",
                summary="Kinase A activates pathway B in epithelial cells.",
                source="paper-1",
                locator="papers/kinase-a.txt",
            )
        ],
        output_contract=OutputContract(
            definition_of_done="Return the core claim as one sentence.",
            expected_return_shape="One sentence.",
        ),
        budget_limits=CellBudgetLimits(
            max_selected_refs_per_source=1,
            max_expanded_refs_per_source=1,
            max_completion_rounds=1,
            max_runtime_seconds=30,
        ),
        authority_limits=CellAuthorityLimits(
            acting_authority=WorkerAuthority.DELEGATE,
            max_spawned_cells=0,
            allow_delegate=False,
            allow_memory_write_scopes=["memory.team"],
        ),
        continuation_hooks=ContinuationHooks(
            reply_to_cell_id="planner-cell",
            status_topic="organism.status",
            completion_topic="organism.completion",
            escalation_topic="organism.escalation",
        ),
    )


class _CellCompletionProvider:
    def __init__(self) -> None:
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        worker_id = str(request.metadata.get("worker_id") or "")
        if worker_id == "research-cell":
            return CompletionResponse(
                text="Kinase A activates pathway B.",
                raw={"worker_id": worker_id},
            )
        if worker_id == "writer-cell":
            return CompletionResponse(
                text="Draft: Kinase A activates pathway B and should be highlighted.",
                raw={"worker_id": worker_id},
            )
        return CompletionResponse(text="Unhandled worker.", raw={"worker_id": worker_id})


def test_cell_handoff_packet_converts_to_execution_request_with_trace_and_limits() -> None:
    packet = _research_packet()

    request = packet.to_execution_request()

    assert request.task == "Inspect the evidence and summarize the signaling relationship."
    assert request.constraints.scope == "cell-signaling"
    assert request.constraints.hard_constraints == ["Use only supplied evidence."]
    assert request.constraints.soft_constraints == ["Keep the answer to one sentence."]
    assert request.evidence[0].ref_id == "paper:1"
    assert request.evidence[0].content == "Kinase A activates pathway B in epithelial cells."
    assert request.acquisition.policy is not None
    assert request.acquisition.policy.max_selected_items_per_source == 1
    assert request.acquisition.policy.max_expanded_items_per_source == 1
    assert request.continuation is not None
    assert request.continuation.stage.value == "act"
    assert request.continuation.selections[0].ref_id == "paper:1"
    assert request.continuation.task_state["handoff_packet_id"] == packet.packet_id
    assert request.memory.allowed_write_scopes == ["memory.team"]
    assert request.communication.address_for(CommunicationChannelKind.COMPLETION) == "organism.completion"
    assert request.metadata["trace_id"] == "trace:52-1"
    assert request.metadata["sender"]["cell_id"] == "planner-cell"
    assert request.metadata["recipient"]["cell_id"] == "research-cell"
    assert request.metadata["budget_limits"]["max_runtime_seconds"] == 30
    assert request.metadata["authority_limits"]["acting_authority"] == "delegate"
    assert request.metadata["continuation_hooks"]["completion_topic"] == "organism.completion"


def test_trace_log_keeps_handoffs_separate_from_supervisory_signals() -> None:
    packet = _research_packet()
    log = CrossCellTraceLog()
    log.record_handoff(packet)
    log.record_signal(
        make_status_signal(
            packet,
            status="running",
            summary="Research cell is processing the packet.",
        )
    )
    log.record_signal(
        make_warning_signal(
            packet,
            code="thin_evidence",
            detail="Only one ref is attached to the handoff.",
            summary="Research cell may need stronger evidence.",
        )
    )
    log.record_signal(
        make_budget_pressure_signal(
            packet,
            summary="Research cell is on its last completion round.",
            pressure_sources=["max_completion_rounds"],
            remaining={"max_completion_rounds": 0},
        )
    )

    trace_rows = log.inspect_trace(packet.trace.trace_id)

    assert [row["kind"] for row in trace_rows] == ["handoff", "signal", "signal", "signal"]
    assert trace_rows[0]["recipient_cell_id"] == "research-cell"
    assert [row["signal_type"] for row in trace_rows[1:]] == [
        "status",
        "warning",
        "budget_pressure",
    ]
    assert all("broadcast_scope" in row for row in trace_rows[1:])
    assert all("recipient_cell_id" not in row for row in trace_rows[1:])


@pytest.mark.asyncio
async def test_three_cell_slice_coordinates_via_typed_handoffs_and_supervisory_signals() -> None:
    provider = _CellCompletionProvider()
    executor = WorkerCoreExecutor(completion_provider=provider)
    trace = SignalTrace(trace_id="trace:organism-demo", root_task_id="compose-brief")
    trace_log = CrossCellTraceLog()

    research_packet = _research_packet(trace=trace)
    research_worker = WorkerDefinition(
        id="research-cell",
        role="researcher",
        instruction="Use only the supplied evidence.",
        model="stub-model",
    )
    research_run = await execute_cell_handoff(
        executor=executor,
        worker=research_worker,
        packet=research_packet,
        trace_log=trace_log,
    )

    assert research_run.result.status == "completed"
    research_signal = research_run.signals[-1]
    assert isinstance(research_signal, CompletionSignal)
    research_output_ref = research_signal.as_evidence_ref(
        ref_id="handoff:research:output",
        label="Research finding",
    )

    writer_packet = CellHandoffPacket(
        trace=SignalTrace(
            trace_id=trace.trace_id,
            root_task_id=trace.root_task_id,
            parent_packet_id=research_packet.packet_id,
            parent_signal_id=research_signal.signal_id,
        ),
        sender=_planner_address(),
        recipient=CellAddress(
            cell_id="writer-cell",
            tissue_id="synthesis",
            organ_id="reporting",
            organism_id="demo-organism",
        ),
        task=HandoffTask(
            task_id="draft-task",
            instruction="Draft one sentence from the research finding.",
            hard_constraints=["Do not invent claims."],
        ),
        evidence_refs=[research_output_ref],
        output_contract=OutputContract(
            definition_of_done="Draft one sentence using the research output.",
            expected_return_shape="One sentence.",
        ),
        budget_limits=CellBudgetLimits(max_completion_rounds=1),
        authority_limits=CellAuthorityLimits(
            acting_authority=WorkerAuthority.LEAF,
            max_spawned_cells=0,
        ),
        continuation_hooks=ContinuationHooks(
            reply_to_cell_id="planner-cell",
            status_topic="organism.status",
            completion_topic="organism.completion",
            escalation_topic="organism.escalation",
        ),
    )
    writer_worker = WorkerDefinition(
        id="writer-cell",
        role="writer",
        instruction="Draft from the supplied research finding only.",
        model="stub-model",
    )
    writer_run = await execute_cell_handoff(
        executor=executor,
        worker=writer_worker,
        packet=writer_packet,
        trace_log=trace_log,
    )
    trace_log.record_signal(
        make_budget_pressure_signal(
            writer_packet,
            summary="Writer cell is out of spare completion rounds.",
            pressure_sources=["max_completion_rounds"],
            remaining={"max_completion_rounds": 0},
        )
    )
    trace_log.record_signal(
        make_escalation_signal(
            writer_packet,
            reason="validator_review_required",
            requested_action="Send the draft to a validator cell before publishing.",
            summary="Writer completed but requests validator review.",
        )
    )

    assert writer_run.result.status == "completed"
    assert provider.requests[0].metadata["handoff_packet_id"] == research_packet.packet_id
    assert provider.requests[1].metadata["handoff_packet_id"] == writer_packet.packet_id
    assert "Kinase A activates pathway B." in provider.requests[1].user_prompt

    trace_rows = trace_log.inspect_trace(trace.trace_id)

    assert len(trace_log.packets_for_trace(trace.trace_id)) == 2
    assert len(trace_log.signals_for_trace(trace.trace_id)) == 6
    assert {row["signal_type"] for row in trace_rows if row["kind"] == "signal"} == {
        "status",
        "completed",
        "budget_pressure",
        "escalated",
    }
    assert all(row["trace_id"] == trace.trace_id for row in trace_rows)
