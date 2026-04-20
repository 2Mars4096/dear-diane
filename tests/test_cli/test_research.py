from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dan.cli.research import (
    DEFAULT_RESEARCH_ACCEPTANCE_CRITERIA,
    DEFAULT_RESEARCH_MAX_SUPERVISION_LOOPS,
    ResearchHeartbeatMonitor,
    ResearchProgressRenderer,
    _apply_evidence_integrity_pass,
    _continuation_reader_count,
    _effective_max_supervision_loops,
    _print_research_report,
    _render_report,
    _render_report_markdown,
    _temporal_frame,
    build_parser,
    main,
)
from dan.cli.research_product import (
    ResearchAuditIssue,
    ResearchCliSession,
    ResearchEvidenceIntegrityRow,
    ResearchOrganismReport,
    ResearchQualityGate,
    ResearchVerificationFact,
    load_research_product_session,
    resolve_research_product_paths,
)
from dan.worker.organism_log import ORGANISM_LOG_SCHEMA_VERSION
from dan.worker.organisms import (
    ResearchConversationEvidenceTarget,
    ResearchConversationReviewDecision,
    ResearchConversationTurnDecision,
    ResearchConversationIntentionPlan,
    ResearchConversationSubproblem,
    ResearchConversationWorkstream,
)


