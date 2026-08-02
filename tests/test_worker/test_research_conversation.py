from __future__ import annotations

import json

import pytest

from dan.worker.organisms.research_conversation import (
    ResearchConversationContext,
    ResearchConversationController,
    ResearchConversationEvidenceTarget,
    ResearchConversationFacts,
    ResearchConversationIntentionPlan,
    ResearchConversationMessage,
    ResearchConversationReportSummary,
    ResearchConversationReviewDecision,
    ResearchConversationSubproblem,
    ResearchConversationTurnDecision,
    ResearchConversationWorkstream,
    _fallback_intention_plan,
    _normalize_intention_plan,
    _fallback_turn_decision,
    _normalize_review_decision,
    _normalize_turn_decision,
)
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse


def _quality_gates(status: str = "pass") -> list[dict[str, str]]:
    return [
        {
            "gate": gate,
            "status": status if gate != "numeric_reconciliation" else "not_applicable",
            "summary": f"{gate} checked.",
            "evidence_ref": "test:evidence",
            "required_follow_up": "",
        }
        for gate in (
            "time_anchor",
            "scope_boundary",
            "source_authority",
            "numeric_reconciliation",
            "claim_object_fit",
            "final_status",
        )
    ]


def _context(
    *,
    recent_conversation=None,
    recent_reports=None,
    claim_ledger=None,
) -> ResearchConversationContext:
    return ResearchConversationContext(
        workspace_root="/workspace",
        model="kimi-k2.5",
        thinking_mode="auto",
        tool_ids=["file_read", "web_search"],
        acceptance_criteria=[],
        default_delivery_target="research memo",
        depth_profile="standard",
        requested_reader_count=6,
        pending_clarification=None,
        facts=ResearchConversationFacts(
            product_name="DAN Research",
            workspace_root="/workspace",
            effective_working_directory="/workspace",
            shell_process_directory="/repo",
            session_id="research-session-test",
            active_model="kimi-k2.5",
            thinking_mode="auto",
            enabled_tools=["file_read", "web_search"],
            research_turn_count=1,
            conversation_message_count=2,
            pending_clarification=None,
            latest_report_status="failed",
            latest_report_objective="investigate pricing drift",
            latest_report_confidence=0.42,
            latest_report_error="report incomplete",
            default_delivery_target="research memo",
            depth_profile="standard",
            requested_reader_count=6,
            current_timestamp="2026-04-14T00:00:00+08:00",
            current_date="2026-04-14",
            timezone="Asia/Hong_Kong",
        ),
        recent_conversation=list(recent_conversation or []),
        recent_reports=list(recent_reports or []),
        claim_ledger=list(claim_ledger or []),
    )


def test_fallback_turn_stays_conversational_when_model_payload_is_missing() -> None:
    decision = _fallback_turn_decision(
        user_message="hello",
        pending_clarification=None,
        context=_context(),
    )

    assert decision.action == "respond"
    assert "bounded deep research" in decision.public_response


def test_fallback_turn_reuses_latest_objective_for_continue_request() -> None:
    decision = _fallback_turn_decision(
        user_message="go deeper on that",
        pending_clarification=None,
        context=_context(
            recent_reports=[
                ResearchConversationReportSummary(
                    status="completed",
                    task_id="deep-research-task:1",
                    objective="Investigate pricing drift between docs and the repo.",
                    delivery_target="research memo",
                    findings=["Docs and repo disagree on the current price."],
                    evidence_summary=["repo says 9.99", "docs say 12.99"],
                    evidence_refs=["repo:pricing", "docs:pricing"],
                    contradictions=["Price differs across sources."],
                    open_questions=["Which source is authoritative?"],
                    verification_facts=[],
                    audit_issues=[],
                    quality_gates=_quality_gates(),
                    report_readiness="grounded",
                    readiness_note="Enough evidence for a bounded grounded comparison.",
                    confidence=0.63,
                    recommended_change="Verify the release source of truth.",
                    error=None,
                )
            ]
        ),
    )

    assert decision.action == "research"
    assert decision.research_objective == "Investigate pricing drift between docs and the repo."


