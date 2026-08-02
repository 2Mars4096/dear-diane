from __future__ import annotations

import asyncio
import json

import pytest

from dan.providers import StreamChunk
from dan.worker.core.contracts import (
    AcquisitionSelection,
    ContinuationPayload,
    DiscoveryCatalog,
    ExpandedContext,
)
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse
from dan.worker.organisms.coding_conversation import (
    CodingConversationContext,
    CodingConversationController,
    CodingConversationFacts,
    CodingConversationReportSummary,
    CodingConversationReviewDecision,
    CodingConversationMessage,
    CodingProjectMilestone,
    CodingProjectPlan,
    CodingProjectPlannerContext,
    CodingProjectPlannerController,
    ProviderCompletionAdapter,
    _parse_payload,
    _fallback_turn_decision,
    _fallback_project_planner_decision,
    _normalize_turn_decision,
    _normalize_review_decision,
    _conversation_review_contract,
    _conversation_turn_contract,
)
from dan.worker.organisms.coding_conversation import CodingConversationTurnDecision


def _context(*, recent_conversation=None, recent_reports=None) -> CodingConversationContext:
    return CodingConversationContext(
        workspace_root="/workspace",
        model="kimi-k2.5",
        thinking_mode="auto",
        tool_ids=["list_directory"],
        acceptance_criteria=[],
        pending_clarification=None,
        facts=CodingConversationFacts(
            product_name="DAN Code",
            workspace_root="/workspace",
            effective_working_directory="/workspace",
            shell_process_directory="/repo",
            session_id="coding-session-test",
            active_model="kimi-k2.5",
            thinking_mode="auto",
            approval_mode="confirm-risky",
            enabled_tools=["list_directory"],
            coding_turn_count=1,
            conversation_message_count=2,
            pending_clarification=None,
            latest_report_status="failed",
            latest_report_objective="build the website",
            latest_report_target_files=[],
            latest_report_error="no validated candidate",
            current_timestamp="2026-04-10T00:00:00+08:00",
            current_date="2026-04-10",
            timezone="Asia/Hong_Kong",
        ),
        recent_conversation=list(recent_conversation or []),
        recent_reports=list(recent_reports or []),
    )


def _planner_context(
    *,
    existing_plan: CodingProjectPlan | None = None,
    recent_reports=None,
) -> CodingProjectPlannerContext:
    base = _context(recent_reports=recent_reports)
    return CodingProjectPlannerContext(
        workspace_root=base.workspace_root,
        model=base.model,
        thinking_mode=base.thinking_mode,
        tool_ids=list(base.tool_ids),
        acceptance_criteria=list(base.acceptance_criteria),
        pending_clarification=base.pending_clarification,
        facts=base.facts,
        recent_conversation=list(base.recent_conversation),
        recent_reports=list(base.recent_reports),
        existing_plan=existing_plan,
    )


def test_fallback_turn_stays_conversational_when_model_payload_is_missing() -> None:
    decision = _fallback_turn_decision(
        user_message="helloo,",
        pending_clarification=None,
        context=_context(),
    )

    assert decision.action == "respond"
    assert "launch bounded coding work" in decision.public_response


def test_fallback_turn_reuses_pending_clarification() -> None:
    decision = _fallback_turn_decision(
        user_message="please help me build the website we talked about",
        pending_clarification="Which website should I use as the reference?",
        context=_context(),
    )

    assert decision.action == "clarify"


def test_fallback_turn_reuses_latest_failed_objective_for_continue_request() -> None:
    decision = _fallback_turn_decision(
        user_message="can you continue to fix the shark web",
        pending_clarification=None,
        context=_context(
            recent_reports=[
                CodingConversationReportSummary(
                    status="failed",
                    task_id="coding-organ-task:10",
                    objective="Build the absharks static website under /workspace.",
                    candidate_id=None,
                    change_summary="",
                    target_files=["/workspace/absharks/index.html"],
                    test_plan=[],
                    risks=[],
                    error="The coding organism did not produce a validated candidate.",
                )
            ]
        ),
    )

    assert decision.action == "code"
    assert decision.coding_objective == "Build the absharks static website under /workspace."
    assert "latest coding objective" in decision.public_response