def _quality_gates() -> list[dict[str, str]]:
    return [
        {
            "gate": gate,
            "status": "pass" if gate != "numeric_reconciliation" else "not_applicable",
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


def test_build_parser_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args([])

    assert args.objective is None
    assert args.task_id == "deep-research-task"
    assert args.organism_id == "reference-project-execution"
    assert args.model is None
    assert args.research_readers is None
    assert args.depth is None
    assert args.max_tool_rounds is None
    assert args.max_tool_calls is None
    assert args.max_supervision_loops is None
    assert args.init is False
    assert args.json is False


def test_effective_max_supervision_loops_defaults_to_bounded_product_loop() -> None:
    assert _effective_max_supervision_loops(None) == DEFAULT_RESEARCH_MAX_SUPERVISION_LOOPS
    assert _effective_max_supervision_loops(0) is None


def test_default_acceptance_criteria_do_not_reintroduce_gate_driven_blockers() -> None:
    criteria = " ".join(DEFAULT_RESEARCH_ACCEPTANCE_CRITERIA).lower()

    assert "quality_gates" not in criteria
    assert "claim_object_fit" not in criteria
    assert "final_status" not in criteria
    assert "endless follow-up passes" in criteria


def test_continuation_reader_count_downshifts_auto_followup_passes() -> None:
    assert _continuation_reader_count(
        base_requested_count=None,
        continuation_index=1,
        previous_selected_count=8,
    ) == 4
    assert _continuation_reader_count(
        base_requested_count=7,
        continuation_index=1,
        previous_selected_count=8,
    ) == 7


def test_temporal_frame_prefers_trend_when_range_language_is_present() -> None:
    frame = _temporal_frame(
        objective="Help me compare recent oil and coal price changes over the past 6 months.",
        current_date="2026-04-16",
        timezone_name="Asia/Hong_Kong",
    )

    assert frame["mode"] == "trend"
    assert frame["anchor"] == "2026-04-16"
    assert "2026-04-16" in frame["guidance"]


def test_research_report_normalizes_confidence_labels_and_percentages() -> None:
    report = ResearchOrganismReport(
        status="completed",
        trace_id="trace-research",
        organism_id="reference-project-execution",
        organ_id="deep-research",
        task_id="deep-research-task:1",
        objective="Investigate the discrepancy",
        confidence="MEDIUM-HIGH (70%)",
    )

    assert report.confidence == 0.7


def test_research_report_normalizes_verification_facts() -> None:
    report = ResearchOrganismReport(
        status="completed",
        trace_id="trace-research",
        organism_id="reference-project-execution",
        organ_id="deep-research",
        task_id="deep-research-task:1",
        objective="Investigate the discrepancy",
        verification_facts=[
            {
                "claim": "KOL is a live tradable ETF.",
                "status": "contradicted",
                "evidence_ref": "sec.gov",
                "date": "2026-04-15",
                "rationale": "Primary source says it was terminated.",
            },
            "Coal demand still needs direct confirmation.",
        ],
    )

    assert report.verification_facts == [
        ResearchVerificationFact(
            fact="KOL is a live tradable ETF.",
            status="conflicted",
            source="sec.gov",
            as_of="2026-04-15",
            note="Primary source says it was terminated.",
            claim_key="kol is a live tradable etf",
            authority_rank=100,
        ),
        ResearchVerificationFact(
            fact="Coal demand still needs direct confirmation.",
            status="unverified",
            source="",
            as_of="",
            note="",
            claim_key="coal demand still needs direct confirmation",
            authority_rank=0,
        ),
    ]


def test_research_report_normalizes_audit_issues_and_readiness() -> None:
    report = ResearchOrganismReport(
        status="completed",
        trace_id="trace-research",
        organism_id="reference-project-execution",
        organ_id="deep-research",
        task_id="deep-research-task:1",
        objective="Investigate the discrepancy",
        audit_issues=[
            {
                "type": "source authority",
                "level": "blocking",
                "gap": "The key product fact is backed only by retail quote pages.",
                "claim": "Current ticker status",
                "next_step": "Verify against issuer or exchange sources.",
            }
        ],
        report_readiness="decision ready",
    )

    assert report.audit_issues == [
        ResearchAuditIssue(
            kind="authority",
            severity="critical",
            issue="The key product fact is backed only by retail quote pages.",
            affected_claim="Current ticker status",
            required_follow_up="Verify against issuer or exchange sources.",
        )
    ]
    assert report.report_readiness == "actionable"


def test_research_report_normalizes_quality_gates() -> None:
    report = ResearchOrganismReport(
        status="completed",
        trace_id="trace-research",
        organism_id="reference-project-execution",
        organ_id="deep-research",
        task_id="deep-research-task:1",
        objective="Investigate the discrepancy",
        quality_gates=[
            {
                "name": "freshness",
                "status": "blocked",
                "note": "The artifact mixes historical and current timestamps.",
                "source": "report:timeline",
                "next_step": "Declare historical snapshot or current-as-of-runtime mode.",
            }
        ],
    )

    assert report.quality_gates == [
        ResearchQualityGate(
            gate="time_anchor",
            status="fail",
            summary="The artifact mixes historical and current timestamps.",
            evidence_ref="report:timeline",
            required_follow_up="Declare historical snapshot or current-as-of-runtime mode.",
        )
    ]


def test_research_report_derives_evidence_integrity_rows() -> None:
    report = ResearchOrganismReport(
        status="completed",
        trace_id="trace-research",
        organism_id="reference-project-execution",
        organ_id="deep-research",
        task_id="deep-research-task:1",
        objective="Investigate the discrepancy",
        verification_facts=[
            {
                "fact": "KOL ETF status",
                "status": "verified",
                "source": "sec.gov",
                "as_of": "2026-04-18",
                "note": "Primary filing confirms liquidation.",
                "entity_type": "fund_or_etf",
                "metric_kind": "status_or_listing",
                "geography_or_scope": "United States",
            },
            {
                "fact": "Qinhuangdao 5500 benchmark on April 18, 2026",
                "status": "unverified",
                "source": "argusmedia.com",
                "note": "Only a weekly proxy series was accessible.",
                "metric_kind": "price_or_nav",
                "series_kind": "spot_or_physical",
                "comparison_basis": "proxy only",
            },
        ],
        audit_issues=[
            {
                "kind": "method",
                "severity": "major",
                "issue": "Exact daily benchmark was not accessible; only weekly proxy data was found.",
                "affected_claim": "Qinhuangdao 5500 benchmark on April 18, 2026",
                "required_follow_up": "Mark as proxy or find a same-day benchmark print.",
            }
        ],
    )

    assert report.evidence_integrity == [
        ResearchEvidenceIntegrityRow(
            claim="KOL ETF status",
            claim_key="united states|fund or etf|status or listing|kol etf status",
            status="accepted",
            source_identity="matched",
            metric_identity="matched",
            unit_scale_consistency="missing",
            time_alignment="matched",
            scope_alignment="matched",
            same_source_consistency="matched",
            source="sec.gov",
            as_of="2026-04-18",
            rationale="Primary filing confirms liquidation.",
            entity_type="fund_or_etf",
            metric_kind="status_or_listing",
            geography_or_scope="United States",
        ),
        ResearchEvidenceIntegrityRow(
            claim="Qinhuangdao 5500 benchmark on April 18, 2026",
            claim_key=(
                "price or nav|spot or physical|proxy only|"
                "qinhuangdao 5500 benchmark on april 18 2026"
            ),
            status="accepted_with_proxy",
            source_identity="matched",
            metric_identity="unclear",
            unit_scale_consistency="unclear",
            time_alignment="missing",
            scope_alignment="missing",
            same_source_consistency="unclear",
            source="argusmedia.com",
            as_of="",
            rationale="Only a weekly proxy series was accessible.",
            metric_kind="price_or_nav",
            series_kind="spot_or_physical",
            comparison_basis="proxy only",
        ),
    ]


def test_evidence_integrity_pass_downgrades_optimistic_report() -> None:
    report = ResearchOrganismReport(
        status="completed",
        trace_id="trace-research",
        organism_id="reference-project-execution",
        organ_id="deep-research",
        task_id="deep-research-task:1",
        objective="Investigate the discrepancy",
        report_readiness="actionable",
        verification_facts=[
            {
                "fact": "April 18 benchmark print",
                "status": "unverified",
                "source": "argusmedia.com",
                "note": "Only a weekly proxy series was accessible.",
                "metric_kind": "price_or_nav",
                "series_kind": "spot_or_physical",
                "comparison_basis": "proxy only",
            }
        ],
        quality_gates=_quality_gates(),
    )

    _apply_evidence_integrity_pass(report)

    assert report.report_readiness == "provisional"
    assert report.artifact_mode == "provisional_report"
    assert any(
        gate.gate == "final_status" and gate.status == "warn"
        for gate in report.quality_gates
    )
    assert "Evidence integrity:" in report.readiness_note


def test_print_research_report_defaults_to_user_facing_memo(capsys) -> None:
    _print_research_report(
        {
            "status": "completed",
            "trace_id": "trace-research",
            "task_id": "deep-research-task:1",
            "objective": "Investigate the discrepancy",
            "temporal_mode": "current",
            "temporal_anchor": "2026-04-16",
            "temporal_guidance": "Temporal frame: current-as-of-runtime.",
            "delivery_target": "research memo",
            "selected_reader_count": 4,
            "depth_profile": "standard",
            "report_readiness": "grounded",
            "readiness_note": "Enough evidence for a bounded grounded note.",
            "confidence": 0.82,
            "handoff_count": 3,
            "signal_count": 5,
            "findings": ["KOL was terminated in 2020."],
            "evidence_summary": ["Primary issuer and clearing sources agree."],
            "evidence_refs": ["VanEck press release"],
            "recommended_change": "Do not use stale KOL quote data.",
            "verification_facts": [
                {
                    "fact": "KOL was terminated in 2020.",
                    "status": "verified",
                    "source": "sec.gov",
                    "as_of": "2026-04-15",
                    "note": "Primary filing confirmed termination.",
                }
            ],
            "audit_issues": [
                {
                    "severity": "major",
                    "kind": "freshness",
                    "issue": "One 'current' claim still lacks a final recheck.",
                    "affected_claim": "Current import figure",
                    "required_follow_up": "Re-query the latest customs release before publishing.",
                }
            ],
            "quality_gates": [
                {
                    "gate": "time_anchor",
                    "status": "fail",
                    "summary": "Historical and current timestamps are mixed.",
                    "evidence_ref": "report:timeline",
                    "required_follow_up": "Pick one time mode.",
                }
            ],
        }
    )

    stdout = capsys.readouterr().out
    assert "Research Report" in stdout
    assert "Objective: Investigate the discrepancy" in stdout
    assert "Temporal frame: current (2026-04-16)" in stdout
    assert "Recommendation:" in stdout
    assert "Findings:" in stdout
    assert "Evidence Summary:" in stdout
    assert "Evidence Refs:" in stdout
    assert "KOL was terminated in 2020." in stdout
    assert "Primary issuer and clearing sources agree." in stdout
    assert "Do not use stale KOL quote data." in stdout
    assert "One 'current' claim still lacks a final recheck." in stdout
    assert "Status:" not in stdout
    assert "Run ID:" not in stdout
    assert "Confidence:" not in stdout
    assert "Activity:" not in stdout
    assert "Verification Appendix:" not in stdout
    assert "Audit Appendix:" not in stdout
    assert "Quality Gates:" not in stdout
    assert "Historical and current timestamps are mixed." not in stdout


def test_print_research_report_renders_blocker_artifact_without_recommendation(capsys) -> None:
    _print_research_report(
        {
            "status": "incomplete",
            "artifact_mode": "blocker_report",
            "objective": "Verify the ETF status and coal benchmark.",
            "temporal_mode": "current",
            "temporal_anchor": "2026-04-17",
            "readiness_note": "The last pass still lacks an authoritative ETF status source.",
            "recommended_change": "Buy the ETF immediately.",
            "findings": ["Retail quote pages disagree with issuer data."],
            "quality_gates": [
                {
                    "gate": "final_status",
                    "status": "fail",
                    "summary": "Another bounded pass is still required.",
                    "required_follow_up": "Verify the issuer or exchange status directly.",
                }
            ],
            "audit_issues": [
                {
                    "severity": "major",
                    "kind": "authority",
                    "issue": "Current ETF status is still backed by stale quote pages.",
                    "required_follow_up": "Check issuer and exchange records.",
                }
            ],
        }
    )

    stdout = capsys.readouterr().out
    assert "Research Blocker Report" in stdout
    assert "Why it is blocked:" in stdout
    assert "Next steps:" in stdout
    assert "Recommendation:" not in stdout
    assert "Buy the ETF immediately." not in stdout


def test_render_report_markdown_matches_human_memo_shape() -> None:
    markdown = _render_report_markdown(
        {
            "status": "completed",
            "artifact_mode": "provisional_report",
            "objective": "Investigate the discrepancy",
            "temporal_mode": "current",
            "temporal_anchor": "2026-04-16",
            "report_readiness": "provisional",
            "readiness_note": "One current metric still relies on a proxy.",
            "recommended_change": "Do not rely on stale KOL quote data.",
            "findings": [
                "## ETF Status",
                "KOL was terminated in 2020.",
            ],
            "evidence_summary": ["Primary issuer and clearing sources agree."],
            "evidence_refs": ["VanEck press release"],
            "verification_facts": [
                {
                    "fact": "KOL was terminated in 2020.",
                    "status": "verified",
                    "source": "sec.gov",
                    "as_of": "2026-04-15",
                    "note": "Primary filing confirmed termination.",
                }
            ],
            "audit_issues": [
                {
                    "severity": "major",
                    "kind": "freshness",
                    "issue": "One current claim still lacks a final recheck.",
                    "required_follow_up": "Re-query the latest customs release before publishing.",
                }
            ],
            "quality_gates": [
                {
                    "gate": "time_anchor",
                    "status": "warn",
                    "summary": "Temporal frame was explicit but one current metric stayed provisional.",
                }
            ],
        }
    )

    assert markdown.startswith("# Provisional Research Report")
    assert "## Recommendation" in markdown
    assert "## Findings" in markdown
    assert "## Sources" in markdown
    assert "## Caveats" in markdown
    assert "\n## ETF Status\n" in markdown
    assert "- ## ETF Status" not in markdown
    assert "## Verification Appendix" not in markdown
    assert "## Audit Appendix" not in markdown
    assert "## Quality Gates" not in markdown
    assert "KOL was terminated in 2020." in markdown
    assert "Do not rely on stale KOL quote data." in markdown


def test_render_report_writes_markdown_when_output_extension_is_md(
    tmp_path, capsys
) -> None:
    report = ResearchOrganismReport(
        status="completed",
        trace_id="trace-research",
        organism_id="reference-project-execution",
        organ_id="deep-research",
        task_id="deep-research-task:1",
        objective="Investigate the discrepancy",
        findings=["KOL was terminated in 2020."],
        evidence_summary=["Primary issuer and clearing sources agree."],
        evidence_refs=["VanEck press release"],
        report_readiness="grounded",
        recommended_change="Do not use stale KOL quote data.",
    )

    destination = tmp_path / "report.md"
    _render_report(
        report,
        as_json=False,
        output_path=str(destination),
        workspace_root=tmp_path,
    )

    stdout = capsys.readouterr().out
    assert "Research Report" in stdout
    markdown = destination.read_text(encoding="utf-8")
    assert markdown.startswith("# Research Report")
    assert "## Findings" in markdown
    assert "Do not use stale KOL quote data." in markdown
    assert "## Verification Appendix" not in markdown


def test_progress_renderer_shows_research_heartbeat(capsys) -> None:
    renderer = ResearchProgressRenderer(enabled=True)
    renderer(
        {
            "event": "research.heartbeat",
            "worker_id": "deep-research.reader-e",
            "phase": "tool",
            "detail": "web_search: CCTD China Coal Transportation Distribution port inventory levels 2025",
            "elapsed_seconds": 19,
        }
    )

    stdout = capsys.readouterr().out
    assert "still running" in stdout
    assert "reader-e" in stdout
    assert "19s" in stdout
    assert "web_search" in stdout


def test_progress_renderer_can_show_public_model_trace(capsys) -> None:
    renderer = ResearchProgressRenderer(enabled=True, show_model_trace=True)

    renderer(
        {
            "event": "model.requested",
            "round": 1,
            "model": "gpt-test",
            "tool_count": 0,
            "message_count": 2,
            "total_input_chars": 128,
            "max_tokens": 4096,
            "request_timeout_seconds": 120.0,
            "request_mode": "complete",
            "worker_id": "deep-research.lead",
        }
    )
    renderer(
        {
            "event": "model.responded",
            "round": 1,
            "model": "gpt-test",
            "tool_calls": ["web_search", "web_fetch"],
            "finish_reason": "tool_calls",
            "usage_total_tokens": 321,
            "usage_cached_input_tokens": 64,
            "text_chars": 57,
            "text": "Inspecting contradictory evidence before launching the next pass.",
            "worker_id": "deep-research.lead",
        }
    )

    stdout = capsys.readouterr().out
    assert "[lead][model] request: round=1 model=gpt-test tools=0 msgs=2 chars=128 max_tokens=4096 timeout=120s mode=complete" in stdout
    assert "[lead][model] response: round=1 model=gpt-test tool_calls=web_search, web_fetch finish_reason=tool_calls tokens=321 cached=64 chars=57" in stdout
    assert "[lead][model] preview: Inspecting contradictory evidence before launching the next pass." in stdout


def test_progress_renderer_can_show_streamed_public_model_trace(capsys) -> None:
    renderer = ResearchProgressRenderer(enabled=True, show_model_trace=True)

    renderer(
        {
            "event": "model.stream.started",
            "round": 1,
            "model": "gpt-test",
            "worker_id": "deep-research.lead",
        }
    )
    renderer(
        {
            "event": "model.stream.delta",
            "round": 1,
            "model": "gpt-test",
            "delta": "Streaming",
            "worker_id": "deep-research.lead",
        }
    )
    renderer(
        {
            "event": "model.stream.delta",
            "round": 1,
            "model": "gpt-test",
            "delta": " preview",
            "worker_id": "deep-research.lead",
        }
    )
    renderer(
        {
            "event": "model.stream.completed",
            "round": 1,
            "model": "gpt-test",
            "worker_id": "deep-research.lead",
        }
    )
    renderer(
        {
            "event": "model.responded",
            "round": 1,
            "model": "gpt-test",
            "finish_reason": "stream",
            "text": "Streaming preview",
            "streamed": True,
            "usage_total_tokens": 12,
            "text_chars": 17,
            "worker_id": "deep-research.lead",
        }
    )

    stdout = capsys.readouterr().out
    assert "[lead][model] stream: Streaming preview" in stdout
    assert "[lead][model] response: round=1 model=gpt-test finish_reason=stream tokens=12 chars=17" in stdout
    assert "[lead][model] preview:" not in stdout


@pytest.mark.asyncio
async def test_research_heartbeat_monitor_emits_after_quiet_interval() -> None:
    events: list[dict[str, object]] = []

    def _collector(event: dict[str, object]) -> None:
        events.append(dict(event))

    monitor = ResearchHeartbeatMonitor(
        event_callback=_collector,
        enabled=True,
        idle_seconds=0.01,
        repeat_seconds=0.01,
        poll_seconds=0.005,
    )
    monitor.observe(
        {
            "event": "tool.started",
            "worker_id": "deep-research.reader-e",
            "tool_id": "web_search",
            "arguments": {"query": "CCTD port inventory levels"},
        }
    )
    await monitor.start()
    try:
        await asyncio.sleep(0.03)
    finally:
        await monitor.stop()

    heartbeat_events = [
        event for event in events
        if event.get("event") == "research.heartbeat"
    ]
    assert heartbeat_events
    assert heartbeat_events[0]["worker_id"] == "deep-research.reader-e"
    assert heartbeat_events[0]["phase"] == "tool"
    assert "CCTD port inventory levels" in str(heartbeat_events[0]["detail"])


@pytest.mark.asyncio
async def test_research_heartbeat_monitor_emits_control_stage_after_quiet_interval() -> None:
    events: list[dict[str, object]] = []

    def _collector(event: dict[str, object]) -> None:
        events.append(dict(event))

    monitor = ResearchHeartbeatMonitor(
        event_callback=_collector,
        enabled=True,
        idle_seconds=0.01,
        repeat_seconds=0.01,
        poll_seconds=0.005,
    )
    await monitor.start()
    try:
        monitor.note_control_stage(
            phase="planning",
            detail="continuation objective",
        )
        await asyncio.sleep(0.03)
    finally:
        await monitor.stop()

    heartbeat_events = [
        event for event in events
        if event.get("event") == "research.heartbeat"
    ]
    assert heartbeat_events
    assert heartbeat_events[0]["phase"] == "planning"
    assert "continuation objective" in str(heartbeat_events[0]["detail"])


def test_main_lists_local_tools(capsys) -> None:
    exit_code = main(["--list-tools"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "file_read [file]" in stdout
    assert "web_search [web]" in stdout


def test_main_json_uses_research_runner(tmp_path, capsys, monkeypatch) -> None:
    async def _fake_runner(*_args, **kwargs):
        assert kwargs["model"] == "gpt-test"
        assert kwargs["research_reader_count"] == 6
        assert kwargs["depth_profile"] == "deep"
        assert kwargs["max_tool_rounds"] == 12
        assert kwargs["max_tool_calls"] == 48
        assert kwargs["tool_ids"] == [
            "file_read",
            "web_search",
            "git_status",
        ]
        assert Path(kwargs["workspace_root"]) == (tmp_path / "workspace").resolve()
        return {
            "status": "completed",
            "trace_id": "trace-research",
            "organism_id": "reference-project-execution",
            "organ_id": "deep-research",
            "task_id": "deep-research-task:1",
            "objective": kwargs["objective"],
            "delivery_target": kwargs["delivery_target"],
            "findings": ["Grounded finding."],
            "evidence_summary": ["repo evidence"],
            "evidence_refs": ["repo:evidence"],
            "contradictions": [],
            "open_questions": ["Which source is canonical?"],
            "verification_facts": [],
            "audit_issues": [],
            "quality_gates": _quality_gates(),
            "report_readiness": "grounded",
            "readiness_note": "Enough evidence for a bounded grounded report.",
            "confidence": 0.78,
            "recommended_change": "Verify the canonical source.",
            "selected_reader_count": 6,
            "selected_reader_briefs": ["brief-1", "brief-2"],
            "depth_profile": kwargs["depth_profile"],
            "max_tool_rounds": kwargs["max_tool_rounds"],
            "max_tool_calls": kwargs["max_tool_calls"],
            "outputs": {"confidence": 0.78},
            "handoff_count": 3,
            "signal_count": 5,
            "error": None,
            "stage_records": [],
        }

    monkeypatch.setattr("dan.cli.research._build_live_provider", lambda *args, **kwargs: object())

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            ResearchConversationTurnDecision(
                action="research",
                public_response="Starting one bounded research run.",
                research_objective=user_message,
                delivery_target="research memo",
            ),
            durable_session,
        )

    async def _fake_review(self, *, session, objective, report_summary, context):
        return (
            ResearchConversationReviewDecision(
                action="done",
                public_response="This bounded research pass is done.",
            ),
            session,
        )

    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.review_research_result",
        _fake_review,
    )
    monkeypatch.setattr("dan.cli.research.run_research_organism_live", _fake_runner)

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--workspace",
            str(tmp_path / "workspace"),
            "--workdir",
            str(tmp_path / "workdir"),
            "--depth",
            "deep",
            "--research-readers",
            "6",
            "--tool",
            "file_read",
            "--tool",
            "web_search",
            "--tool",
            "shell_command",
            "--tool",
            "git_status",
            "--json",
            "Investigate the discrepancy",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["trace_id"] == "trace-research"
    assert payload["confidence"] == 0.78
    session_payload = json.loads(
        (tmp_path / "workspace" / ".dan-research" / "session.json").read_text()
    )
    assert session_payload["workspace_root"] == str((tmp_path / "workspace").resolve())
    assert len(session_payload["turns"]) == 1
    transcript_lines = (
        tmp_path / "workspace" / ".dan-research" / "transcript.jsonl"
    ).read_text().strip().splitlines()
    assert len(transcript_lines) == 1


def test_main_uses_intention_plan_to_refine_research_run(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    runner_calls: dict[str, object] = {}

    async def _fake_runner(*_args, **kwargs):
        runner_calls["objective"] = kwargs["objective"]
        runner_calls["reader_briefs"] = list(kwargs["reader_briefs"])
        runner_calls["evidence_summaries"] = list(kwargs["evidence_summaries"])
        runner_calls["research_reader_count"] = kwargs["research_reader_count"]
        return {
            "status": "completed",
            "trace_id": "trace-research",
            "organism_id": "reference-project-execution",
            "organ_id": "deep-research",
            "task_id": kwargs["task_id"],
            "objective": kwargs["objective"],
            "delivery_target": kwargs["delivery_target"],
            "findings": ["Grounded finding."],
            "evidence_summary": ["repo evidence"],
            "evidence_refs": ["repo:evidence"],
            "contradictions": [],
            "open_questions": [],
            "verification_facts": [],
            "audit_issues": [],
            "quality_gates": _quality_gates(),
            "report_readiness": "grounded",
            "readiness_note": "Enough evidence for a bounded grounded report.",
            "confidence": 0.78,
            "recommended_change": "Verify the canonical source.",
            "selected_reader_count": 4,
            "selected_reader_briefs": kwargs["reader_briefs"],
            "depth_profile": kwargs["depth_profile"],
            "max_tool_rounds": kwargs["max_tool_rounds"],
            "max_tool_calls": kwargs["max_tool_calls"],
            "outputs": {"confidence": 0.78},
            "handoff_count": 3,
            "signal_count": 5,
            "error": None,
            "stage_records": [],
        }

    monkeypatch.setattr("dan.cli.research._build_live_provider", lambda *args, **kwargs: object())

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            ResearchConversationTurnDecision(
                action="research",
                public_response="Starting one bounded research run.",
                research_objective=user_message,
                delivery_target="research memo",
            ),
            durable_session,
        )

    async def _fake_plan(
        self,
        *,
        session,
        objective,
        delivery_target,
        acceptance_criteria,
        context,
        planning_mode="initial",
        previous_report=None,
    ):
        _ = delivery_target, acceptance_criteria, context, previous_report
        return (
            ResearchConversationIntentionPlan(
                answer_goal=objective,
                refined_objective="Resolve the repo/docs discrepancy with direct evidence.",
                plan_summary="Break the task into a scoped repo pass and a scoped external pass.",
                subproblems=[
                    ResearchConversationSubproblem(
                        problem_id="repo",
                        question="What does the repo currently say?",
                        why_it_matters="This is the local claim we need to compare.",
                        evidence_to_seek="Read the authoritative repo docs and config files.",
                        search_hint="Start in docs/ and the pricing config.",
                    ),
                    ResearchConversationSubproblem(
                        problem_id="external",
                        question="What does the external product surface currently say?",
                        why_it_matters="This is the external claim we need to compare.",
                        evidence_to_seek="Find the strongest current external source.",
                        search_hint="Prefer the canonical product or release page.",
                    ),
                ],
                workstreams=[
                    ResearchConversationWorkstream(
                        stream_id="local",
                        title="Local repo claim",
                        goal="Resolve the current local claim",
                        why_it_matters="This stream grounds the repo-side evidence.",
                        subproblem_ids=["repo"],
                        aggregation_hint="Use this as the local half of the final discrepancy comparison.",
                    ),
                    ResearchConversationWorkstream(
                        stream_id="external",
                        title="External product claim",
                        goal="Resolve the current external claim",
                        why_it_matters="This stream grounds the external evidence.",
                        subproblem_ids=["external"],
                        aggregation_hint="Use this as the external half of the final discrepancy comparison.",
                    ),
                ],
                evidence_targets=[
                    ResearchConversationEvidenceTarget(
                        target_id="repo-price",
                        claim="Current repo price",
                        why_it_matters="The local price fact needs an exact current value.",
                        related_subproblem_ids=["repo"],
                        as_of="2026-04-16",
                        unit_or_format="explicit price with currency and unit",
                        geography_or_scope="",
                        accepted_source_families=[
                            "primary source",
                            "authoritative secondary source",
                        ],
                        preferred_sites=["docs.example.com"],
                        aliases=["repo price", "pricing config"],
                        acceptable_proxy="If the docs lag, use the nearest release-tagged config value and label it as a proxy.",
                        stop_condition="Stop when one authoritative repo file gives one exact current price.",
                        not_found_guidance="Treat this as not found only after docs plus config search both fail.",
                        not_available_guidance="Treat this as not available if the repo does not publish a price at all.",
                    )
                ],
            ),
            session,
        )

    async def _fake_review(self, *, session, objective, report_summary, context):
        _ = objective, report_summary, context
        return (
            ResearchConversationReviewDecision(
                action="done",
                public_response="This bounded research pass is done.",
            ),
            session,
        )

    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.plan_research_intention",
        _fake_plan,
    )
    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.review_research_result",
        _fake_review,
    )
    monkeypatch.setattr("dan.cli.research.run_research_organism_live", _fake_runner)

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--workspace",
            str(tmp_path / "workspace"),
            "--json",
            "Investigate the discrepancy",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["objective"] == "Resolve the repo/docs discrepancy with direct evidence."
    assert runner_calls["objective"] == "Resolve the repo/docs discrepancy with direct evidence."
    assert any(
        "Workstream local" in brief
        for brief in runner_calls["reader_briefs"]
    )
    assert runner_calls["research_reader_count"] == 2
    assert any(
        "Plan summary: Break the task into a scoped repo pass and a scoped external pass."
        == summary
        for summary in runner_calls["evidence_summaries"]
    )
    assert any(
        "Preferred sites: docs.example.com" in brief and "Proxy rule:" in brief
        for brief in runner_calls["reader_briefs"]
    )
    assert any(
        "Workstream external" in summary
        for summary in runner_calls["evidence_summaries"]
    )
    assert any(
        summary.startswith("Fact target repo-price: Current repo price")
        for summary in runner_calls["evidence_summaries"]
    )