def test_normalize_turn_decision_preserves_report_reply_markdown() -> None:
    decision = _normalize_turn_decision(
        {
            "action": "respond",
            "response_kind": "report_reply",
            "public_response": "## Final Report\n\nLine one.\n\nLine two.",
        },
        user_message="please continue to the final report",
        pending_clarification=None,
        context=_context(),
    )

    assert decision.action == "respond"
    assert decision.response_kind == "report_reply"
    assert decision.public_response.startswith("## Final Report")
    assert decision.artifact_markdown == "## Final Report\n\nLine one.\n\nLine two."


def test_normalize_turn_decision_infers_delivery_target_from_query() -> None:
    decision = _normalize_turn_decision(
        {
            "action": "research",
            "public_response": "Starting bounded research.",
            "research_objective": "Are current coal ETFs in China worth buying now?",
        },
        user_message="Are current coal ETFs in China worth buying now?",
        pending_clarification=None,
        context=_context(),
    )

    assert decision.action == "research"
    assert "investment thesis memo" in decision.delivery_target


def test_fallback_intention_plan_uses_previous_report_gaps_for_continuation() -> None:
    previous_report = ResearchConversationReportSummary(
        status="completed",
        task_id="deep-research-task:1",
        objective="Investigate pricing drift between docs and the repo.",
        delivery_target="research memo",
        findings=["Docs and repo disagree on the current price."],
        evidence_summary=["repo says 9.99", "docs say 12.99"],
        evidence_refs=["repo:pricing", "docs:pricing"],
        contradictions=["One source says the price changed last week."],
        open_questions=["Which source is authoritative?"],
        verification_facts=[
            {
                "fact": "Current published price",
                "status": "conflicted",
                "source": "docs:pricing",
                "as_of": "2026-04-15",
                "note": "Repo and docs disagree.",
            }
        ],
        audit_issues=[
            {
                "kind": "authority",
                "severity": "major",
                "issue": "The current price is still backed by mixed sources.",
                "affected_claim": "Current published price",
                "required_follow_up": "Check the canonical release or billing source.",
            }
        ],
        quality_gates=_quality_gates(),
        report_readiness="provisional",
        readiness_note="One authoritative recheck is still needed.",
        confidence=0.68,
        recommended_change="Verify the canonical source.",
        error=None,
    )

    plan = _fallback_intention_plan(
        objective="Continue the pricing investigation",
        delivery_target="research memo",
        acceptance_criteria=[],
        context=_context(recent_reports=[previous_report]),
        planning_mode="continuation",
        previous_report=previous_report,
    )

    assert isinstance(plan, ResearchConversationIntentionPlan)
    assert plan.subproblems
    assert plan.workstreams
    assert plan.evidence_targets
    questions = [item.question for item in plan.subproblems]
    assert any("Current published price" in question for question in questions)
    assert any(item.subproblem_ids for item in plan.workstreams)
    target = plan.evidence_targets[0]
    assert target.claim == "Current published price"
    assert target.acceptable_proxy
    assert target.not_found_guidance
    assert target.not_available_guidance
    assert "unresolved gaps" in plan.plan_summary


