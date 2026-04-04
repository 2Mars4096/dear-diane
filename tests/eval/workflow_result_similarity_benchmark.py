"""Live benchmark comparing reference workflows against DAN-generated workflows.

Usage:
    PYTHONPATH=src:. python -m tests.eval.workflow_result_similarity_benchmark
    PYTHONPATH=src:. python -m tests.eval.workflow_result_similarity_benchmark --case equity_daily_brief
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dan.builder import workflow
from dan.engine import Engine
from dan.engine.checkpoint import NullCheckpointStore
from dan.models.graph import Graph
from dan.server.runtime_config import build_engine_config_from_env
from dan.validation.graph import validate_graph
from tests.eval import RESULTS_DIR
from tests.eval.client import DanClient

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]+")
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "their",
    "were", "have", "has", "had", "will", "would", "about", "after", "before",
    "while", "under", "over", "into", "onto", "than", "then", "them", "they",
    "been", "being", "also", "only", "use", "used", "using", "must", "should",
    "could", "through", "there", "here", "each", "more", "less", "very", "make",
    "made", "when", "what", "where", "which", "whose", "because", "these", "those",
    "input", "output", "final", "section", "sections", "workflow",
}


def _parse_env_assignment(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export "):].lstrip()
    if "=" not in stripped:
        return None
    key, raw = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    value = raw.strip()
    if not value:
        return key, ""
    if value[0] in {"'", '"'} and value[-1] == value[0]:
        lexer = shlex.shlex(value, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
        return key, " ".join(tokens)
    return key, value


def _load_env_file(path: Path, *, override: bool = False) -> list[str]:
    loaded: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_assignment(line)
        if parsed is None:
            continue
        key, value = parsed
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded


@dataclass(frozen=True)
class SimilarityFixture:
    fixture_id: str
    title: str
    objective: str
    final_sections: tuple[str, ...]
    sample_input: str

    def generation_prompt(self) -> str:
        headings = "\n".join(f"- {section}" for section in self.final_sections)
        return (
            f"Build a dedicated, multi-step workflow for {self.title}. "
            f"{self.objective} "
            "The workflow must accept exactly one text input on port `input`, operate only on that "
            "provided text, and avoid web access, file paths, human approval steps, or external tools. "
            "Use at least 5 meaningful domain-specific nodes for intake, fact extraction, analysis, "
            "critique, revision, and finalization. "
            "The final node must produce a markdown memo using exactly these H2 section headings in order:\n"
            f"{headings}\n"
            "Keep the workflow runnable with just the provided input payload."
        )


def _comparison_fixtures() -> list[SimilarityFixture]:
    return [
        SimilarityFixture(
            fixture_id="equity_daily_brief",
            title="a daily equity research briefing",
            objective=(
                "Turn analyst notes into a balanced market-ready briefing with explicit upside, downside, "
                "catalysts, and monitoring points."
            ),
            final_sections=(
                "Executive Summary",
                "Bull Case",
                "Bear Case",
                "Catalysts",
                "Watch Items",
                "Open Questions",
            ),
            sample_input=(
                "Morning watchlist notes:\n"
                "- ALFA: Q1 revenue up 18% year over year, but gross margin fell from 42% to 37%. "
                "Management says margin pressure came from expedited shipping and launch discounts. "
                "A new enterprise contract in Germany starts next quarter.\n"
                "- BRIO: Shares sold off 9% after a short report questioned customer retention. "
                "Company said churn stayed below 4%, but did not provide cohort data. "
                "CFO bought shares yesterday.\n"
                "- CYON: Semiconductor supplier. Lead times improving from 22 weeks to 14 weeks. "
                "Auto segment demand still weak; data center orders accelerated. "
                "Street expects a margin rebound in the second half.\n"
                "Desk context: portfolio manager wants concise positioning guidance, explicit risks, and "
                "what to watch over the next two weeks."
            ),
        ),
        SimilarityFixture(
            fixture_id="incident_postmortem",
            title="an incident postmortem summary",
            objective=(
                "Convert incident notes into an operational postmortem that separates facts, impact, "
                "root causes, and corrective actions."
            ),
            final_sections=(
                "Incident Summary",
                "Timeline",
                "Root Causes",
                "Customer Impact",
                "Immediate Fixes",
                "Follow-up Actions",
            ),
            sample_input=(
                "Incident notes:\n"
                "08:14 UTC deploy started for checkout-service version 4.12.\n"
                "08:21 alert: payment authorization latency above 2.5 seconds.\n"
                "08:24 error rate rose from 0.3% to 14% on EU traffic.\n"
                "08:30 on-call noticed new Redis connection pool setting reduced max clients.\n"
                "08:34 rollback initiated.\n"
                "08:42 latency recovered; error rate back below 1% by 08:47.\n"
                "Impact: approximately 3,800 failed payment attempts; support volume spiked; no data loss.\n"
                "Contributing factors: missing canary for EU routing path, no alert on Redis saturation, "
                "runbook did not mention connection pool regression checks.\n"
                "Leadership asks for a factual write-up, not blame, plus near-term and medium-term actions."
            ),
        ),
        SimilarityFixture(
            fixture_id="product_launch_brief",
            title="a product launch brief",
            objective=(
                "Transform messy launch planning notes into a decision-ready brief for product, marketing, "
                "and sales leads."
            ),
            final_sections=(
                "Target Users",
                "Value Proposition",
                "Launch Plan",
                "Risks",
                "Success Metrics",
                "Decisions Needed",
            ),
            sample_input=(
                "Launch planning notes for 'Ops Copilot':\n"
                "Audience: mid-market operations managers and RevOps leads at B2B SaaS companies.\n"
                "Core value: summarize weekly KPI drift, surface anomalies, draft action plans, and answer "
                "questions over uploaded reports.\n"
                "Constraints: beta only supports CSV and pasted notes; no direct warehouse connector yet.\n"
                "Marketing wants customer stories, but only two pilots are willing to be named. "
                "Sales wants pricing guidance before launch. PM wants to position this as workflow automation, "
                "not just analytics. Leadership wants 20 design-partner conversions within one quarter.\n"
                "Risks mentioned: users may expect live dashboards, support load could spike, and onboarding "
                "still takes 45 minutes with manual setup."
            ),
        ),
        SimilarityFixture(
            fixture_id="vendor_risk_review",
            title="a vendor risk assessment",
            objective=(
                "Turn procurement and security notes into a structured vendor recommendation with concrete "
                "gaps and mitigations."
            ),
            final_sections=(
                "Vendor Context",
                "Key Risks",
                "Control Gaps",
                "Mitigations",
                "Recommendation",
                "Follow-up Requests",
            ),
            sample_input=(
                "Vendor review: Northstar Analytics provides AI meeting transcription and search.\n"
                "They host in US-East only, retain raw audio for 30 days by default, and support SSO plus "
                "SCIM provisioning. SOC 2 Type II is current, but no ISO 27001. DPA available. "
                "No customer-managed keys. Admin audit logs export only through support. "
                "Security questionnaire shows annual penetration tests and quarterly access reviews.\n"
                "Business team wants launch in six weeks for customer success and recruiting. "
                "Legal is concerned about cross-border processing for EU candidate interviews. "
                "Security team wants a recommendation with blocking vs non-blocking issues."
            ),
        ),
        SimilarityFixture(
            fixture_id="feedback_synthesis",
            title="a customer feedback synthesis",
            objective=(
                "Condense fragmented customer feedback into a prioritized product signal memo."
            ),
            final_sections=(
                "Common Themes",
                "Pain Points",
                "Requested Features",
                "Positive Signals",
                "Priorities",
                "Recommended Actions",
            ),
            sample_input=(
                "Customer feedback bundle:\n"
                "1. Three enterprise customers said dashboard load time is too slow for executive reviews.\n"
                "2. Five SMB users praised the new onboarding checklist and said first-value was much faster.\n"
                "3. Multiple requests for scheduled exports to Slack and email.\n"
                "4. Two customers said role-based permissions are still too coarse for finance teams.\n"
                "5. One champion said anomaly explanations are clearer now, but action recommendations still feel generic.\n"
                "6. Churn interview: buyer loved the insight quality but could not justify manual data upload every week.\n"
                "PM asks for a synthesis that separates signal from noise and recommends what to do next quarter."
            ),
        ),
        SimilarityFixture(
            fixture_id="literature_review_digest",
            title="a literature review digest",
            objective=(
                "Transform a mixed set of research notes into a balanced review that highlights consensus, "
                "conflicts, and remaining gaps."
            ),
            final_sections=(
                "Scope",
                "Consensus Findings",
                "Disagreements",
                "Evidence Gaps",
                "Practical Implications",
                "Next Research Steps",
            ),
            sample_input=(
                "Research notebook on AI code review assistants:\n"
                "- Paper A: large productivity gains on routine bug-fix tasks, weaker gains on architectural refactors.\n"
                "- Paper B: reviewers accepted more low-severity suggestions but saw no change in severe defect detection.\n"
                "- Paper C: novice developers improved more than senior developers.\n"
                "- Field report D: teams using mandatory AI-review summaries saw faster reviews but more superficial comments.\n"
                "- Survey E: trust improved when tools cited evidence and uncertainty explicitly.\n"
                "- Limitation across studies: short evaluation windows, mostly synthetic tasks, little evidence on long-lived codebases.\n"
                "Goal: prepare a digest for an engineering leadership reading group."
            ),
        ),
        SimilarityFixture(
            fixture_id="operations_weekly_brief",
            title="a weekly operations briefing",
            objective=(
                "Convert scattered operational updates into a concise briefing for a cross-functional staff meeting."
            ),
            final_sections=(
                "Highlights",
                "Incidents",
                "Bottlenecks",
                "Metrics to Watch",
                "Staffing Notes",
                "Next Actions",
            ),
            sample_input=(
                "Ops weekly updates:\n"
                "- Fulfillment backlog down from 1,420 to 980 orders after weekend overtime.\n"
                "- One warehouse scanner outage caused a 90-minute slowdown on Tuesday.\n"
                "- Return processing is still averaging 5.8 days vs 3-day target because two lanes are understaffed.\n"
                "- On-time shipment improved from 91.2% to 94.6%, but premium shipping cost per order increased 12%.\n"
                "- New supervisor in Phoenix started Monday; two open coordinator roles remain in Atlanta.\n"
                "- Finance wants a view on whether overtime should continue next week.\n"
                "Prepare a briefing for operations, finance, and customer support leaders."
            ),
        ),
        SimilarityFixture(
            fixture_id="policy_compliance_assessment",
            title="a policy compliance assessment",
            objective=(
                "Turn policy and implementation notes into a compliance gap review with severity and remediation."
            ),
            final_sections=(
                "Requirements",
                "Compliant Areas",
                "Gaps",
                "Severity Assessment",
                "Remediation Plan",
                "Evidence Needed",
            ),
            sample_input=(
                "Policy review notes for data retention:\n"
                "Policy requires customer support transcripts deleted after 18 months, access logs retained for 24 months, "
                "and legal hold records exempt from auto-delete. Current implementation deletes transcripts after 24 months, "
                "keeps access logs for 24 months, and has a manual spreadsheet for legal holds. "
                "Engineering says transcript retention can be changed in one config migration. "
                "Legal says the spreadsheet is risky because it is not tied to enforcement. "
                "Audit wants a severity call and a remediation sequence before the next quarterly review."
            ),
        ),
        SimilarityFixture(
            fixture_id="meeting_to_project_plan",
            title="a project plan from meeting notes",
            objective=(
                "Turn a dense meeting transcript summary into an executable project plan with owners and sequencing."
            ),
            final_sections=(
                "Objectives",
                "Decisions Made",
                "Owners",
                "Timeline",
                "Risks",
                "Immediate Next Steps",
            ),
            sample_input=(
                "Meeting notes: The team agreed to migrate billing notifications to the new event bus before the July renewal cycle. "
                "Platform owns the event publisher, Growth owns template refresh, and Support needs updated macros. "
                "The group chose a phased rollout: internal dry run next week, 10% traffic in two weeks, full cutover by month end. "
                "Open concerns: duplicate sends during rollback, missing analytics on delivery failures, and no final copy approval from legal. "
                "Leadership wants an actionable plan by tomorrow morning."
            ),
        ),
        SimilarityFixture(
            fixture_id="hiring_interview_packet",
            title="an interview packet for a hiring team",
            objective=(
                "Convert hiring-manager notes into a structured interview packet with criteria, stages, and risks."
            ),
            final_sections=(
                "Role Summary",
                "Must-Haves",
                "Interview Stages",
                "Scorecard",
                "Red Flags",
                "Candidate Questions",
            ),
            sample_input=(
                "Hiring brief for Senior Analytics Engineer:\n"
                "Need someone who can own dbt models, data quality monitoring, and stakeholder-facing metric definitions. "
                "Manager emphasizes strong SQL, clear written communication, and comfort with ambiguous business requests. "
                "Nice-to-have: Python, experiment analysis, and mentoring junior analysts. "
                "Pain from past hires: great coders who could not align definitions with finance and product. "
                "Interview process should screen for technical depth, business judgment, and collaboration. "
                "Need a packet the panel can use this week."
            ),
        ),
    ]


def _default_model() -> str:
    config = build_engine_config_from_env()
    return str(config.llm_default_model or "").strip()


def _local_engine() -> Engine:
    config = build_engine_config_from_env()
    config.checkpoint_enabled = False
    config.memory_enabled = False
    config.memory_pipeline_enabled = False
    config.cache_enabled = False
    config.state_store_enabled = False
    return Engine(
        config=config,
        checkpoint_store=NullCheckpointStore(),
    )


def _build_reference_graph(fixture: SimilarityFixture, *, model: str) -> Graph:
    wf = workflow(
        f"reference_{fixture.fixture_id}",
        description=f"Reference workflow for {fixture.title}",
        canonical_workers=True,
    )

    section_text = "\n".join(f"## {section}" for section in fixture.final_sections)
    model_arg = {"model": model} if model else {}

    intake = wf.llm(
        f"{fixture.fixture_id}_intake",
        prompt=(
            "Read the provided input carefully. Restate the core situation, stakeholders, constraints, "
            "important evidence, and any explicit asks. Stay concrete and do not invent facts.\n\n{input}"
        ),
        **model_arg,
    )
    analysis = wf.llm(
        f"{fixture.fixture_id}_analysis",
        prompt=(
            f"Using this intake memo for {fixture.title}, create a compact fact base first, then analyze the "
            f"problem. Weigh tradeoffs, surface the most important considerations, call out missing information, "
            f"and prioritize what matters most.\n\n{intake}"
        ),
        **model_arg,
    )
    final = wf.llm(
        f"{fixture.fixture_id}_final",
        prompt=(
            f"Write the final deliverable for {fixture.title} as markdown. Use exactly these H2 section "
            f"headings in this order:\n{section_text}\n"
            "Under each section, write concise but specific bullets or short paragraphs grounded only in the "
            "input and the analysis. Tighten claims, preserve useful specifics, and include risks or open questions "
            "where appropriate. Do not add headings beyond the required ones.\n\n"
            f"{analysis}"
        ),
        **model_arg,
    )

    intake >> analysis >> final
    graph = wf.build()
    fatal = [
        error
        for error in validate_graph(graph)
        if "warning" not in error.lower()
        and "schema safety bypassed" not in error.lower()
    ]
    if fatal:
        raise ValueError(f"Reference graph for {fixture.fixture_id} is invalid: {fatal}")
    return graph


def _graph_summary(graph_data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(graph_data, dict):
        return {"node_count": 0, "edge_count": 0, "node_types": []}
    nodes = list(graph_data.get("nodes") or [])
    edges = list(graph_data.get("edges") or [])
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "node_types": [str(node.get("node_type") or "") for node in nodes if isinstance(node, dict)],
    }


def _extract_primary_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "result", "report", "output", "final"):
            if key in value:
                return _extract_primary_output(value[key])
        if len(value) == 1:
            return _extract_primary_output(next(iter(value.values())))
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def _normalize_words(text: str) -> set[str]:
    words: set[str] = set()
    for raw in _WORD_RE.findall(str(text or "").lower()):
        if len(raw) < 4 or raw in _STOPWORDS:
            continue
        words.add(raw)
    return words


def _keyword_overlap(reference_text: str, candidate_text: str) -> float:
    reference_words = _normalize_words(reference_text)
    candidate_words = _normalize_words(candidate_text)
    if not reference_words or not candidate_words:
        return 0.0
    union = reference_words | candidate_words
    if not union:
        return 0.0
    return len(reference_words & candidate_words) / len(union)


def _section_coverage(text: str, sections: tuple[str, ...]) -> float:
    normalized = str(text or "").lower()
    if not sections:
        return 1.0
    hits = 0
    for section in sections:
        lowered = section.lower()
        if f"## {lowered}" in normalized or lowered in normalized:
            hits += 1
    return hits / len(sections)


def _truncate(text: str, limit: int = 4000) -> str:
    normalized = str(text or "")
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 20] + "\n...[truncated]..."


async def _consume_stream(client: DanClient, channel_id: str, *, timeout: float = 180.0) -> tuple[str, list[dict[str, Any]], str | None]:
    response_text = ""
    events: list[dict[str, Any]] = []
    error: str | None = None
    async for event in client.stream_events(channel_id, timeout=timeout):
        events.append(event)
        event_type = str(event.get("type") or "")
        if event_type in {"chat_text", "chat_token"}:
            response_text += str(event.get("text", event.get("token", "")) or "")
        elif event_type == "chat_complete":
            content = str(event.get("content") or "")
            if content and not response_text:
                response_text = content
            if event.get("detected_mode") != "progress_ack":
                break
        elif event_type == "chat_error":
            error = str(event.get("error", event.get("content", "stream error")) or "stream error")
            break
    return response_text, events, error


async def _wait_for_run(
    client: DanClient,
    graph_id: str,
    *,
    inputs: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.time()
    run_resp = await client.start_run(graph_id, inputs=inputs)
    run_id = str(run_resp.get("run_id") or "")
    if not run_id:
        raise RuntimeError(f"Run start returned no run_id for graph {graph_id}")

    while True:
        snapshot = await client.get_run(run_id)
        status = str(snapshot.get("status") or snapshot.get("state") or "").lower()
        if status in {"completed", "done", "finished"}:
            return snapshot
        if status in {"failed", "error", "cancelled"}:
            return snapshot
        if (time.time() - started) > timeout_seconds:
            raise TimeoutError(f"Timed out waiting for run {run_id} on graph {graph_id}")
        await asyncio.sleep(2.0)


async def _run_graph_locally(
    graph: Graph,
    *,
    inputs: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    engine = _local_engine()
    result = await asyncio.wait_for(
        engine.run(graph, inputs=inputs, run_id=f"bench-{uuid4().hex[:8]}"),
        timeout=timeout_seconds,
    )
    return {
        "status": "completed" if result.success else "failed",
        "outputs": result.outputs,
        "errors": result.errors,
        "success": result.success,
    }


async def _create_generated_graph(
    client: DanClient,
    fixture: SimilarityFixture,
    *,
    graph_id: str,
    timeout_seconds: float,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    await client.create_graph(graph_id)
    response = await client.send_message(
        workflow_id=graph_id,
        message=fixture.generation_prompt(),
        mode="build",
        surface_context={"workflow_generation_contract_enabled": True},
    )
    channel_id = str(response.get("stream_channel_id") or "")
    response_text, events, stream_error = ("", [], None)
    if channel_id:
        response_text, events, stream_error = await _consume_stream(
            client,
            channel_id,
            timeout=timeout_seconds,
        )

    graph_wrapper = await client.get_graph(graph_id)
    graph_data = None
    if graph_wrapper is not None:
        graph_data = graph_wrapper.get("data", graph_wrapper)
        if not list(graph_data.get("nodes") or []):
            graph_data = None

    return graph_data, {
        "response_text": response_text,
        "stream_error": stream_error,
        "event_types": [str(event.get("type") or "") for event in events],
    }


def _parse_judge_scores(text: str) -> dict[str, Any] | None:
    match = _JSON_BLOCK_RE.search(str(text or ""))
    if match is None:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    required = {
        "task_completion",
        "factual_alignment",
        "structural_alignment",
        "overall_similarity",
    }
    if not required.issubset(payload.keys()):
        return None
    scores = {
        key: max(0, min(10, int(payload[key])))
        for key in required
    }
    scores["passed"] = bool(payload.get("passed", scores["overall_similarity"] >= 7))
    return scores


async def _judge_similarity(
    client: DanClient,
    fixture: SimilarityFixture,
    *,
    reference_output: str,
    candidate_output: str,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    judge_prompt = f"""You are evaluating whether two workflow outputs are meaningfully equivalent.