def test_main_marks_report_incomplete_when_review_needs_another_pass(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    calls = {"runner": 0, "review": 0}

    async def _fake_runner(*_args, **kwargs):
        calls["runner"] += 1
        return {
            "status": "completed",
            "trace_id": f"trace-research-{calls['runner']}",
            "organism_id": "reference-project-execution",
            "organ_id": "deep-research",
            "task_id": kwargs["task_id"],
            "objective": kwargs["objective"],
            "temporal_mode": "current",
            "temporal_anchor": "2026-04-16",
            "temporal_window": "",
            "temporal_guidance": "Temporal frame: current-as-of-runtime.",
            "delivery_target": kwargs["delivery_target"],
            "findings": ["Grounded finding."],
            "evidence_summary": ["repo evidence"],
            "evidence_refs": ["repo:evidence"],
            "contradictions": [],
            "open_questions": ["One more verification pass would help."],
            "verification_facts": [],
            "audit_issues": [],
            "quality_gates": _quality_gates(),
            "report_readiness": "grounded",
            "readiness_note": "Enough evidence for a bounded grounded note.",
            "confidence": 0.78,
            "recommended_change": "Verify the canonical source.",
            "selected_reader_count": 4,
            "selected_reader_briefs": ["brief-1", "brief-2"],
            "depth_profile": kwargs["depth_profile"],
            "max_tool_rounds": kwargs["max_tool_rounds"],
            "max_tool_calls": kwargs["max_tool_calls"],
            "outputs": {"confidence": 0.78},
            "handoff_count": 3,
            "signal_count": 5,
            "error": None,
            "stage_records": [],
        }

    monkeypatch.setattr("dan.cli.research._build_live_provider", lambda *args, **kwargs: object())

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            ResearchConversationTurnDecision(
                action="research",
                public_response="Starting one bounded research run.",
                research_objective=user_message,
                delivery_target="research memo",
            ),
            durable_session,
        )

    async def _fake_review(self, *, session, objective, report_summary, context):
        calls["review"] += 1
        return (
            ResearchConversationReviewDecision(
                action="continue",
                public_response="One more bounded pass is still required.",
                next_objective=objective,
            ),
            session,
        )

    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.review_research_result",
        _fake_review,
    )
    monkeypatch.setattr("dan.cli.research.run_research_organism_live", _fake_runner)

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--max-supervision-loops",
            "2",
            "--workspace",
            str(tmp_path / "workspace"),
            "--workdir",
            str(tmp_path / "workdir"),
            "--json",
            "Investigate the discrepancy",
        ]
    )

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert calls["runner"] == 2
    assert calls["review"] == 2
    assert payload["status"] == "incomplete"
    assert payload["report_readiness"] == "blocked"
    assert payload["artifact_mode"] == "blocker_report"
    final_status_gates = [
        gate for gate in payload["quality_gates"]
        if gate.get("gate") == "final_status"
    ]
    assert final_status_gates
    assert final_status_gates[-1]["status"] == "fail"
    assert "artifact is returning incomplete" in final_status_gates[-1]["summary"]