def test_fallback_intention_plan_keeps_numbered_continuation_targets_atomic() -> None:
    objective = (
        "Execute final targeted retrieval pass to close remaining critical gaps. "
        "Priority 1 (blocking): Retrieve VanEck Vectors Coal ETF (KOL) current NAV "
        "and market price as of April 17, 2026 from NYSE Arca official feed or "
        "VanEck issuer daily holdings disclosure. Priority 2 (blocking): Obtain "
        "Qinhuangdao thermal coal spot price (5500 kcal/kg, FOB) for mid-April "
        "2026 from China Coal Transport and Distribution Association (CCTDA), "
        "China Coal Network, or Bloomberg/Refinitiv commodity feeds. Priority 3 "
        "(best effort): Verify Zhengzhou Commodity Exchange (ZC) thermal coal "
        "futures contract trading status and latest settlement price as of April "
        "17, 2026. Priority 4 (deferred): If correlation analysis remains "
        "infeasible, explicitly mark as unverifiable with available sources "
        "rather than failing the pass. Return verification appendix marking ETF "
        "pricing, thermal coal spot price, and ZCE status as verified/unverified/conflicted."
    )

    plan = _fallback_intention_plan(
        objective=objective,
        delivery_target="research memo",
        acceptance_criteria=[],
        context=_context(),
        planning_mode="continuation",
        previous_report=None,
    )

    questions = [item.question for item in plan.subproblems]

    assert questions == [
        "Retrieve VanEck Vectors Coal ETF (KOL) current NAV and market price as of April 17, 2026 from NYSE Arca official feed or VanEck issuer daily holdings disclosure",
        "Obtain Qinhuangdao thermal coal spot price (5500 kcal/kg, FOB) for mid-April 2026 from China Coal Transport and Distribution Association (CCTDA), China Coal Network, or Bloomberg/Refinitiv commodity feeds",
        "Verify Zhengzhou Commodity Exchange (ZC) thermal coal futures contract trading status and latest settlement price as of April 17, 2026",
    ]
    assert all("Priority" not in question for question in questions)
    assert all("verification appendix" not in question.lower() for question in questions)
    assert all("unverifiable" not in target.claim.lower() for target in plan.evidence_targets)


def test_intention_plan_normalization_cleans_and_dedupes_subproblems() -> None:
    plan = _normalize_intention_plan(
        {
            "public_response": "I will break this down first.",
            "answer_goal": "Compare the repo docs with the product site",
            "refined_objective": "Compare the repo docs with the product site and resolve the discrepancy",
            "plan_summary": "Break the request into small research tasks.",
            "acceptance_criteria": ["Use direct evidence.", "Use direct evidence."],
            "subproblems": [
                {
                    "problem_id": "scope",
                    "question": "  What does the repo currently say?  ",
                    "why_it_matters": "Need the current internal claim.",
                    "evidence_to_seek": "Read the repo docs.",
                    "search_hint": "Start in docs/.",
                    "depends_on": ["inventory", "inventory"],
                },
                {
                    "problem_id": "scope-dup",
                    "question": "What does the repo currently say?",
                    "why_it_matters": "duplicate",
                    "evidence_to_seek": "duplicate",
                },
            ],
            "workstreams": [
                {
                    "stream_id": " current-state ",
                    "title": "  Current repo state  ",
                    "goal": "  Resolve the local repo claim  ",
                    "why_it_matters": "Need one coherent local evidence stream.",
                    "subproblem_ids": ["scope", "scope", "missing"],
                    "aggregation_hint": "Fold this back into the local-vs-external comparison.",
                }
            ],
            "evidence_targets": [
                {
                    "target_id": " local-price ",
                    "claim": "  Current repo price  ",
                    "why_it_matters": "Need one exact local fact target.",
                    "related_subproblem_ids": ["scope", "missing"],
                    "preferred_sites": [" docs.example.com ", "docs.example.com"],
                    "aliases": ["repo price", "repo price"],
                    "acceptable_proxy": "Use the nearest release tag if the docs lag by one publish cycle.",
                    "stop_condition": "Stop when the repo docs or config file gives one explicit price.",
                    "not_found_guidance": "Only call this not found after repo docs plus config search both fail.",
                    "not_available_guidance": "Call this not available if the repo does not publish a price at all.",
                }
            ],
        },
        objective="Compare the repo docs with the product site",
        delivery_target="research memo",
        acceptance_criteria=["Return evidence refs."],
        context=_context(),
        planning_mode="initial",
        previous_report=None,
    )

    assert isinstance(plan, ResearchConversationIntentionPlan)
    assert plan.answer_goal == "Compare the repo docs with the product site"
    assert "comparison brief" in plan.recommended_answer_shape
    assert plan.coverage_priorities
    assert plan.acceptance_criteria == ["Return evidence refs.", "Use direct evidence."]
    assert plan.subproblems == [
        ResearchConversationSubproblem(
            problem_id="scope",
            question="What does the repo currently say?",
            why_it_matters="Need the current internal claim.",
            evidence_to_seek="Read the repo docs.",
            search_hint="Start in docs/.",
            depends_on=["inventory"],
        )
    ]
    assert plan.workstreams == [
        ResearchConversationWorkstream(
            stream_id="current-state",
            title="Current repo state",
            goal="Resolve the local repo claim",
            why_it_matters="Need one coherent local evidence stream.",
            subproblem_ids=["scope"],
            aggregation_hint="Fold this back into the local-vs-external comparison.",
        )
    ]
    assert plan.evidence_targets == [
        ResearchConversationEvidenceTarget(
            target_id="local-price",
            claim="Current repo price",
            entity_type="",
            metric_kind="price",
            series_kind="",
            comparison_basis="",
            why_it_matters="Need one exact local fact target.",
            related_subproblem_ids=["scope"],
            as_of="2026-04-14",
            unit_or_format="explicit price with currency and unit",
            geography_or_scope="",
            accepted_source_families=[
                "official exchange or benchmark",
                "industry benchmark or issuer",
                "authoritative market data",
            ],
            preferred_sites=["docs.example.com"],
            aliases=["repo price", "Current repo price"],
            acceptable_proxy="Use the nearest release tag if the docs lag by one publish cycle.",
            stop_condition="Stop when the repo docs or config file gives one explicit price.",
            not_found_guidance="Only call this not found after repo docs plus config search both fail.",
            not_available_guidance="Call this not available if the repo does not publish a price at all.",
        )
    ]