def test_fallback_turn_stays_conversational_for_failed_no_output_continue_request() -> None:
    decision = _fallback_turn_decision(
        user_message="please continue",
        pending_clarification=None,
        context=_context(
            recent_reports=[
                CodingConversationReportSummary(
                    status="failed",
                    task_id="coding-organ-task:11",
                    objective="Build the absharks static website under /workspace.",
                    candidate_id=None,
                    change_summary="",
                    target_files=[],
                    test_plan=[],
                    risks=[],
                    error="The coding organism did not produce a validated candidate.",
                )
            ]
        ),
    )

    assert decision.action == "respond"
    assert "launch bounded coding work" in decision.public_response


def test_project_planner_fallback_reuses_next_pending_milestone() -> None:
    decision = _fallback_project_planner_decision(
        user_message="continue",
        requested_objective="Continue the local notes API project.",
        requested_acceptance_criteria=["Keep persistence intact."],
        existing_plan=CodingProjectPlan(
            project_goal="Build the local notes API project.",
            plan_summary="Three milestone plan.",
            active_milestone_id="m1",
            milestones=[
                CodingProjectMilestone(
                    milestone_id="m1",
                    title="Scaffold",
                    objective="Create the service scaffold.",
                    status="completed",
                ),
                CodingProjectMilestone(
                    milestone_id="m2",
                    title="Endpoints",
                    objective="Implement the create/list/get note endpoints.",
                    acceptance_criteria=["Persist notes locally."],
                    status="pending",
                ),
            ],
        ),
    )

    assert decision.active_milestone_id == "m2"
    assert decision.active_objective == "Implement the create/list/get note endpoints."
    assert "Persist notes locally." in decision.active_acceptance_criteria


@pytest.mark.asyncio
async def test_controller_turn_routes_explicit_coding_request_through_durable_runner(
    monkeypatch,
) -> None:
    captured_requests: list[CompletionRequest] = []

    async def _fake_complete(self, request):
        captured_requests.append(request)
        return CompletionResponse(
            text=json.dumps(
                {
                    "action": "code",
                    "public_response": "I’m starting one bounded coding run for this request.",
                    "coding_objective": "fix the navbar glow in shark-anatomy.html",
                }
            ),
            raw={},
        )

    monkeypatch.setattr(
        "dan.worker.organisms.coding_conversation.ProviderCompletionAdapter.complete",
        _fake_complete,
    )

    controller = CodingConversationController(provider=object(), model="gpt-test")

    decision, session = await controller.decide_user_turn(
        session=None,
        user_message="fix the navbar glow in shark-anatomy.html",
        pending_clarification=None,
        context=_context(),
    )

    assert session is not None
    assert decision.action == "code"
    assert decision.coding_objective == "fix the navbar glow in shark-anatomy.html"
    assert decision.public_response == "I’m starting one bounded coding run for this request."
    assert session.accepted_message_count == 1
    assert len(session.mailbox) == 1
    assert session.mailbox[0].status == "completed"
    assert captured_requests[0].metadata["worker_id"] == "dan-code.orchestrator"
    assert captured_requests[0].metadata["agent_message_index"] == 1