def test_main_closes_provisionally_on_explicit_final_pass_at_cap(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    calls = {"runner": 0, "review": 0}
    final_pass_objective = (
        "Fourth and final bounded research pass — retrieve at minimum the required pillars. "
        "After this pass, synthesize a provisional report with all available fragments and "
        "explicit caveats. No fifth pass."
    )

    async def _fake_runner(*_args, **kwargs):
        calls["runner"] += 1
        return {
            "status": "completed",
            "trace_id": f"trace-research-final-{calls['runner']}",
            "organism_id": "reference-project-execution",
            "organ_id": "deep-research",
            "task_id": kwargs["task_id"],
            "objective": kwargs["objective"],
            "temporal_mode": "current",
            "temporal_anchor": "2026-04-19",
            "temporal_window": "",
            "temporal_guidance": "Temporal frame: current-as-of-runtime.",
            "delivery_target": kwargs["delivery_target"],
            "findings": [
                "All required pillars are populated with grounded dated evidence.",
                "The report is sufficiently complete for a provisional assessment with caveats.",
            ],
            "evidence_summary": [
                "Coal prices are present but stale.",
                "Brent is present but only as an intraday snapshot.",
            ],
            "evidence_refs": ["search:brent", "search:coal", "search:equity"],
            "contradictions": [
                "China Shenhua trailing P/E varies across vendors within a bounded range.",
            ],
            "open_questions": ["Refresh the stale benchmark before treating the memo as final."],
            "verification_facts": [
                {
                    "fact": "China Shenhua trailing P/E ~15-17x",
                    "status": "conflicted",
                    "source": "multi-vendor",
                    "as_of": "2026-04-17",
                    "note": "Different EPS methods drive a bounded range.",
                }
            ],
            "audit_issues": [
                {
                    "kind": "freshness",
                    "severity": "major",
                    "issue": "China coal spot prices are stale relative to the target date.",
                    "affected_claim": "Qinhuangdao thermal coal benchmark",
                    "required_follow_up": "Refresh the benchmark before treating the memo as final.",
                }
            ],
            "quality_gates": [
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
            "report_readiness": "blocked",
            "readiness_note": (
                "Provisional with caveats: all required pillars are present, but some facts "
                "are stale or method-sensitive."
            ),
            "confidence": 0.13,
            "recommended_change": (
                "Close provisionally with explicit caveats rather than launching a fifth pass."
            ),
            "selected_reader_count": 4,
            "selected_reader_briefs": ["brief-1", "brief-2"],
            "depth_profile": kwargs["depth_profile"],
            "max_tool_rounds": kwargs["max_tool_rounds"],
            "max_tool_calls": kwargs["max_tool_calls"],
            "outputs": {"confidence": 0.13},
            "handoff_count": 3,
            "signal_count": 5,
            "error": None,
            "stage_records": [],
        }

    monkeypatch.setattr("dan.cli.research._build_live_provider", lambda *args, **kwargs: object())

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            ResearchConversationTurnDecision(
                action="research",
                public_response="Starting one bounded research run.",
                research_objective=final_pass_objective,
                delivery_target="research memo",
            ),
            durable_session,
        )

    async def _fake_review(self, *, session, objective, report_summary, context):
        calls["review"] += 1
        return (
            ResearchConversationReviewDecision(
                action="continue",
                public_response="One more bounded pass is still required.",
                next_objective=objective,
            ),
            session,
        )

    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.review_research_result",
        _fake_review,
    )
    monkeypatch.setattr("dan.cli.research.run_research_organism_live", _fake_runner)

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--max-supervision-loops",
            "2",
            "--workspace",
            str(tmp_path / "workspace"),
            "--workdir",
            str(tmp_path / "workdir"),
            "--json",
            "Investigate the discrepancy",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls["runner"] == 2
    assert calls["review"] == 2
    assert payload["status"] == "completed"
    assert payload["report_readiness"] == "provisional"
    assert payload["artifact_mode"] == "provisional_report"
    final_status_gates = [
        gate for gate in payload["quality_gates"]
        if gate.get("gate") == "final_status"
    ]
    assert final_status_gates
    assert final_status_gates[-1]["status"] == "warn"
    assert "explicitly final bounded research pass" in final_status_gates[-1]["summary"]