def test_intention_plan_normalization_backfills_unassigned_subproblems_into_workstreams() -> None:
    plan = _normalize_intention_plan(
        {
            "answer_goal": "Answer the discrepancy",
            "refined_objective": "Resolve the discrepancy with direct evidence",
            "subproblems": [
                {
                    "problem_id": "repo",
                    "question": "What does the repo currently say?",
                    "why_it_matters": "Need the local claim.",
                    "evidence_to_seek": "Read the repo docs.",
                },
                {
                    "problem_id": "external",
                    "question": "What does the external page say?",
                    "why_it_matters": "Need the external claim.",
                    "evidence_to_seek": "Read the canonical product page.",
                },
            ],
            "workstreams": [
                {
                    "stream_id": "local",
                    "goal": "Resolve the local claim",
                    "why_it_matters": "This gathers the repo-side evidence.",
                    "subproblem_ids": ["repo"],
                }
            ],
        },
        objective="Answer the discrepancy",
        delivery_target="research memo",
        acceptance_criteria=[],
        context=_context(),
        planning_mode="initial",
        previous_report=None,
    )

    assert [item.stream_id for item in plan.workstreams] == ["local", "stream-2"]
    assert plan.workstreams[0].subproblem_ids == ["repo"]
    assert plan.workstreams[1].subproblem_ids == ["external"]
    assert [item.claim for item in plan.evidence_targets] == [
        "What does the repo currently say?",
        "What does the external page say?",
    ]
    assert plan.evidence_targets[1].related_subproblem_ids == ["external"]


def test_fallback_continuation_does_not_reopen_resolved_fact_from_open_questions() -> None:
    previous_report = ResearchConversationReportSummary(
        status="completed",
        task_id="deep-research-task:2",
        objective="Investigate the discrepancy.",
        delivery_target="research memo",
        findings=["The canonical price was verified from the repo."],
        evidence_summary=["Repo docs and config agree."],
        evidence_refs=["repo:pricing", "repo:config"],
        contradictions=[],
        open_questions=["Can we verify the current published price one more time?"],
        verification_facts=[
            {
                "fact": "Current published price",
                "status": "verified",
                "source": "repo:pricing",
                "as_of": "2026-04-14",
                "note": "Repo docs and config agree on the value.",
            }
        ],
        audit_issues=[
            {
                "kind": "authority",
                "severity": "major",
                "issue": "The competitor-side price still needs a canonical source.",
                "affected_claim": "Competitor published price",
                "required_follow_up": "Check the canonical competitor product page.",
            }
        ],
        quality_gates=_quality_gates(),
        report_readiness="grounded",
        readiness_note="The core price fact is grounded.",
        confidence=0.82,
        recommended_change="Proceed with the grounded value.",
        error=None,
    )

    plan = _fallback_intention_plan(
        objective="Continue the pricing investigation",
        delivery_target="research memo",
        acceptance_criteria=[],
        context=_context(recent_reports=[previous_report]),
        planning_mode="continuation",
        previous_report=previous_report,
    )

    assert all("Current published price" not in item.question for item in plan.subproblems)
    assert any("Competitor published price" in item.question for item in plan.subproblems)
    assert all(target.claim != "Current published price" for target in plan.evidence_targets)