@pytest.mark.asyncio
async def test_project_planner_routes_through_durable_runner(monkeypatch) -> None:
    captured_requests: list[CompletionRequest] = []

    async def _fake_complete(self, request):
        captured_requests.append(request)
        return CompletionResponse(
            text=json.dumps(
                {
                    "public_response": "I split this into two milestones and I’m starting with the scaffold.",
                    "project_goal": "Build a local notes API service.",
                    "plan_summary": "Scaffold first, then add endpoints and tests.",
                    "milestones": [
                        {
                            "milestone_id": "m1",
                            "title": "Scaffold",
                            "objective": "Create the service scaffold and app entrypoint.",
                            "acceptance_criteria": ["Create the project skeleton."],
                            "status": "active",
                        },
                        {
                            "milestone_id": "m2",
                            "title": "Endpoints",
                            "objective": "Add create/list/get endpoints with tests.",
                            "status": "pending",
                        },
                    ],
                    "active_milestone_id": "m1",
                    "active_objective": "Create the service scaffold and app entrypoint.",
                    "active_acceptance_criteria": ["Create the project skeleton."],
                }
            ),
            raw={},
        )

    monkeypatch.setattr(
        "dan.worker.organisms.coding_conversation.ProviderCompletionAdapter.complete",
        _fake_complete,
    )

    planner = CodingProjectPlannerController(provider=object(), model="gpt-test")

    decision, session = await planner.plan_project(
        session=None,
        user_message="build a local notes API service",
        requested_objective="Build a local notes API service.",
        requested_acceptance_criteria=["Keep the docs concise."],
        context=_planner_context(),
    )

    assert session is not None
    assert decision.project_goal == "Build a local notes API service."
    assert decision.active_milestone_id == "m1"
    assert decision.active_objective == "Create the service scaffold and app entrypoint."
    assert "Create the project skeleton." in decision.active_acceptance_criteria
    assert "Keep the docs concise." in decision.active_acceptance_criteria
    assert captured_requests[0].metadata["worker_id"] == "dan-code.project-planner"
    assert captured_requests[0].metadata["agent_message_index"] == 1


@pytest.mark.asyncio
async def test_project_planner_bypasses_model_for_benchmark_mode(monkeypatch) -> None:
    async def _unexpected_complete(self, request):
        raise AssertionError("benchmark-mode planning should bypass the model")

    monkeypatch.setattr(
        "dan.worker.organisms.coding_conversation.ProviderCompletionAdapter.complete",
        _unexpected_complete,
    )

    planner = CodingProjectPlannerController(provider=object(), model="gpt-test")
    benchmark_context = _planner_context().model_copy(
        update={
            "facts": _planner_context().facts.model_copy(
                update={"benchmark_mode": True}
            )
        }
    )

    decision, session = await planner.plan_project(
        session=None,
        user_message="fix the swe-bench instance",
        requested_objective="Resolve the benchmark issue with the smallest correct patch.",
        requested_acceptance_criteria=["Keep the patch benchmark-scoped."],
        context=benchmark_context,
    )

    assert session is not None
    assert decision.active_objective == "Resolve the benchmark issue with the smallest correct patch."
    assert decision.active_acceptance_criteria == ["Keep the patch benchmark-scoped."]
    assert len(decision.milestones) == 1
    assert decision.public_response == "I’ll treat this as one bounded milestone for now."


def test_parse_payload_unwraps_embedded_public_response_json() -> None:
    payload = _parse_payload(
        {
            "public_response": (
                "```json\n"
                + json.dumps(
                    {
                        "action": "code",
                        "public_response": "Proceeding with implementation.",
                        "coding_objective": "Build only index.html first.",
                    },
                    sort_keys=True,
                )
                + "\n```"
            )
        }
    )

    assert payload["action"] == "code"
    assert payload["public_response"] == "Proceeding with implementation."
    assert payload["coding_objective"] == "Build only index.html first."


def test_turn_normalization_keeps_llm_code_decision_for_contextual_task() -> None:
    decision = _normalize_turn_decision(
        {
            "action": "code",
            "public_response": "I can continue that website build now.",
            "coding_objective": "please help me build the website we talked about",
        },
        user_message="please help me build the website we talked about",
        pending_clarification=None,
        context=_context(
            recent_conversation=[
                CodingConversationMessage(role="user", text="build a shark website in a new folder"),
                CodingConversationMessage(role="assistant", text="I can do that."),
            ]
        ),
    )

    assert decision.action == "code"