def test_session_claim_ledger_keeps_stronger_verified_fact_across_turns() -> None:
    session = ResearchCliSession(workspace_root="/workspace")

    session.record_turn(
        ResearchOrganismReport(
            status="completed",
            trace_id="trace-1",
            organism_id="reference-project-execution",
            organ_id="deep-research",
            task_id="deep-research-task:1",
            objective="Verify ETF status.",
            delivery_target="research memo",
            verification_facts=[
                {
                    "fact": "KOL ETF status",
                    "status": "verified",
                    "source": "sec.gov",
                    "note": "Primary filing confirms liquidation.",
                }
            ],
        )
    )
    session.record_turn(
        ResearchOrganismReport(
            status="completed",
            trace_id="trace-2",
            organism_id="reference-project-execution",
            organ_id="deep-research",
            task_id="deep-research-task:2",
            objective="Retry ETF status.",
            delivery_target="research memo",
            verification_facts=[
                {
                    "fact": "KOL ETF status",
                    "status": "unverified",
                    "source": "marketbeat.com",
                    "note": "Retail quote page still shows stale data.",
                }
            ],
        )
    )

    assert len(session.claim_ledger) == 1
    assert session.claim_ledger[0].fact == "KOL ETF status"
    assert session.claim_ledger[0].status == "verified"
    assert session.claim_ledger[0].source == "sec.gov"