def test_fallback_continuation_uses_session_claim_ledger_to_keep_verified_fact_closed() -> None:
    previous_report = ResearchConversationReportSummary(
        status="completed",
        task_id="deep-research-task:3",
        objective="Continue the pricing investigation.",
        delivery_target="research memo",
        findings=["The canonical price was already verified."],
        evidence_summary=["Repo docs and config agree."],
        evidence_refs=["repo:pricing", "repo:config"],
        contradictions=[],
        open_questions=["Can we verify the current published price one more time?"],
        verification_facts=[],
        audit_issues=[],
        quality_gates=_quality_gates(),
        report_readiness="grounded",
        readiness_note="The core fact is grounded.",
        confidence=0.84,
        recommended_change="Use the grounded price.",
        error=None,
    )

    plan = _fallback_intention_plan(
        objective="Continue the pricing investigation",
        delivery_target="research memo",
        acceptance_criteria=[],
        context=_context(
            recent_reports=[previous_report],
            claim_ledger=[
                {
                    "fact": "Current published price",
                    "status": "verified",
                    "source": "repo:pricing",
                    "as_of": "2026-04-14",
                    "note": "Repo docs and config agree on the value.",
                }
            ],
        ),
        planning_mode="continuation",
        previous_report=previous_report,
    )

    assert all("Current published price" not in item.question for item in plan.subproblems)
    assert all(target.claim != "Current published price" for target in plan.evidence_targets)


def test_fallback_evidence_target_infers_metric_and_series_identity_for_market_data() -> None:
    plan = _fallback_intention_plan(
        objective="Verify Brent front-month futures settlement and coal ETF AUM as of 2026-04-17.",
        delivery_target="research memo",
        acceptance_criteria=[],
        context=_context(),
        planning_mode="initial",
        previous_report=None,
    )

    claims = {target.claim: target for target in plan.evidence_targets}

    brent_target = next(
        target for target in plan.evidence_targets if "Brent" in target.claim
    )
    assert brent_target.metric_kind in {"settlement_price", "price"}
    assert brent_target.series_kind == "futures_contract_series"
    assert "futures" in brent_target.comparison_basis.lower()

    coal_fund_target = next(
        target
        for target in plan.evidence_targets
        if "AUM" in target.claim or "aum" in target.claim.lower()
    )
    assert coal_fund_target.entity_type == "fund_or_etf"
    assert coal_fund_target.metric_kind == "aum"
    assert "AUM" in coal_fund_target.comparison_basis


def test_turn_normalization_converts_question_like_response_into_clarify() -> None:
    decision = _normalize_turn_decision(
        {
            "action": "respond",
            "public_response": "Which project do you want me to compare against?",
        },
        user_message="compare our architecture against it",
        pending_clarification=None,
        context=_context(),
    )

    assert decision.action == "clarify"
    assert decision.clarifying_question == "Which project do you want me to compare against?"


def test_review_normalization_blocks_done_when_report_has_no_output() -> None:
    report = ResearchConversationReportSummary(
        status="failed",
        task_id="deep-research-task:1",
        objective="Investigate the issue.",
        delivery_target="research memo",
        findings=[],
        evidence_summary=[],
        evidence_refs=[],
        contradictions=[],
        open_questions=[],
        verification_facts=[],
        audit_issues=[],
        quality_gates=[],
        report_readiness="blocked",
        readiness_note="No report was produced.",
        confidence=None,
        recommended_change="",
        error="research pass returned no usable report",
    )

    decision = _normalize_review_decision(
        {
            "action": "done",
            "public_response": "This bounded research pass is done.",
        },
        objective="Investigate the issue.",
        report_summary=report,
    )

    assert isinstance(decision, ResearchConversationReviewDecision)
    assert decision.action == "continue"
    assert "did not produce a concrete grounded report yet" in decision.public_response