Task title: {fixture.title}
Task objective: {fixture.objective}
Required final sections:
{chr(10).join(f"- {section}" for section in fixture.final_sections)}

Shared input:
{_truncate(fixture.sample_input, 2500)}

Reference output:
{_truncate(reference_output, 5000)}

Candidate output:
{_truncate(candidate_output, 5000)}

Score each dimension from 0-10:
1. task_completion
2. factual_alignment
3. structural_alignment
4. overall_similarity

Set passed=true only if the candidate is good enough that a reviewer would treat it as substantially the same result, even if wording differs.

Respond with JSON only:
{{"task_completion": 0, "factual_alignment": 0, "structural_alignment": 0, "overall_similarity": 0, "passed": false}}
"""
    response = await client.send_message(
        workflow_id=f"__eval_similarity_judge__-{uuid4().hex[:8]}",
        message=judge_prompt,
        mode="ask",
    )
    channel_id = str(response.get("stream_channel_id") or "")
    if not channel_id:
        return None
    response_text, _, stream_error = await _consume_stream(
        client,
        channel_id,
        timeout=timeout_seconds,
    )
    if stream_error:
        return None
    return _parse_judge_scores(response_text)


async def _run_fixture(
    client: DanClient,
    fixture: SimilarityFixture,
    *,
    model: str,
    timeout_seconds: float,
    judge_mode: str,
    keep_graphs: bool,
) -> dict[str, Any]:
    reference_graph_id = f"eval-ref-{fixture.fixture_id}-{uuid4().hex[:8]}"
    generated_graph_id = f"eval-gen-{fixture.fixture_id}-{uuid4().hex[:8]}"
    report: dict[str, Any] = {
        "fixture_id": fixture.fixture_id,
        "title": fixture.title,
        "reference_graph_id": reference_graph_id,
        "generated_graph_id": generated_graph_id,
        "required_sections": list(fixture.final_sections),
        "reference": {},
        "generated": {},
        "deterministic_metrics": {},
        "judge_scores": None,
        "passed": False,
    }
    stage = "reference_build"

    try:
        reference_graph = _build_reference_graph(fixture, model=model)
        reference_validation = {
            "errors": [],
            "warnings": validate_graph(reference_graph),
            "run_ready": True,
        }
        report["reference"] = {
            "graph_summary": _graph_summary(reference_graph.model_dump(mode="json")),
            "validation": reference_validation,
        }
        stage = "reference_run"
        reference_snapshot = await _run_graph_locally(
            reference_graph,
            inputs={"input": fixture.sample_input},
            timeout_seconds=timeout_seconds,
        )
        reference_output = _extract_primary_output(reference_snapshot.get("outputs"))
        report["reference"].update({
            "run_status": reference_snapshot.get("status"),
            "outputs": reference_snapshot.get("outputs"),
            "primary_output": reference_output,
            "errors": reference_snapshot.get("errors"),
        })
        if str(reference_snapshot.get("status") or "").lower() not in {"completed", "done", "finished"}:
            report["reference"]["failure_reason"] = "reference_run_failed"
            return report

        stage = "generation"
        generated_graph_data, generation_meta = await _create_generated_graph(
            client,
            fixture,
            graph_id=generated_graph_id,
            timeout_seconds=timeout_seconds,
        )
        report["generated"]["generation"] = generation_meta
        if generated_graph_data is None:
            report["generated"]["failure_reason"] = "no_graph_created"
            return report

        generated_graph = Graph.model_validate(generated_graph_data)
        generated_validation = {
            "errors": [],
            "warnings": validate_graph(generated_graph),
            "run_ready": True,
        }
        report["generated"]["graph_summary"] = _graph_summary(generated_graph_data)
        report["generated"]["validation"] = generated_validation
        fatal_generated_errors = [
            error
            for error in generated_validation.get("warnings", [])
            if "warning" not in str(error).lower()
            and "schema safety bypassed" not in str(error).lower()
        ]
        if fatal_generated_errors:
            report["generated"]["failure_reason"] = "validation_failed"
            report["generated"]["validation_errors"] = fatal_generated_errors
            return report

        stage = "generated_run"
        generated_snapshot = await _run_graph_locally(
            generated_graph,
            inputs={"input": fixture.sample_input},
            timeout_seconds=timeout_seconds,
        )
        generated_output = _extract_primary_output(generated_snapshot.get("outputs"))
        report["generated"].update({
            "run_status": generated_snapshot.get("status"),
            "outputs": generated_snapshot.get("outputs"),
            "primary_output": generated_output,
            "errors": generated_snapshot.get("errors"),
        })
        if str(generated_snapshot.get("status") or "").lower() not in {"completed", "done", "finished"}:
            report["generated"]["failure_reason"] = "generated_run_failed"
            return report

        deterministic_metrics = {
            "reference_section_coverage": _section_coverage(reference_output, fixture.final_sections),
            "candidate_section_coverage": _section_coverage(generated_output, fixture.final_sections),
            "keyword_overlap": _keyword_overlap(reference_output, generated_output),
        }
        report["deterministic_metrics"] = deterministic_metrics

        judge_scores = None
        if judge_mode != "off":
            stage = "judge"
            judge_scores = await _judge_similarity(
                client,
                fixture,
                reference_output=reference_output,
                candidate_output=generated_output,
                timeout_seconds=timeout_seconds,
            )
            if judge_scores is None and judge_mode == "required":
                report["generated"]["failure_reason"] = "judge_unavailable"
                return report
        report["judge_scores"] = judge_scores

        if judge_scores is not None:
            report["passed"] = bool(
                judge_scores.get("passed")
                and judge_scores.get("overall_similarity", 0) >= 7
                and judge_scores.get("task_completion", 0) >= 7
                and judge_scores.get("structural_alignment", 0) >= 6
            )
        else:
            report["passed"] = bool(
                deterministic_metrics["candidate_section_coverage"] >= 0.8
                and deterministic_metrics["keyword_overlap"] >= 0.18
            )
        return report
    except TimeoutError as exc:
        report["error"] = str(exc) or repr(exc)
        report["error_type"] = type(exc).__name__
        report["failure_stage"] = stage
        if stage.startswith("reference"):
            report.setdefault("reference", {})["failure_reason"] = "timeout"
        elif stage.startswith("generated") or stage == "generation":
            report.setdefault("generated", {})["failure_reason"] = "timeout"
        else:
            report["failure_reason"] = "timeout"
        return report
    except Exception as exc:
        report["error"] = str(exc) or repr(exc)
        report["error_type"] = type(exc).__name__
        report["failure_stage"] = stage
        return report
    finally:
        if not keep_graphs:
            await client.delete_graph(reference_graph_id)
            await client.delete_graph(generated_graph_id)


def _report_path() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return RESULTS_DIR / f"{stamp}_workflow_result_similarity_benchmark.json"


def _summarize_report(cases: list[dict[str, Any]]) -> dict[str, Any]:
    passed_cases = [case for case in cases if case.get("passed")]
    judge_scores = [
        case["judge_scores"]["overall_similarity"]
        for case in cases
        if isinstance(case.get("judge_scores"), dict)
    ]
    return {
        "case_count": len(cases),
        "pass_count": len(passed_cases),
        "pass_rate": (len(passed_cases) / len(cases)) if cases else 0.0,
        "judge_case_count": len(judge_scores),
        "judge_overall_mean": (sum(judge_scores) / len(judge_scores)) if judge_scores else None,
        "generation_failures": [
            case["fixture_id"]
            for case in cases
            if case.get("generated", {}).get("failure_reason") == "no_graph_created"
        ],
        "validation_failures": [
            case["fixture_id"]
            for case in cases
            if case.get("generated", {}).get("failure_reason") == "validation_failed"
        ],
    }


async def _run_benchmark(
    *,
    base_url: str,
    selected_case_ids: set[str] | None,
    judge_mode: str,
    timeout_seconds: float,
    keep_graphs: bool,
    env_file: str | None,
) -> dict[str, Any]:
    env_path: Path | None = None
    if env_file:
        env_path = Path(env_file)
    else:
        default_env = Path(".env")
        if default_env.exists():
            env_path = default_env
    loaded_env_keys: list[str] = []
    if env_path is not None and env_path.exists():
        loaded_env_keys = _load_env_file(env_path)

    fixtures = _comparison_fixtures()
    if selected_case_ids:
        fixtures = [fixture for fixture in fixtures if fixture.fixture_id in selected_case_ids]
    model = _default_model()

    async with DanClient(base_url) as client:
        cases: list[dict[str, Any]] = []
        for fixture in fixtures:
            started = time.time()
            case = await _run_fixture(
                client,
                fixture,
                model=model,
                timeout_seconds=timeout_seconds,
                judge_mode=judge_mode,
                keep_graphs=keep_graphs,
            )
            case["elapsed_ms"] = int((time.time() - started) * 1000)
            cases.append(case)

    return {
        "kind": "workflow_result_similarity_benchmark",
        "generated_at": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "base_url": base_url,
        "env_file": str(env_path) if env_path is not None else None,
        "loaded_env_keys": loaded_env_keys,
        "judge_mode": judge_mode,
        "fixture_ids": [fixture.fixture_id for fixture in fixtures],
        "summary": _summarize_report(cases),
        "cases": cases,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.eval.workflow_result_similarity_benchmark",
        description=(
            "Build 10 long-form reference workflows, ask DAN to generate its own versions, "
            "run both, and compare outputs for meaningful similarity."
        ),
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="Optional fixture_id to run. May be repeated.",
    )
    parser.add_argument(
        "--judge",
        choices=("auto", "off", "required"),
        default="auto",
        help="Whether to use an LLM judge for output similarity.",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional env file to load before local execution. Defaults to .env when present.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--keep-graphs", action="store_true")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    report = asyncio.run(
        _run_benchmark(
            base_url=args.base_url,
            selected_case_ids=set(args.case or []),
            judge_mode=args.judge,
            timeout_seconds=args.timeout_seconds,
            keep_graphs=args.keep_graphs,
            env_file=args.env_file,
        )
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _report_path()
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output_path),
        "summary": report["summary"],
    }, indent=2))


if __name__ == "__main__":
    main()