def test_main_keeps_looping_until_review_is_done_by_default(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    calls = {"runner": 0, "review": 0}

    async def _fake_runner(*_args, **kwargs):
        calls["runner"] += 1
        return {
            "status": "completed",
            "trace_id": f"trace-research-{calls['runner']}",
            "organism_id": "reference-project-execution",
            "organ_id": "deep-research",
            "task_id": kwargs["task_id"],
            "objective": kwargs["objective"],
            "temporal_mode": "current",
            "temporal_anchor": "2026-04-16",
            "temporal_window": "",
            "temporal_guidance": "Temporal frame: current-as-of-runtime.",
            "delivery_target": kwargs["delivery_target"],
            "findings": [f"Grounded finding {calls['runner']}."],
            "evidence_summary": [f"repo evidence {calls['runner']}"],
            "evidence_refs": [f"repo:evidence:{calls['runner']}"],
            "contradictions": [],
            "open_questions": [],
            "verification_facts": [],
            "audit_issues": [],
            "quality_gates": _quality_gates(),
            "report_readiness": "grounded",
            "readiness_note": "Enough evidence for a bounded grounded note.",
            "confidence": 0.78,
            "recommended_change": "Verify the canonical source.",
            "selected_reader_count": 4,
            "selected_reader_briefs": ["brief-1", "brief-2"],
            "depth_profile": kwargs["depth_profile"],
            "max_tool_rounds": kwargs["max_tool_rounds"],
            "max_tool_calls": kwargs["max_tool_calls"],
            "outputs": {"confidence": 0.78},
            "handoff_count": 3,
            "signal_count": 5,
            "error": None,
            "stage_records": [],
        }

    monkeypatch.setattr("dan.cli.research._build_live_provider", lambda *args, **kwargs: object())

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            ResearchConversationTurnDecision(
                action="research",
                public_response="Starting one bounded research run.",
                research_objective=user_message,
                delivery_target="research memo",
            ),
            durable_session,
        )

    async def _fake_review(self, *, session, objective, report_summary, context):
        calls["review"] += 1
        action = "done" if calls["review"] >= 3 else "continue"
        return (
            ResearchConversationReviewDecision(
                action=action,
                public_response=f"Review decision {action}.",
                next_objective=objective,
            ),
            session,
        )

    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.review_research_result",
        _fake_review,
    )
    monkeypatch.setattr("dan.cli.research.run_research_organism_live", _fake_runner)

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--workspace",
            str(tmp_path / "workspace"),
            "--workdir",
            str(tmp_path / "workdir"),
            "--json",
            "Investigate the discrepancy",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls["runner"] == 3
    assert calls["review"] == 3
    assert payload["status"] == "completed"
    assert payload["trace_id"] == "trace-research-3"