def test_turn_normalization_falls_back_when_code_has_no_objective() -> None:
    decision = _normalize_turn_decision(
        {
            "action": "code",
            "public_response": "",
            "coding_objective": "",
        },
        user_message="",
        pending_clarification=None,
        context=_context(),
    )

    assert isinstance(decision, CodingConversationTurnDecision)
    assert decision.action == "respond"


def test_turn_normalization_infers_respond_from_public_response_only_payload() -> None:
    decision = _normalize_turn_decision(
        {
            "public_response": "latest run status: failed",
        },
        user_message="current status now",
        pending_clarification=None,
        context=_context(),
    )

    assert isinstance(decision, CodingConversationTurnDecision)
    assert decision.action == "respond"
    assert decision.public_response == "latest run status: failed"


def test_turn_normalization_converts_question_like_response_into_clarify() -> None:
    decision = _normalize_turn_decision(
        {
            "action": "respond",
            "public_response": (
                "Should I check the current workspace state first, or do you want me to "
                "attempt another direct fix?"
            ),
        },
        user_message="current progress? can you retry to implement the fix",
        pending_clarification=None,
        context=_context(),
    )

    assert decision.action == "clarify"
    assert (
        decision.clarifying_question
        == "Should I check the current workspace state first, or do you want me to attempt another direct fix?"
    )


def test_review_normalization_blocks_done_when_build_has_no_output() -> None:
    report = CodingConversationReportSummary(
        status="failed",
        task_id="coding-organ-task:1",
        objective="build the website",
        candidate_id=None,
        change_summary="",
        target_files=[],
        test_plan=[],
        risks=[],
        error="did not produce a validated candidate",
    )

    decision = _normalize_review_decision(
        {
            "action": "done",
            "public_response": "This bounded coding pass is done.",
        },
        objective="build the website",
        report_summary=report,
    )

    assert isinstance(decision, CodingConversationReviewDecision)
    assert decision.action == "continue"
    assert "did not produce a concrete validated result yet" in decision.public_response


def test_review_normalization_infers_continue_from_public_response_only_payload() -> None:
    report = CodingConversationReportSummary(
        status="failed",
        task_id="coding-organ-task:1",
        objective="current status now",
        candidate_id=None,
        change_summary="",
        target_files=[],
        test_plan=[],
        risks=[],
        error="did not produce a validated candidate",
    )

    decision = _normalize_review_decision(
        {
            "public_response": "This turn should not have started coding.",
        },
        objective="current status now",
        report_summary=report,
    )

    assert isinstance(decision, CodingConversationReviewDecision)
    assert decision.action == "continue"


def test_review_normalization_mentions_material_output_when_failed_run_wrote_files() -> None:
    report = CodingConversationReportSummary(
        status="failed",
        task_id="coding-organ-task:2",
        objective="build the website",
        candidate_id=None,
        change_summary="Created the first website slice.",
        target_files=["/workspace/absharks/index.html"],
        test_plan=[],
        risks=[],
        error="aggregation failed",
    )

    decision = _normalize_review_decision(
        {
            "action": "done",
            "public_response": "This bounded coding pass is done.",
        },
        objective="build the website",
        report_summary=report,
    )

    assert decision.action == "continue"
    assert "produced material output" in decision.public_response


def test_review_normalization_stops_completed_benchmark_run_even_if_model_says_continue() -> None:
    report = CodingConversationReportSummary(
        status="completed",
        task_id="coding-organ-task:3",
        objective="Fix the marshmallow DateTime container regression.",
        candidate_id="partial-candidate-from-tool-evidence",
        change_summary="Applied the benchmark patch to DateTime._bind_to_schema.",
        target_files=["/workspace/src/marshmallow/fields.py"],
        test_plan=["python test_datetime_container_fix.py"],
        risks=[],
        error=None,
    )

    decision = _normalize_review_decision(
        {
            "action": "continue",
            "public_response": "I should do one more bounded repair pass before stopping.",
            "next_objective": "Add one more benchmark validation step.",
        },
        objective="Fix the marshmallow DateTime container regression.",
        report_summary=report,
        benchmark_mode=True,
    )

    assert decision.action == "done"
    assert decision.next_objective == ""
    assert decision.public_response == (
        "This bounded coding pass is done and the benchmark artifacts can be exported now."
    )