def test_review_normalization_blocks_done_when_contradictions_are_low_confidence() -> None:
    report = ResearchConversationReportSummary(
        status="completed",
        task_id="deep-research-task:1",
        objective="Investigate the issue.",
        delivery_target="research memo",
        findings=["One plausible conclusion."],
        evidence_summary=["One source suggests a current answer."],
        evidence_refs=["search:one-source"],
        contradictions=["A second source points the other direction."],
        open_questions=["Status still needs confirmation."],
        verification_facts=[],
        audit_issues=[],
        quality_gates=_quality_gates(),
        report_readiness="provisional",
        readiness_note="One source is not enough yet.",
        confidence=0.2,
        recommended_change="Act on the conclusion.",
        error=None,
    )

    decision = _normalize_review_decision(
        {
            "action": "done",
            "public_response": "This bounded research pass is done.",
        },
        objective="Investigate the issue.",
        report_summary=report,
    )

    assert isinstance(decision, ResearchConversationReviewDecision)
    assert decision.action == "continue"
    assert "contradictions" in decision.public_response


def test_review_normalization_allows_provisional_done_when_objective_authorizes_unverifiable_closure() -> None:
    report = ResearchConversationReportSummary(
        status="completed",
        task_id="deep-research-task:8",
        objective="Retrieve the missing daily benchmark.",
        delivery_target="research memo",
        findings=[
            "All targeted sources failed to yield the exact daily benchmark for the anchor date.",
            "The remaining benchmark is explicitly marked unverifiable because the authoritative sources stayed inaccessible after repeated attempts.",
        ],
        evidence_summary=[
            "Alternative sources only returned adjacent-date or methodology evidence.",
        ],
        evidence_refs=[
            "search:cctd-weekly-archive",
            "search:industry-alternative",
        ],
        contradictions=[],
        open_questions=["Exact anchor-date benchmark remains unverifiable."],
        verification_facts=[
            {
                "fact": "Anchor-date daily benchmark",
                "status": "unverified",
                "source": "cctd.example",
                "as_of": "2026-04-17",
                "note": "Unable to verify from primary sources after repeated attempts.",
            }
        ],
        audit_issues=[
            {
                "kind": "freshness",
                "severity": "major",
                "issue": "Anchor-date benchmark remains unverifiable because the primary source stayed inaccessible.",
                "affected_claim": "Anchor-date daily benchmark",
                "required_follow_up": "Keep the conclusion provisional and confidence-downgraded.",
            }
        ],
        quality_gates=[
            {
                **gate,
                "status": (
                    "fail"
                    if gate["gate"] in {"time_anchor", "final_status"}
                    else "warn"
                    if gate["gate"] == "source_authority"
                    else gate["status"]
                ),
            }
            for gate in _quality_gates()
        ],
        report_readiness="provisional",
        readiness_note="One anchor-date fact is explicitly unverifiable, so the report remains provisional with downgraded confidence.",
        confidence=0.3,
        recommended_change="Finalize provisionally with explicit caveats rather than looping indefinitely.",
        error=None,
    )

    decision = _normalize_review_decision(
        {
            "action": "continue",
            "public_response": "I need another bounded pass.",
            "next_objective": "Retry the same missing fact again.",
        },
        objective=(
            "If primary sources remain inaccessible after repeated attempts, explicitly mark "
            "the missing fact as unverifiable with confidence downgrade to 0.3 and finalize "
            "provisionally rather than remaining blocked."
        ),
        report_summary=report,
    )

    assert decision.action == "done"
    assert "provisional report" in decision.public_response