def test_main_falls_back_when_initial_plan_stage_stalls(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    runner_calls: list[str] = []

    async def _fake_runner(*_args, **kwargs):
        runner_calls.append(kwargs["objective"])
        return {
            "status": "completed",
            "trace_id": "trace-research-fallback",
            "organism_id": "reference-project-execution",
            "organ_id": "deep-research",
            "task_id": kwargs["task_id"],
            "objective": kwargs["objective"],
            "temporal_mode": "current",
            "temporal_anchor": "2026-04-17",
            "temporal_window": "",
            "temporal_guidance": "Temporal frame: current-as-of-runtime.",
            "delivery_target": kwargs["delivery_target"],
            "findings": ["Fallback planning still produced a bounded report."],
            "evidence_summary": ["repo evidence"],
            "evidence_refs": ["repo:evidence"],
            "contradictions": [],
            "open_questions": [],
            "verification_facts": [],
            "audit_issues": [],
            "quality_gates": _quality_gates(),
            "report_readiness": "grounded",
            "readiness_note": "Enough evidence for a bounded grounded note.",
            "confidence": 0.78,
            "recommended_change": "Verify the canonical source.",
            "selected_reader_count": 2,
            "selected_reader_briefs": kwargs["reader_briefs"],
            "depth_profile": kwargs["depth_profile"],
            "max_tool_rounds": kwargs["max_tool_rounds"],
            "max_tool_calls": kwargs["max_tool_calls"],
            "outputs": {"confidence": 0.78},
            "handoff_count": 1,
            "signal_count": 2,
            "error": None,
            "stage_records": [],
        }

    monkeypatch.setenv("DAN_RESEARCH_CONTROL_STAGE_STALL_SECONDS", "0.01")
    monkeypatch.setenv("DAN_RESEARCH_CONTROL_STAGE_MAX_SECONDS", "0.03")
    monkeypatch.setattr("dan.cli.research._build_live_provider", lambda *args, **kwargs: object())

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            ResearchConversationTurnDecision(
                action="research",
                public_response="Starting one bounded research run.",
                research_objective=user_message,
                delivery_target="research memo",
            ),
            durable_session,
        )

    async def _slow_plan(
        self,
        *,
        session,
        objective,
        delivery_target,
        acceptance_criteria,
        context,
        planning_mode="initial",
        previous_report=None,
    ):
        _ = delivery_target, acceptance_criteria, context, planning_mode, previous_report
        await asyncio.sleep(0.05)
        return (
            ResearchConversationIntentionPlan(
                answer_goal=objective,
                refined_objective=f"{objective} (slow plan)",
                plan_summary="This plan should never win.",
                subproblems=[
                    ResearchConversationSubproblem(
                        problem_id="slow",
                        question="Slow question",
                    )
                ],
                workstreams=[
                    ResearchConversationWorkstream(
                        stream_id="slow",
                        title="Slow stream",
                        goal="Slow goal",
                        subproblem_ids=["slow"],
                    )
                ],
            ),
            session,
        )

    async def _fake_review(self, *, session, objective, report_summary, context):
        _ = objective, report_summary, context
        return (
            ResearchConversationReviewDecision(
                action="done",
                public_response="This bounded research pass is done.",
            ),
            session,
        )

    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.plan_research_intention",
        _slow_plan,
    )
    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.review_research_result",
        _fake_review,
    )
    monkeypatch.setattr("dan.cli.research.run_research_organism_live", _fake_runner)

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--workspace",
            str(tmp_path / "workspace"),
            "--json",
            "Investigate the discrepancy",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    assert len(runner_calls) == 1
    control_log = tmp_path / "workspace" / ".dan-research" / "control-plane-events.jsonl"
    rows = [
        json.loads(line)
        for line in control_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(row["event"] == "orchestrator.plan.fallback" for row in rows)


def test_main_resets_poisoned_orchestrator_session_after_plan_fallback(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    initial_session_id = {"value": None}
    review_session_ids: list[str] = []

    async def _fake_runner(*_args, **kwargs):
        return {
            "status": "completed",
            "trace_id": "trace-research-reset-session",
            "organism_id": "reference-project-execution",
            "organ_id": "deep-research",
            "task_id": kwargs["task_id"],
            "objective": kwargs["objective"],
            "temporal_mode": "current",
            "temporal_anchor": "2026-04-17",
            "temporal_window": "",
            "temporal_guidance": "Temporal frame: current-as-of-runtime.",
            "delivery_target": kwargs["delivery_target"],
            "findings": ["Grounded finding after reset."],
            "evidence_summary": ["repo evidence"],
            "evidence_refs": ["repo:evidence"],
            "contradictions": [],
            "open_questions": [],
            "verification_facts": [],
            "audit_issues": [],
            "quality_gates": _quality_gates(),
            "report_readiness": "grounded",
            "readiness_note": "Enough evidence for a bounded grounded note.",
            "confidence": 0.82,
            "recommended_change": "Verify the canonical source.",
            "selected_reader_count": 2,
            "selected_reader_briefs": kwargs["reader_briefs"],
            "depth_profile": kwargs["depth_profile"],
            "max_tool_rounds": kwargs["max_tool_rounds"],
            "max_tool_calls": kwargs["max_tool_calls"],
            "outputs": {"confidence": 0.82},
            "handoff_count": 1,
            "signal_count": 2,
            "error": None,
            "stage_records": [],
        }

    monkeypatch.setenv("DAN_RESEARCH_CONTROL_STAGE_STALL_SECONDS", "0.01")
    monkeypatch.setenv("DAN_RESEARCH_CONTROL_STAGE_MAX_SECONDS", "0.03")
    monkeypatch.setattr("dan.cli.research._build_live_provider", lambda *args, **kwargs: object())

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        initial_session_id["value"] = initial_session_id["value"] or durable_session.session_id
        return (
            ResearchConversationTurnDecision(
                action="research",
                public_response="Starting one bounded research run.",
                research_objective=user_message,
                delivery_target="research memo",
            ),
            durable_session,
        )

    async def _slow_plan(
        self,
        *,
        session,
        objective,
        delivery_target,
        acceptance_criteria,
        context,
        planning_mode="initial",
        previous_report=None,
    ):
        _ = (
            session,
            objective,
            delivery_target,
            acceptance_criteria,
            context,
            planning_mode,
            previous_report,
        )
        await asyncio.sleep(0.05)
        return (
            ResearchConversationIntentionPlan(
                answer_goal=objective,
                refined_objective=f"{objective} (slow plan)",
                plan_summary="This plan should never win.",
                subproblems=[
                    ResearchConversationSubproblem(
                        problem_id="slow",
                        question="Slow question",
                    )
                ],
                workstreams=[
                    ResearchConversationWorkstream(
                        stream_id="slow",
                        title="Slow stream",
                        goal="Slow goal",
                        subproblem_ids=["slow"],
                    )
                ],
            ),
            session,
        )

    async def _fake_review(self, *, session, objective, report_summary, context):
        _ = objective, report_summary, context
        assert session is not None
        review_session_ids.append(session.session_id)
        assert session.session_id != initial_session_id["value"]
        return (
            ResearchConversationReviewDecision(
                action="done",
                public_response="This bounded research pass is done.",
            ),
            session,
        )

    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.plan_research_intention",
        _slow_plan,
    )
    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.review_research_result",
        _fake_review,
    )
    monkeypatch.setattr("dan.cli.research.run_research_organism_live", _fake_runner)

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--workspace",
            str(tmp_path / "workspace"),
            "--json",
            "Investigate the discrepancy",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    assert review_session_ids
    control_log = tmp_path / "workspace" / ".dan-research" / "control-plane-events.jsonl"
    rows = [
        json.loads(line)
        for line in control_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(row["event"] == "orchestrator.plan.session.reset" for row in rows)


def test_main_uses_deterministic_continuation_plan_without_replanning(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    runner_objectives: list[str] = []
    review_calls = {"count": 0}
    plan_calls = {"count": 0}

    async def _fake_runner(*_args, **kwargs):
        runner_objectives.append(kwargs["objective"])
        trace_id = f"trace-research-{len(runner_objectives)}"
        return {
            "status": "completed",
            "trace_id": trace_id,
            "organism_id": "reference-project-execution",
            "organ_id": "deep-research",
            "task_id": kwargs["task_id"],
            "objective": kwargs["objective"],
            "temporal_mode": "current",
            "temporal_anchor": "2026-04-17",
            "temporal_window": "",
            "temporal_guidance": "Temporal frame: current-as-of-runtime.",
            "delivery_target": kwargs["delivery_target"],
            "findings": [f"Grounded finding {len(runner_objectives)}."],
            "evidence_summary": [f"repo evidence {len(runner_objectives)}"],
            "evidence_refs": [f"repo:evidence:{len(runner_objectives)}"],
            "contradictions": [],
            "open_questions": [],
            "verification_facts": [],
            "audit_issues": [],
            "quality_gates": _quality_gates(),
            "report_readiness": "grounded",
            "readiness_note": "Enough evidence for a bounded grounded note.",
            "confidence": 0.78,
            "recommended_change": "Verify the canonical source.",
            "selected_reader_count": 2,
            "selected_reader_briefs": kwargs["reader_briefs"],
            "depth_profile": kwargs["depth_profile"],
            "max_tool_rounds": kwargs["max_tool_rounds"],
            "max_tool_calls": kwargs["max_tool_calls"],
            "outputs": {"confidence": 0.78},
            "handoff_count": 1,
            "signal_count": 2,
            "error": None,
            "stage_records": [],
        }

    monkeypatch.setattr("dan.cli.research._build_live_provider", lambda *args, **kwargs: object())

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            ResearchConversationTurnDecision(
                action="research",
                public_response="Starting one bounded research run.",
                research_objective=user_message,
                delivery_target="research memo",
            ),
            durable_session,
        )

    async def _fake_plan(
        self,
        *,
        session,
        objective,
        delivery_target,
        acceptance_criteria,
        context,
        planning_mode="initial",
        previous_report=None,
    ):
        _ = delivery_target, acceptance_criteria, context, previous_report
        plan_calls["count"] += 1
        if planning_mode == "initial":
            return (
                ResearchConversationIntentionPlan(
                    answer_goal=objective,
                    refined_objective=objective,
                    plan_summary="Initial plan.",
                    subproblems=[
                        ResearchConversationSubproblem(
                            problem_id="initial",
                            question="Initial question",
                        )
                    ],
                    workstreams=[
                        ResearchConversationWorkstream(
                            stream_id="initial",
                            title="Initial stream",
                            goal="Initial goal",
                            subproblem_ids=["initial"],
                        )
                    ],
                ),
                session,
            )
        raise AssertionError("continuation planning should now be deterministic")

    async def _fake_review(self, *, session, objective, report_summary, context):
        _ = objective, report_summary, context
        review_calls["count"] += 1
        action = "done" if review_calls["count"] >= 2 else "continue"
        return (
            ResearchConversationReviewDecision(
                action=action,
                public_response=f"Review decision {action}.",
                next_objective="Follow-up objective",
            ),
            session,
        )

    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.plan_research_intention",
        _fake_plan,
    )
    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.review_research_result",
        _fake_review,
    )
    monkeypatch.setattr("dan.cli.research.run_research_organism_live", _fake_runner)

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--workspace",
            str(tmp_path / "workspace"),
            "--json",
            "Investigate the discrepancy",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed"
    assert plan_calls["count"] == 1
    assert runner_objectives == [
        "Investigate the discrepancy",
        "Follow-up objective",
    ]
    control_log = tmp_path / "workspace" / ".dan-research" / "control-plane-events.jsonl"
    rows = [
        json.loads(line)
        for line in control_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(
        row["event"] == "orchestrator.plan.completed"
        and row.get("strategy") == "deterministic_follow_up"
        for row in rows
    )


def test_main_without_objective_enters_interactive_loop(tmp_path, monkeypatch) -> None:
    calls: dict[str, object] = {}

    monkeypatch.setattr("dan.cli.research._build_live_provider", lambda *args, **kwargs: object())

    def _fake_loop(
        *,
        args,
        controller,
        llm_provider,
        workspace_root,
        model,
        thinking_mode,
        session,
        product_paths,
        tool_ids,
        acceptance_criteria,
        delivery_target,
        hard_constraints,
        soft_constraints,
        evidence_summaries,
        reader_briefs,
        research_reader_count,
        max_supervision_loops,
        depth_profile,
        max_tool_rounds,
        max_tool_calls,
        max_runtime_seconds,
        persist_session,
        run_root,
        progress_renderer,
        control_logger,
    ):
        calls["workspace_root"] = workspace_root
        calls["model"] = model
        calls["session_id"] = session.session_id
        calls["product_paths"] = product_paths
        calls["tool_ids"] = tool_ids
        calls["delivery_target"] = delivery_target
        calls["research_reader_count"] = research_reader_count
        calls["max_supervision_loops"] = max_supervision_loops
        calls["depth_profile"] = depth_profile
        calls["max_tool_rounds"] = max_tool_rounds
        calls["max_tool_calls"] = max_tool_calls
        calls["max_runtime_seconds"] = max_runtime_seconds
        calls["persist_session"] = persist_session
        calls["run_root"] = run_root
        calls["control_logger_path"] = control_logger.path
        return 0

    monkeypatch.setattr("dan.cli.research._interactive_loop", _fake_loop)

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--workspace",
            str(tmp_path / "workspace"),
        ]
    )

    assert exit_code == 0
    assert Path(calls["workspace_root"]) == (tmp_path / "workspace").resolve()
    assert calls["model"] == "gpt-test"
    assert Path(calls["product_paths"].root) == (
        tmp_path / "workspace" / ".dan-research"
    ).resolve()
    assert calls["tool_ids"] == [
        "web_search",
        "file_read",
        "list_directory",
    ]
    assert calls["delivery_target"] == "research memo"
    assert calls["research_reader_count"] is None
    assert calls["max_supervision_loops"] == DEFAULT_RESEARCH_MAX_SUPERVISION_LOOPS
    assert calls["depth_profile"] == "standard"
    assert calls["max_tool_rounds"] == 8
    assert calls["max_tool_calls"] == 24
    assert calls["persist_session"] is True
    assert Path(calls["run_root"]) == (
        tmp_path / "workspace" / ".dan-research" / "runs"
    ).resolve()
    assert Path(calls["control_logger_path"]) == (
        tmp_path / "workspace" / ".dan-research" / "control-plane-events.jsonl"
    ).resolve()