class _StreamingConversationProvider:
    def __init__(self) -> None:
        self.complete_calls = 0
        self.stream_calls: list[dict[str, object]] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.complete_calls += 1
        raise AssertionError("stream-enabled conversation path should not call complete()")

    async def stream(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.stream_calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        yield StreamChunk(delta="Streaming", accumulated="Streaming")
        yield StreamChunk(delta=" response", accumulated="Streaming response")
        yield StreamChunk(
            delta="",
            accumulated="Streaming response",
            done=True,
            usage={"total_tokens": 4},
        )


@pytest.mark.asyncio
async def test_provider_completion_adapter_streams_text_when_enabled() -> None:
    events: list[dict[str, object]] = []
    provider = _StreamingConversationProvider()
    adapter = ProviderCompletionAdapter(
        provider=provider,
        default_model="gpt-test",
        stream_text_responses=True,
        event_callback=events.append,
    )

    response = await adapter.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Answer directly.",
            user_prompt="What is the latest status?",
            metadata={"worker_id": "coding-conversation.orchestrator"},
        )
    )

    assert response.text == "Streaming response"
    assert provider.complete_calls == 0
    assert len(provider.stream_calls) == 1
    assert response.raw["provider_result"]["provider_metadata"] == {
        "streamed_response": True
    }
    assert [event["event"] for event in events] == [
        "model.requested",
        "model.stream.started",
        "model.stream.delta",
        "model.stream.delta",
        "model.stream.completed",
        "model.responded",
        "completion.completed",
    ]
    assert events[0]["request_mode"] == "stream"
    assert events[0]["message_count"] == 2
    assert events[0]["system_message_count"] == 1
    assert events[0]["user_message_count"] == 1
    assert events[0]["total_input_chars"] >= len("Answer directly.") + len("What is the latest status?")
    assert events[5]["usage_total_tokens"] == 4
    assert events[5]["text_chars"] == len("Streaming response")
    assert events[5]["streamed"] is True


class _HedgedConversationProvider:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = list(responses)
        self.complete_calls: list[int] = []
        self.cancelled_calls: list[int] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        call_index = len(self.complete_calls) + 1
        self.complete_calls.append(call_index)
        config = self._responses[call_index - 1]
        delay = float(config.get("delay", 0.0))
        try:
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self.cancelled_calls.append(call_index)
            raise

        class _Result:
            def __init__(self, payload):
                self.text = str(payload.get("text", ""))
                self.model = str(payload.get("model", "gpt-test"))
                self.finish_reason = "stop"
                self.usage = {"total_tokens": 1}
                self.provider_metadata = {}
                self.raw_assistant_message = {
                    "role": "assistant",
                    "content": self.text,
                }

        return _Result(config)


@pytest.mark.asyncio
async def test_controller_review_routes_completed_report_through_durable_runner(
    monkeypatch,
) -> None:
    captured_requests: list[CompletionRequest] = []

    async def _fake_complete(self, request):
        captured_requests.append(request)
        return CompletionResponse(
            text=json.dumps(
                {
                    "action": "done",
                    "public_response": "This bounded coding pass is done.",
                }
            ),
            raw={},
        )

    monkeypatch.setattr(
        "dan.worker.organisms.coding_conversation.ProviderCompletionAdapter.complete",
        _fake_complete,
    )

    controller = CodingConversationController(provider=object(), model="gpt-test")
    session = controller.create_session(metadata={"surface": "test"})
    report = CodingConversationReportSummary(
        status="completed",
        task_id="coding-organ-task:1",
        objective="fix the navbar glow",
        candidate_id="candidate-1",
        change_summary="Updated the hover effect in styles.css.",
        target_files=["/workspace/absharks/styles.css"],
        test_plan=["open the page and hover the navbar"],
        risks=[],
        error=None,
    )

    review, returned_session = await controller.review_coding_result(
        session=session,
        objective="fix the navbar glow",
        report_summary=report,
        context=_context(),
    )

    assert returned_session is session
    assert review.action == "done"
    assert review.public_response == "This bounded coding pass is done."
    assert session.accepted_message_count == 1
    assert len(session.mailbox) == 1
    assert session.mailbox[0].status == "completed"
    assert captured_requests[0].metadata["worker_id"] == "dan-code.orchestrator"
    assert captured_requests[0].metadata["agent_message_index"] == 1