def test_review_normalization_allows_best_effort_done_on_explicit_final_pass() -> None:
    report = ResearchConversationReportSummary(
        status="completed",
        task_id="deep-research-task:6",
        objective="Fourth and final bounded research pass.",
        delivery_target="research memo",
        findings=[
            "All four required pillars are populated with grounded dated evidence.",
            "The report is sufficiently complete for a provisional investment assessment with caveats.",
        ],
        evidence_summary=[
            "Coal prices are present but stale by roughly two weeks.",
            "Brent relies on an intraday snapshot rather than the official settlement.",
        ],
        evidence_refs=[
            "search:fortune-brent-apr17",
            "search:sunsirs-coal-apr6",
            "search:china-shenhua-valuations",
        ],
        contradictions=[
            "China Shenhua trailing P/E ranges from 15.08 to 17.12 depending on vendor methodology.",
        ],
        open_questions=[
            "Refresh the stale China coal benchmark before treating the memo as final.",
        ],
        verification_facts=[
            {
                "fact": "China Shenhua trailing P/E ~15-17x",
                "status": "conflicted",
                "source": "multi-vendor",
                "as_of": "2026-04-17",
                "note": "Methodology differences drive a bounded range rather than a single value.",
            }
        ],
        audit_issues=[
            {
                "kind": "freshness",
                "severity": "major",
                "issue": "China coal spot prices are 13-16 days stale relative to the target date.",
                "affected_claim": "Qinhuangdao thermal coal benchmark",
                "required_follow_up": "Refresh the daily benchmark before treating the memo as final.",
            }
        ],
        quality_gates=[
            {
                **gate,
                "status": (
                    "warn"
                    if gate["gate"] in {"time_anchor", "source_authority", "numeric_reconciliation"}
                    else "fail"
                    if gate["gate"] == "final_status"
                    else gate["status"]
                ),
            }
            for gate in _quality_gates()
        ],
        report_readiness="blocked",
        readiness_note=(
            "Provisional with caveats: all required pillars are present, but one price series is "
            "stale and Brent is only an intraday snapshot."
        ),
        confidence=0.13,
        recommended_change=(
            "Close provisionally with explicit caveats rather than launching a fifth pass."
        ),
        error=None,
    )

    decision = _normalize_review_decision(
        {
            "action": "continue",
            "public_response": "One more bounded pass is still required.",
            "next_objective": "Retry the same pillars again.",
        },
        objective=(
            "Fourth and final bounded research pass — retrieve at minimum the four required "
            "pillars. After this pass, synthesize a provisional report with all available "
            "fragments and explicit caveats. No fifth pass."
        ),
        report_summary=report,
    )

    assert decision.action == "done"
    assert "explicitly capped further passes" in decision.public_response


def test_review_normalization_blocks_done_when_critical_audit_issue_remains() -> None:
    report = ResearchConversationReportSummary(
        status="completed",
        task_id="deep-research-task:1",
        objective="Investigate the issue.",
        delivery_target="research memo",
        findings=["One plausible conclusion."],
        evidence_summary=["One source suggests a current answer."],
        evidence_refs=["search:one-source"],
        contradictions=[],
        open_questions=[],
        verification_facts=[
            {
                "fact": "Current product status",
                "status": "verified",
                "source": "issuer.example",
                "as_of": "2026-04-15",
                "note": "",
            }
        ],
        audit_issues=[
            {
                "kind": "freshness",
                "severity": "critical",
                "issue": "The report still lacks a final current-state recheck.",
                "affected_claim": "Current product status",
                "required_follow_up": "Re-query the authoritative source immediately before publishing.",
            }
        ],
        quality_gates=_quality_gates(),
        report_readiness="grounded",
        readiness_note="Grounded except for one blocking freshness issue.",
        confidence=0.81,
        recommended_change="Proceed.",
        error=None,
    )

    decision = _normalize_review_decision(
        {
            "action": "done",
            "public_response": "This bounded research pass is done.",
        },
        objective="Investigate the issue.",
        report_summary=report,
    )

    assert decision.action == "continue"
    assert "blocking audit issues" in decision.public_response