def test_load_research_product_session_ignores_empty_file(tmp_path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    paths = resolve_research_product_paths(workspace)
    Path(paths.root).mkdir(parents=True, exist_ok=True)
    Path(paths.session).write_text("", encoding="utf-8")

    loaded = load_research_product_session(paths)

    assert loaded is None


def test_main_show_config_recovers_from_empty_session_file(tmp_path, capsys) -> None:
    workspace = (tmp_path / "workspace").resolve()
    paths = resolve_research_product_paths(workspace)
    Path(paths.root).mkdir(parents=True, exist_ok=True)
    Path(paths.session).write_text("", encoding="utf-8")

    exit_code = main(
        [
            "--workspace",
            str(workspace),
            "--show-config",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace_root"] == str(workspace)
    assert payload["saved_turns"] == 0
    assert payload["session_exists"] is True


def test_main_uses_env_model_when_flag_is_omitted(tmp_path, capsys, monkeypatch) -> None:
    provider_calls: list[str] = []

    monkeypatch.setenv("DAN_MODEL", "kimi-test")
    monkeypatch.setattr(
        "dan.cli.research._build_live_provider",
        lambda model, **kwargs: provider_calls.append(str(model)) or object(),
    )

    async def _unexpected_runner(*args, **kwargs):
        raise AssertionError("research runner should not be called for a conversational greeting")

    monkeypatch.setattr("dan.cli.research.run_research_organism_live", _unexpected_runner)

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            ResearchConversationTurnDecision(
                action="respond",
                public_response="I can launch bounded research when you give me a concrete investigation task.",
            ),
            durable_session,
        )

    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.decide_user_turn",
        _fake_decide,
    )

    exit_code = main(
        [
            "--workspace",
            str(tmp_path / "workspace"),
            "hi",
        ]
    )

    assert exit_code == 0
    assert provider_calls == ["kimi-test"]
    stdout = capsys.readouterr().out
    assert "I can launch bounded research" in stdout


def test_main_persists_orchestrator_report_reply_artifact(tmp_path, capsys, monkeypatch) -> None:
    workspace = (tmp_path / "workspace").resolve()
    monkeypatch.setattr(
        "dan.cli.research._build_live_provider",
        lambda *args, **kwargs: object(),
    )

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            ResearchConversationTurnDecision(
                action="respond",
                response_kind="report_reply",
                public_response="## Final Report\n\nDirectional memo.",
                artifact_title="Final Report",
            ),
            durable_session,
        )

    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.decide_user_turn",
        _fake_decide,
    )

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--workspace",
            str(workspace),
            "--json",
            "please continue to the final report",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["response_kind"] == "report_reply"
    assert payload["artifact_path"].endswith(".dan-research/runs/reply-01/report.md")
    artifact_path = Path(payload["artifact_path"])
    assert artifact_path.exists()
    assert artifact_path.read_text(encoding="utf-8") == "## Final Report\n\nDirectional memo."

    session = load_research_product_session(resolve_research_product_paths(workspace))
    assert session is not None
    latest = dict(session.orchestrator_state.get("latest_response_artifact") or {})
    assert latest["path"] == str(artifact_path)
    assert latest["kind"] == "report_reply"
    assert session.turns == []


def test_show_config_reports_depth_and_reader_mode(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "--workspace",
            str(tmp_path / "workspace"),
            "--show-config",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["product_name"] == "DAN Research"
    assert payload["depth_profile"] == "standard"
    assert payload["research_reader_count"] is None
    assert payload["control_log_path"].endswith(".dan-research/control-plane-events.jsonl")


def test_load_or_create_session_refreshes_runtime_state(tmp_path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    paths = resolve_research_product_paths(workspace)
    session = ResearchCliSession(workspace_root=str(workspace))
    session.runtime_build_id = "stale-build"
    session.pending_clarification = "Which source should I verify?"
    Path(paths.root).mkdir(parents=True, exist_ok=True)
    Path(paths.session).write_text(session.model_dump_json(indent=2), encoding="utf-8")

    loaded_exit = main(
        [
            "--workspace",
            str(workspace),
            "--show-config",
            "--json",
        ]
    )

    assert loaded_exit == 0
    refreshed = json.loads(Path(paths.session).read_text(encoding="utf-8"))
    assert refreshed["runtime_build_id"] != "stale-build"
    assert refreshed["pending_clarification"] is None


def test_main_persists_control_plane_event_log(tmp_path, capsys, monkeypatch) -> None:
    async def _fake_runner(*_args, **kwargs):
        return {
            "status": "completed",
            "trace_id": "trace-research",
            "organism_id": "reference-project-execution",
            "organ_id": "deep-research",
            "task_id": "deep-research-task:1",
            "objective": kwargs["objective"],
            "delivery_target": kwargs["delivery_target"],
            "findings": ["Grounded finding."],
            "evidence_summary": ["repo evidence"],
            "evidence_refs": ["repo:evidence"],
            "contradictions": [],
            "open_questions": [],
            "verification_facts": [],
            "audit_issues": [],
            "quality_gates": _quality_gates(),
            "report_readiness": "grounded",
            "readiness_note": "Enough evidence for a bounded grounded report.",
            "confidence": 0.78,
            "recommended_change": "Verify the canonical source.",
            "selected_reader_count": 2,
            "selected_reader_briefs": ["brief-1", "brief-2"],
            "depth_profile": kwargs["depth_profile"],
            "max_tool_rounds": kwargs["max_tool_rounds"],
            "max_tool_calls": kwargs["max_tool_calls"],
            "outputs": {"confidence": 0.78},
            "handoff_count": 3,
            "signal_count": 5,
            "error": None,
            "stage_records": [],
        }

    monkeypatch.setattr("dan.cli.research._build_live_provider", lambda *args, **kwargs: object())

    async def _fake_decide(self, *, session, user_message, pending_clarification, context):
        durable_session = session or self.create_session(metadata={"surface": "test"})
        return (
            ResearchConversationTurnDecision(
                action="research",
                public_response="Starting one bounded research run.",
                research_objective=user_message,
                delivery_target="research memo",
            ),
            durable_session,
        )

    async def _fake_review(self, *, session, objective, report_summary, context):
        return (
            ResearchConversationReviewDecision(
                action="done",
                public_response="This bounded research pass is done.",
            ),
            session,
        )

    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.decide_user_turn",
        _fake_decide,
    )
    monkeypatch.setattr(
        "dan.cli.research.ResearchConversationController.review_research_result",
        _fake_review,
    )
    monkeypatch.setattr("dan.cli.research.run_research_organism_live", _fake_runner)

    exit_code = main(
        [
            "--model",
            "gpt-test",
            "--workspace",
            str(tmp_path / "workspace"),
            "--json",
            "Investigate the discrepancy",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["control_log_path"].endswith(".dan-research/control-plane-events.jsonl")
    assert payload["control_log_schema"] == ORGANISM_LOG_SCHEMA_VERSION
    assert payload["markdown_report_path"].endswith(".dan-research/runs/turn-01/report.md")
    control_log = tmp_path / "workspace" / ".dan-research" / "control-plane-events.jsonl"
    rows = [
        json.loads(line)
        for line in control_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    assert all("timestamp" in row for row in rows)
    assert all("sequence" in row for row in rows)
    assert all(row["schema"] == ORGANISM_LOG_SCHEMA_VERSION for row in rows)
    assert any(row["event"] == "cli.started" for row in rows)
    assert any(row["event"] == "orchestrator.turn.decision" for row in rows)
    assert any(row["event"] == "session.persisted" for row in rows)
    assert any(row["event"] == "cli.completed" for row in rows)
    markdown_path = tmp_path / "workspace" / ".dan-research" / "runs" / "turn-01" / "report.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert markdown.startswith("# Research Report")
    assert "## Recommendation" in markdown
    assert "Verify the canonical source." in markdown
    assert "## Quality Gates" not in markdown