@pytest.mark.asyncio
async def test_provider_completion_adapter_accepts_faster_hedged_structured_response() -> None:
    events: list[dict[str, object]] = []
    provider = _HedgedConversationProvider(
        [
            {
                "delay": 0.05,
                "text": json.dumps(
                    {
                        "action": "respond",
                        "public_response": "primary slow response",
                    }
                ),
            },
            {
                "delay": 0.01,
                "text": json.dumps(
                    {
                        "action": "respond",
                        "public_response": "secondary fast response",
                    }
                ),
            },
        ]
    )
    adapter = ProviderCompletionAdapter(
        provider=provider,
        default_model="gpt-test",
        hedge_max_attempts=2,
        hedge_delay_seconds=0.005,
        event_callback=events.append,
    )

    response = await adapter.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Answer directly.",
            user_prompt="What is the latest status?",
            output_contract=_conversation_turn_contract(),
        )
    )

    assert response.text == json.dumps(
        {
            "action": "respond",
            "public_response": "secondary fast response",
        }
    )
    assert provider.complete_calls == [1, 2]
    assert provider.cancelled_calls == [1]
    assert [event["event"] for event in events if event["event"].startswith("model.hedge")] == [
        "model.hedge.launched",
        "model.hedge.accepted",
        "model.hedge.cancelled",
    ]
    request_events = [event for event in events if event["event"] == "model.requested"]
    assert request_events[0]["request_mode"] == "complete"
    assert request_events[0]["hedged"] is False
    assert request_events[1]["hedged"] is True
    accepted = next(event for event in events if event["event"] == "model.hedge.accepted")
    assert accepted["accepted_attempt"] == 2
    assert accepted["used_fallback"] is False


@pytest.mark.asyncio
async def test_provider_completion_adapter_uses_hedge_when_primary_payload_is_invalid() -> None:
    provider = _HedgedConversationProvider(
        [
            {"delay": 0.0, "text": "not valid json"},
            {
                "delay": 0.0,
                "text": json.dumps(
                    {
                        "action": "done",
                        "public_response": "This bounded coding pass is done.",
                    }
                ),
            },
        ]
    )
    adapter = ProviderCompletionAdapter(
        provider=provider,
        default_model="gpt-test",
        hedge_max_attempts=2,
        hedge_delay_seconds=0.05,
    )

    response = await adapter.complete(
        CompletionRequest(
            model="gpt-test",
            system_prompt="Answer directly.",
            user_prompt="Review the run.",
            output_contract=_conversation_review_contract(),
        )
    )

    assert json.loads(response.text)["action"] == "done"
    assert provider.complete_calls == [1, 2]