def test_review_normalization_allows_done_when_quality_gate_is_missing_but_output_is_grounded() -> None:
    report = ResearchConversationReportSummary(
        status="completed",
        task_id="deep-research-task:1",
        objective="Investigate the issue.",
        delivery_target="research memo",
        findings=["One plausible conclusion."],
        evidence_summary=["One source suggests a current answer."],
        evidence_refs=["search:one-source"],
        contradictions=[],
        open_questions=[],
        verification_facts=[],
        audit_issues=[],
        quality_gates=[
            {
                "gate": "time_anchor",
                "status": "pass",
                "summary": "Current as of runtime.",
                "evidence_ref": "test:evidence",
                "required_follow_up": "",
            }
        ],
        report_readiness="grounded",
        readiness_note="Missing standard gates.",
        confidence=0.81,
        recommended_change="Proceed.",
        error=None,
    )

    decision = _normalize_review_decision(
        {
            "action": "done",
            "public_response": "This bounded research pass is done.",
        },
        objective="Investigate the issue.",
        report_summary=report,
    )

    assert decision.action == "done"
    assert "done" in decision.public_response or "provisional report" in decision.public_response


def test_review_normalization_allows_done_when_quality_gate_warns_but_output_is_grounded() -> None:
    report = ResearchConversationReportSummary(
        status="completed",
        task_id="deep-research-task:1",
        objective="Investigate the issue.",
        delivery_target="research memo",
        findings=["One plausible conclusion."],
        evidence_summary=["One source suggests a current answer."],
        evidence_refs=["search:one-source"],
        contradictions=[],
        open_questions=[],
        verification_facts=[],
        audit_issues=[],
        quality_gates=[
            {
                **gate,
                "status": "warn" if gate["gate"] == "scope_boundary" else gate["status"],
            }
            for gate in _quality_gates()
        ],
        report_readiness="grounded",
        readiness_note="Scope still needs narrowing.",
        confidence=0.81,
        recommended_change="Proceed.",
        error=None,
    )

    decision = _normalize_review_decision(
        {
            "action": "done",
            "public_response": "This bounded research pass is done.",
        },
        objective="Investigate the issue.",
        report_summary=report,
    )

    assert decision.action == "done"
    assert "done" in decision.public_response or "provisional report" in decision.public_response


def test_controller_uses_research_control_hedge_env(monkeypatch) -> None:
    monkeypatch.setenv("DAN_RESEARCH_CONTROL_HEDGE_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("DAN_RESEARCH_CONTROL_HEDGE_DELAY_SECONDS", "0.75")

    controller = ResearchConversationController(provider=object(), model="gpt-test")
    adapter = controller._runner._standalone_runner._completion_provider

    assert adapter._hedge_max_attempts == 3
    assert adapter._hedge_delay_seconds == 0.75


@pytest.mark.asyncio
async def test_controller_turn_routes_explicit_research_request_through_durable_runner(
    monkeypatch,
) -> None:
    captured_requests: list[CompletionRequest] = []

    async def _fake_complete(self, request):
        captured_requests.append(request)
        return CompletionResponse(
            text=json.dumps(
                {
                    "action": "research",
                    "public_response": "I’m starting one bounded research run for this request.",
                    "research_objective": "compare our runtime limits with the docs",
                    "delivery_target": "research memo",
                }
            ),
            raw={},
        )

    monkeypatch.setattr(
        "dan.worker.organisms.research_conversation.ProviderCompletionAdapter.complete",
        _fake_complete,
    )

    controller = ResearchConversationController(provider=object(), model="gpt-test")

    decision, session = await controller.decide_user_turn(
        session=None,
        user_message="compare our runtime limits with the docs",
        pending_clarification=None,
        context=_context(
            recent_conversation=[
                ResearchConversationMessage(role="user", text="what are the runtime limits"),
            ]
        ),
    )

    assert session is not None
    assert decision.action == "research"
    assert decision.research_objective == "compare our runtime limits with the docs"
    assert session.accepted_message_count == 1
    assert len(session.mailbox) == 1
    assert session.mailbox[0].status == "completed"
    assert captured_requests[0].metadata["worker_id"] == "dan-research.orchestrator"
    assert captured_requests[0].metadata["agent_message_index"] == 1