@pytest.mark.asyncio
async def test_controller_turn_does_not_reuse_stale_worker_core_continuation(
    monkeypatch,
) -> None:
    captured_continuations: list[ContinuationPayload | None] = []

    async def _fake_complete(self, request):
        captured_continuations.append(request.continuation)
        return CompletionResponse(
            text='{"action":"respond","public_response":"latest run status: failed"}',
            raw={},
        )

    monkeypatch.setattr(
        "dan.worker.organisms.coding_conversation.ProviderCompletionAdapter.complete",
        _fake_complete,
    )

    controller = CodingConversationController(provider=object(), model="gpt-test")
    decision, session = await controller.decide_user_turn(
        session=None,
        user_message="current status?",
        pending_clarification=None,
        context=_context(),
    )

    assert decision.action == "respond"
    assert captured_continuations

    session.worker_session.continuation = ContinuationPayload(
        catalogs=[DiscoveryCatalog(source_id="stale", family="memory_catalog")],
        selections=[
            AcquisitionSelection(
                ref_id="memory:stale",
                source_id="stale",
                family="memory_catalog",
            )
        ],
        expanded_context=[
            ExpandedContext(
                ref_id="memory:stale",
                source_id="stale",
                family="memory_catalog",
                title="Stale context",
                content="Build the website next.",
            )
        ],
        task_state={"stale": True},
    )

    await controller.decide_user_turn(
        session=session,
        user_message="current status?",
        pending_clarification=None,
        context=_context(),
    )

    assert len(captured_continuations) == 2
    second_continuation = captured_continuations[1]
    assert second_continuation is not None
    assert second_continuation.catalogs == []
    assert second_continuation.selections == []
    assert second_continuation.expanded_context == []
    assert second_continuation.task_state == {}


@pytest.mark.asyncio
async def test_controller_turn_parses_fenced_json_payload(monkeypatch) -> None:
    async def _fake_complete(self, request):
        return CompletionResponse(
            text='```json\n{"action":"respond","public_response":"workspace root: /workspace"}\n```',
            raw={},
        )

    monkeypatch.setattr(
        "dan.worker.organisms.coding_conversation.ProviderCompletionAdapter.complete",
        _fake_complete,
    )

    controller = CodingConversationController(provider=object(), model="gpt-test")
    decision, _session = await controller.decide_user_turn(
        session=None,
        user_message="current status?",
        pending_clarification=None,
        context=_context(),
    )

    assert decision.action == "respond"
    assert decision.public_response == "workspace root: /workspace"


@pytest.mark.asyncio
async def test_controller_turn_normalizes_question_like_respond_into_clarify(monkeypatch) -> None:
    async def _fake_complete(self, request):
        return CompletionResponse(
            text=json.dumps(
                {
                    "action": "respond",
                    "public_response": "Should I inspect the current files first, or retry the direct fix now?",
                }
            ),
            raw={},
        )

    monkeypatch.setattr(
        "dan.worker.organisms.coding_conversation.ProviderCompletionAdapter.complete",
        _fake_complete,
    )

    controller = CodingConversationController(provider=object(), model="gpt-test")
    decision, _session = await controller.decide_user_turn(
        session=None,
        user_message="current progress? can you retry to implement the fix",
        pending_clarification=None,
        context=_context(),
    )

    assert decision.action == "clarify"
    assert decision.clarifying_question == "Should I inspect the current files first, or retry the direct fix now?"


@pytest.mark.asyncio
async def test_controller_review_parses_fenced_json_payload(monkeypatch) -> None:
    async def _fake_complete(self, request):
        payload = {
            "action": "continue",
            "public_response": "I will continue with a narrower objective.",
            "next_objective": "Create only index.html first.",
            "repair_brief": "Do the smallest valid slice first.",
        }
        return CompletionResponse(
            text=f"```json\n{json.dumps(payload, sort_keys=True)}\n```",
            raw={},
        )

    monkeypatch.setattr(
        "dan.worker.organisms.coding_conversation.ProviderCompletionAdapter.complete",
        _fake_complete,
    )

    controller = CodingConversationController(provider=object(), model="gpt-test")
    report = CodingConversationReportSummary(
        status="failed",
        task_id="coding-organ-task:1",
        objective="build the website",
        candidate_id=None,
        change_summary="",
        target_files=[],
        test_plan=[],
        risks=[],
        error="aggregation failed",
    )
    review, _session = await controller.review_coding_result(
        session=controller.create_session(metadata={"surface": "test"}),
        objective="build the website",
        report_summary=report,
        context=_context(),
    )

    assert review.action == "continue"
    assert review.public_response == "I will continue with a narrower objective."
    assert review.next_objective == "Create only index.html first."
    assert review.repair_brief == "Do the smallest valid slice first."
