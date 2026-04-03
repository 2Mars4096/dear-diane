"""Core evaluation runner — sends prompts through the DAN server and collects results.

Part of the workflow generation quality evaluation system (Phase 33).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from tests.eval import (
    EvalRecord,
    ExecutionResult,
    GraphSummary,
    PromptFixture,
    TimingInfo,
    TokenInfo,
    ValidationResult,
    PROMPTS_FILE,
)
from tests.eval.client import DanClient
from tests.eval.metrics import EvalLogger
from tests.eval.telemetry_reader import TelemetryReader
from dan.server.audit import ChatAuditStore

try:
    from dan.meta.graph_quality import compute_quality_report
except ImportError:
    compute_quality_report = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

_MAX_CLARIFICATION_REPLIES = 3

_CLARIFICATION_SIGNALS = (
    "reply 1, 2, or 3",
    "reply 1 or 2",
    "reuse it, adapt it, or start fresh",
    "want me to reuse",
    "want me to adapt",
    "want to reuse",
    "shall i",
    "would you like me to",
    "which option",
    "please choose",
    "pick one",
    "please confirm",
    "meta session started",
)

_TIMEOUT_THRESHOLD_MS = 60_000

_ROUTING_BLOCKED_SIGNALS = (
    "please confirm",
    "would you like",
    "clarif",
    "meta session started",
)

_SMALL_BATTERY_TIERS = frozenset({"T1", "T2", "T2R", "T3", "T5"})

AVAILABLE_BATTERIES = (
    "small",
    "complex",
    "execution-friendly",
    "benchmark-prep",
)


def _needs_clarification_reply(text: str) -> bool:
    lower = (text or "").lower()
    return any(sig in lower for sig in _CLARIFICATION_SIGNALS)


def _any_event_needs_clarification(events: list[dict]) -> str | None:
    """Scan event list; return the content of the first that looks like a clarification request."""
    for event in events:
        content = event.get("content") or ""
        lower = content.lower()
        if any(sig in lower for sig in _CLARIFICATION_SIGNALS):
            return content
        if any(sig in lower for sig in _ROUTING_BLOCKED_SIGNALS):
            return content
    return None


def _auto_clarification_reply(text: str) -> str:
    lower = (text or "").lower()
    if "reuse" in lower and "fresh" in lower:
        return "3"
    if "reuse" in lower and "adapt" in lower:
        return "Start fresh"
    if "meta session started" in lower:
        return "yes, proceed"
    if "confirm" in lower:
        return "yes"
    return "Yes, go ahead"


class EvalRunner:
    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        db_path: str | None = None,
        audit_dir: str | Path | None = None,
        execute: bool = False,
        keep_graphs: bool = False,
        delay: float = 2.0,
        execution_path: str = "auto",
        judge: bool = False,
        workflow_contract: str = "enabled",
    ):
        self._client = DanClient(base_url)
        self._telemetry = TelemetryReader(db_path)
        self._audit = ChatAuditStore(audit_dir)
        self._execute = execute
        self._keep_graphs = keep_graphs
        self._delay = delay
        self._execution_path = execution_path
        self._judge = judge
        self._workflow_contract = workflow_contract
        self._created_graphs: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_battery(
        self,
        prompts: list[PromptFixture],
        *,
        lanes: list[str] | None = None,
        logger: EvalLogger,
        on_record: Callable[[EvalRecord], None] | None = None,
    ) -> list[EvalRecord]:
        """Run all prompts through specified lanes. Returns all records."""
        all_records: list[EvalRecord] = []

        total_tasks = 0
        contract_variants = _workflow_contract_variants(self._workflow_contract)
        for f in prompts:
            f_lanes = lanes if lanes is not None else _derive_lanes(f)
            total_tasks += len(f_lanes) * len(contract_variants)

        completed = 0
        passed = 0
        failed = 0
        _start_time = time.time()

        def _progress(fixture_id: str, lane: str, records: list[EvalRecord]) -> None:
            nonlocal completed, passed, failed
            p = sum(1 for r in records if r.status == "passed")
            f = len(records) - p
            passed += p
            failed += f
            completed += 1
            elapsed = time.time() - _start_time
            rate = elapsed / max(completed, 1)
            remaining = (total_tasks - completed) * rate
            if len(records) == 1:
                status_str = records[0].status
                if records[0].failure_mode:
                    status_str += f" ({records[0].failure_mode})"
            else:
                status_str = f"{len(records)} turns ({p}p/{f}f)"
            print(
                f"[{completed}/{total_tasks}] {fixture_id}/{lane}: {status_str}"
                f"  |  total: {passed}p/{failed}f "
                f"({100 * passed / max(passed + failed, 1):.0f}%)"
                f"  |  ~{remaining / 60:.0f}m left",
                flush=True,
            )

        for fixture in prompts:
            fixture_lanes = lanes if lanes is not None else _derive_lanes(fixture)

            for lane in fixture_lanes:
                for workflow_contract_variant in contract_variants:
                    try:
                        if fixture.multi_turn_follow_ups:
                            records = await self.run_multi_turn(
                                fixture,
                                lane,
                                logger,
                                workflow_contract_variant=workflow_contract_variant,
                            )
                        else:
                            records = [
                                await self.run_single(
                                    fixture,
                                    lane,
                                    logger,
                                    workflow_contract_variant=workflow_contract_variant,
                                )
                            ]
                    except Exception as exc:
                        log.error("[%s/%s/%s] unhandled error: %s", fixture.id, lane, workflow_contract_variant, exc)
                        records = [
                            _error_record(
                                fixture,
                                lane,
                                str(exc),
                                execution_path_requested=self._execution_path,
                                workflow_contract_variant=workflow_contract_variant,
                            )
                        ]
                        logger.log(records[0])

                    progress_label = (
                        fixture.id
                        if len(contract_variants) == 1
                        else f"{fixture.id}:{workflow_contract_variant}"
                    )
                    _progress(progress_label, lane, records)

                    for rec in records:
                        all_records.append(rec)
                        if on_record:
                            on_record(rec)

                    if self._delay > 0:
                        await asyncio.sleep(self._delay)

        elapsed_total = time.time() - _start_time
        print(
            f"\n=== Battery complete: {completed}/{total_tasks} tasks, "
            f"{passed} passed, {failed} failed "
            f"({100 * passed / max(passed + failed, 1):.0f}%) "
            f"in {elapsed_total / 60:.1f}m ===\n",
            flush=True,
        )

        return all_records

    async def run_single(
        self,
        fixture: PromptFixture,
        lane: str,
        logger: EvalLogger,
        *,
        workflow_contract_variant: str = "enabled",
    ) -> EvalRecord:
        """Evaluate one prompt in one lane."""
        graph_id = f"eval-{fixture.id}-{lane}-{workflow_contract_variant}-{uuid4().hex[:8]}"
        self._created_graphs.append(graph_id)
        log.info("[%s/%s/%s] starting", fixture.id, lane, workflow_contract_variant)

        error_msg: str | None = None
        events: list[dict] = []
        response_text = ""
        first_token_at: float | None = None
        complete_at: float | None = None
        generation_path: str | None = None
        domain_detected: str | None = None
        guard_events_list: list[dict] = []

        prompt_sent_at = time.time()

        channel_id: str | None = None
        try:
            await self._client.create_graph(graph_id)
            resp = await self._client.send_message(
                workflow_id=graph_id,
                message=fixture.prompt,
                mode=lane,
                surface_context=_eval_surface_context(workflow_contract_variant),
            )
            channel_id = resp.get("stream_channel_id", "")
        except Exception as exc:
            error_msg = str(exc)
            log.error("[%s/%s] setup failed: %s", fixture.id, lane, exc)

        if channel_id and error_msg is None:
            response_text, first_token_at, complete_at, generation_path, domain_detected, events, stream_err = (
                await self._consume_stream(channel_id)
            )
            if stream_err and not error_msg:
                error_msg = stream_err

            guard_events_list = [
                _summarize_event(e) for e in events
                if "guard" in e.get("type", "")
            ]

            clarification_content = _any_event_needs_clarification(events)
            if (clarification_content or _needs_clarification_reply(response_text)) and error_msg is None:
                for attempt in range(_MAX_CLARIFICATION_REPLIES):
                    reply = _auto_clarification_reply(response_text)
                    log.info("[%s/%s] auto-replying to clarification: %s", fixture.id, lane, reply)
                    try:
                        resp2 = await self._client.send_message(
                            workflow_id=graph_id,
                            message=reply,
                            mode=lane,
                            surface_context=_eval_surface_context(
                                workflow_contract_variant,
                            ),
                        )
                        ch2 = resp2.get("stream_channel_id", "")
                        if ch2:
                            rt2, ft2, ct2, gp2, dd2, ev2, se2 = await self._consume_stream(ch2)
                            events.extend(ev2)
                            if rt2:
                                response_text = rt2
                            if ft2 and first_token_at is None:
                                first_token_at = ft2
                            if ct2:
                                complete_at = ct2
                            if gp2:
                                generation_path = gp2
                            if dd2:
                                domain_detected = dd2
                            if se2 and not error_msg:
                                error_msg = se2
                            if not _needs_clarification_reply(rt2 or ""):
                                break
                    except Exception as exc:
                        log.warning("[%s/%s] clarification reply failed: %s", fixture.id, lane, exc)
                        break

        if complete_at is None:
            complete_at = time.time()

        audit_turn_id = _extract_turn_id(events)
        audit_data = await self._query_audit(audit_turn_id)
        graph_created, graph_summary, graph_dict = await self._inspect_graph(graph_id)
        validation = await self._validate(graph_id) if graph_created else None

        quality_score: int | None = None
        quality_concerns: list[str] = []
        if graph_created and graph_dict and compute_quality_report is not None:
            try:
                qr = compute_quality_report(graph_dict, fixture.prompt)
                quality_score = qr.overall_score
                quality_concerns = qr.concerns or []
            except Exception as exc:
                log.debug("quality report failed for %s: %s", graph_id, exc)

        judge_scores: dict[str, int] | None = None
        if self._judge and graph_created and graph_dict:
            from tests.eval.judge import judge_graph
            try:
                judge_scores = await judge_graph(
                    fixture.prompt, graph_dict, base_url=self._client._base_url,
                )
            except Exception as exc:
                log.debug("judge scoring failed for %s: %s", graph_id, exc)

        if graph_created and graph_dict:
            graph_record_id = fixture.id
            if workflow_contract_variant != "enabled":
                graph_record_id = f"{fixture.id}__contract-{workflow_contract_variant}"
            logger.log_graph(graph_record_id, lane, graph_dict)

        execution: ExecutionResult | None = None
        run_tokens: TokenInfo | None = None
        if self._execute and graph_created and validation and validation.passed:
            execution = await self._try_execute(graph_id)
            run_tokens = self._query_run_tokens(graph_id)

        tokens, model, telemetry_data = self._query_telemetry(prompt_sent_at, graph_id)

        gen_summary = _extract_generation_summary(events)

        total_time_ms = (complete_at - prompt_sent_at) * 1000
        status, failure_mode = _determine_status(
            fixture, graph_created, validation, generation_path, events,
            total_time_ms=total_time_ms, response_text=response_text,
            graph_summary=graph_summary,
            execution=execution,
        )
        if not error_msg and execution and execution.status != "completed":
            error_msg = execution.error
        if error_msg and status != "passed":
            failure_mode = failure_mode or ("timeout" if "timeout" in error_msg.lower() else "error")

        expectation_errors = _check_expectations(fixture, graph_summary)

        timing = TimingInfo(
            prompt_sent_at=prompt_sent_at,
            first_token_at=first_token_at,
            complete_at=complete_at,
            total_ms=total_time_ms,
        )

        record = EvalRecord(
            id=fixture.id,
            tier=fixture.tier,
            lane=lane,
            prompt=fixture.prompt,
            model=model,
            timestamp=datetime.now().isoformat(),
            timing=timing,
            build_tokens=tokens or TokenInfo(),
            run_tokens=run_tokens,
            graph_created=graph_created,
            graph_id=graph_id,
            graph_summary=graph_summary,
            validation=validation,
            quality_score=quality_score,
            quality_concerns=quality_concerns,
            expectation_errors=expectation_errors,
            judge_scores=judge_scores,
            execution=execution,
            status=status,
            failure_mode=failure_mode,
            generation_path=generation_path,
            generation_path_taken=gen_summary.get("path_taken") if gen_summary else None,
            fallback_chain=gen_summary.get("fallback_chain") if gen_summary else None,
            generation_wall_clock_ms=gen_summary.get("wall_clock_ms") if gen_summary else None,
            complexity_tier=gen_summary.get("complexity_tier") if gen_summary else None,
            retries_used=gen_summary.get("retries_used") if gen_summary else None,
            domain_detected=domain_detected,
            response_text=response_text,
            error=error_msg,
            observed_events=[_summarize_event(e) for e in events],
            guard_events=guard_events_list,
            telemetry=telemetry_data,
            execution_path_requested=self._execution_path,
            workflow_contract_variant=workflow_contract_variant,
            audit_turn_id=audit_turn_id,
            audit_found=bool(audit_data),
            prompt_module_ids=list((audit_data or {}).get("prompt_module_ids") or []),
            workflow_guidance_injected=bool((audit_data or {}).get("workflow_guidance_injected", False)),
            workflow_guidance_surface=str((audit_data or {}).get("workflow_guidance_surface") or ""),
        )

        log.info(
            "[%s/%s] %s%s",
            fixture.id, lane, status,
            f" ({failure_mode})" if failure_mode else "",
        )
        logger.log(record)
        return record

    async def run_multi_turn(
        self,
        fixture: PromptFixture,
        lane: str,
        logger: EvalLogger,
        *,
        workflow_contract_variant: str = "enabled",
    ) -> list[EvalRecord]:
        """Evaluate a multi-turn prompt sequence. Returns one record per turn."""
        graph_id = f"eval-{fixture.id}-{lane}-{workflow_contract_variant}-{uuid4().hex[:8]}"
        self._created_graphs.append(graph_id)
        log.info("[%s/%s/%s] starting multi-turn (%d turns)", fixture.id, lane, workflow_contract_variant,
                 1 + len(fixture.multi_turn_follow_ups or []))

        try:
            await self._client.create_graph(graph_id)
        except Exception as exc:
            rec = _error_record(
                fixture,
                lane,
                str(exc),
                graph_id=graph_id,
                execution_path_requested=self._execution_path,
                workflow_contract_variant=workflow_contract_variant,
            )
            logger.log(rec)
            return [rec]

        records: list[EvalRecord] = []
        history: list[dict] = []
        graph_revision: str | None = None
        turns = [fixture.prompt] + list(fixture.multi_turn_follow_ups or [])

        for turn_idx, message in enumerate(turns):
            turn_id = fixture.id if turn_idx == 0 else f"{fixture.id}-follow{turn_idx}"
            prompt_sent_at = time.time()
            error_msg: str | None = None

            try:
                resp = await self._client.send_message(
                    workflow_id=graph_id,
                    message=message,
                    mode=lane,
                    history=history if turn_idx > 0 else None,
                    client_graph_revision=graph_revision,
                    surface_context=_eval_surface_context(workflow_contract_variant),
                )
                channel_id = resp.get("stream_channel_id", "")
            except Exception as exc:
                error_msg = str(exc)
                channel_id = None

            response_text = ""
            first_token_at: float | None = None
            complete_at: float | None = None
            generation_path: str | None = None
            domain_detected: str | None = None
            events: list[dict] = []
            guard_events_mt: list[dict] = []

            if channel_id and error_msg is None:
                response_text, first_token_at, complete_at, generation_path, domain_detected, events, stream_err = (
                    await self._consume_stream(channel_id)
                )
                if stream_err and not error_msg:
                    error_msg = stream_err
                guard_events_mt = [
                    _summarize_event(e) for e in events
                    if "guard" in e.get("type", "")
                ]

            if complete_at is None:
                complete_at = time.time()

            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": response_text})

            audit_turn_id = _extract_turn_id(events)
            audit_data = await self._query_audit(audit_turn_id)
            graph_created, graph_summary, graph_dict = await self._inspect_graph(graph_id)
            if graph_dict:
                graph_revision = graph_dict.get("metadata", {}).get("updated_at") or graph_dict.get("version")

            quality_score_mt: int | None = None
            quality_concerns_mt: list[str] = []
            if graph_created and graph_dict and compute_quality_report is not None:
                try:
                    qr = compute_quality_report(graph_dict, message)
                    quality_score_mt = qr.overall_score
                    quality_concerns_mt = qr.concerns or []
                except Exception as exc:
                    log.debug("quality report failed for %s turn %d: %s", graph_id, turn_idx, exc)

            judge_scores_mt: dict[str, int] | None = None
            if self._judge and graph_created and graph_dict:
                from tests.eval.judge import judge_graph
                try:
                    judge_scores_mt = await judge_graph(
                        message,
                        graph_dict,
                        base_url=self._client._base_url,
                    )
                except Exception as exc:
                    log.debug(
                        "judge scoring failed for %s turn %d: %s",
                        graph_id,
                        turn_idx,
                        exc,
                    )

            if graph_created and graph_dict:
                graph_record_id = turn_id
                if workflow_contract_variant != "enabled":
                    graph_record_id = f"{turn_id}__contract-{workflow_contract_variant}"
                logger.log_graph(graph_record_id, lane, graph_dict)

            validation = await self._validate(graph_id) if graph_created else None
            tokens, model, telemetry_data = self._query_telemetry(prompt_sent_at, graph_id)
            gen_summary_mt = _extract_generation_summary(events)
            total_time_ms = (complete_at - prompt_sent_at) * 1000
            status, failure_mode = _determine_status(
                fixture, graph_created, validation, generation_path, events,
                total_time_ms=total_time_ms, response_text=response_text,
                graph_summary=graph_summary,
            )
            if error_msg and status != "passed":
                failure_mode = failure_mode or "error"

            if turn_idx == len(turns) - 1:
                expectation_errors = _check_expectations(fixture, graph_summary)
            else:
                expectation_errors = []

            timing = TimingInfo(
                prompt_sent_at=prompt_sent_at,
                first_token_at=first_token_at,
                complete_at=complete_at,
                total_ms=total_time_ms,
            )

            record = EvalRecord(
                id=turn_id,
                tier=fixture.tier,
                lane=lane,
                prompt=message,
                model=model,
                timestamp=datetime.now().isoformat(),
                timing=timing,
                build_tokens=tokens or TokenInfo(),
                graph_created=graph_created,
                graph_id=graph_id,
                graph_summary=graph_summary,
                quality_score=quality_score_mt,
                quality_concerns=quality_concerns_mt,
                expectation_errors=expectation_errors,
                judge_scores=judge_scores_mt,
                validation=validation,
                execution=None,
                status=status,
                failure_mode=failure_mode,
                generation_path=generation_path,
                generation_path_taken=gen_summary_mt.get("path_taken") if gen_summary_mt else None,
                fallback_chain=gen_summary_mt.get("fallback_chain") if gen_summary_mt else None,
                generation_wall_clock_ms=gen_summary_mt.get("wall_clock_ms") if gen_summary_mt else None,
                complexity_tier=gen_summary_mt.get("complexity_tier") if gen_summary_mt else None,
                retries_used=gen_summary_mt.get("retries_used") if gen_summary_mt else None,
                domain_detected=domain_detected,
                response_text=response_text,
                error=error_msg,
                observed_events=[_summarize_event(e) for e in events],
                guard_events=guard_events_mt,
                multi_turn_history=list(history),
                telemetry=telemetry_data,
                execution_path_requested=self._execution_path,
                workflow_contract_variant=workflow_contract_variant,
                audit_turn_id=audit_turn_id,
                audit_found=bool(audit_data),
                prompt_module_ids=list((audit_data or {}).get("prompt_module_ids") or []),
                workflow_guidance_injected=bool((audit_data or {}).get("workflow_guidance_injected", False)),
                workflow_guidance_surface=str((audit_data or {}).get("workflow_guidance_surface") or ""),
            )

            log.info("[%s/turn%d/%s] %s", fixture.id, turn_idx, lane, status)
            logger.log(record)
            records.append(record)

        return records

    async def cleanup(self, *, close_client: bool = True) -> None:
        """Delete created graphs (unless keep_graphs) and optionally close clients."""
        if not self._keep_graphs:
            for gid in self._created_graphs:
                try:
                    await self._client.delete_graph(gid)
                except Exception:
                    log.debug("cleanup: failed to delete %s", gid)
        self._created_graphs.clear()
        if close_client:
            self._telemetry.close()
            await self._client.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _consume_stream(
        self, channel_id: str,
    ) -> tuple[str, float | None, float | None, str | None, str | None, list[dict], str | None]:
        """Stream events and extract signals.

        Returns (response_text, first_token_at, complete_at, generation_path,
                 domain_detected, events, error_msg).
        """
        events: list[dict] = []
        response_text = ""
        first_token_at: float | None = None
        complete_at: float | None = None
        generation_path: str | None = None
        domain_detected: str | None = None
        error_msg: str | None = None

        try:
            async for event in self._client.stream_events(channel_id, timeout=120.0):
                events.append(event)
                etype = event.get("type", "")

                if etype == "chat_text":
                    if first_token_at is None:
                        first_token_at = time.time()
                    response_text += event.get("text", "")
                elif etype == "chat_token":
                    if first_token_at is None:
                        first_token_at = time.time()
                    response_text += event.get("text", event.get("token", ""))
                elif etype == "chat_intent_extracted":
                    generation_path = "intent_compiler"
                elif etype == "chat_code_generated":
                    generation_path = event.get("source", "codegen")
                elif etype == "chat_mutation":
                    if generation_path is None:
                        generation_path = "codegen"
                elif etype == "chat_complete":
                    complete_at = time.time()
                    content = event.get("content", "")
                    if content and not response_text:
                        response_text = content
                    if event.get("detected_mode") != "progress_ack":
                        break
                elif etype == "chat_error":
                    error_msg = event.get("error", event.get("content", "stream error"))

                meta = event.get("metadata") or {}
                if meta.get("domain"):
                    domain_detected = meta["domain"]
        except Exception as exc:
            log.warning("stream error on %s: %s", channel_id, exc)
            error_msg = f"stream: {exc}"

        return response_text, first_token_at, complete_at, generation_path, domain_detected, events, error_msg

    async def _inspect_graph(self, graph_id: str) -> tuple[bool, GraphSummary | None, dict | None]:
        try:
            graph_data = await self._client.get_graph(graph_id)
            if not graph_data:
                return False, None, None
            inner = graph_data.get("data", graph_data)
            nodes = inner.get("nodes", [])
            if nodes:
                return True, _build_graph_summary(inner), inner
            return False, None, None
        except Exception as exc:
            log.debug("get_graph failed for %s: %s", graph_id, exc)
            return False, None, None

    async def _validate(self, graph_id: str) -> ValidationResult:
        try:
            resp = await self._client.validate_graph(graph_id)
            raw_errors = resp.get("errors", [])
            errors = [
                e.get("message", str(e)) if isinstance(e, dict) else str(e)
                for e in raw_errors
            ]
            passed = len(raw_errors) == 0
            return ValidationResult(
                passed=passed,
                errors=errors,
                run_ready=bool(resp.get("run_ready", True)),
                run_readiness_issues=[
                    str(issue)
                    for issue in (resp.get("run_readiness_issues") or [])
                    if str(issue).strip()
                ],
                run_readiness_failure_mode=(
                    str(resp.get("run_readiness_failure_mode"))
                    if resp.get("run_readiness_failure_mode")
                    else None
                ),
            )
        except Exception:
            return ValidationResult(
                passed=False,
                errors=["validation_request_failed"],
                run_ready=False,
                run_readiness_issues=["validation_request_failed"],
            )

    async def _try_execute(self, graph_id: str) -> ExecutionResult:
        started_at = time.time()
        try:
            run_resp = await self._client.start_run(graph_id)
            run_id = run_resp.get("run_id", "")

            for _ in range(60):
                await asyncio.sleep(2.0)
                run_data = await self._client.get_run(run_id)
                state = run_data.get("state", run_data.get("status", ""))
                elapsed = (time.time() - started_at) * 1000
                if state in ("completed", "done", "finished"):
                    nc = self._count_completed_nodes(run_id)
                    return ExecutionResult(status="completed", duration_ms=elapsed, nodes_completed=nc)
                if state in ("failed", "error", "cancelled"):
                    err = run_data.get("error", f"run ended with state: {state}")
                    nc = self._count_completed_nodes(run_id)
                    return ExecutionResult(status="failed", error=err, duration_ms=elapsed, nodes_completed=nc)

            elapsed = (time.time() - started_at) * 1000
            nc = self._count_completed_nodes(run_id)
            return ExecutionResult(status="timeout", error="execution_timeout", duration_ms=elapsed, nodes_completed=nc)
        except Exception as exc:
            elapsed = (time.time() - started_at) * 1000
            return ExecutionResult(status="error", error=str(exc), duration_ms=elapsed)

    def _count_completed_nodes(self, run_id: str) -> int:
        """Query telemetry for completed node events in a run."""
        try:
            events = self._telemetry.get_run_telemetry(run_id)
            return sum(1 for e in events if e.get("event_type") == "workflow_node" and e.get("success", False))
        except Exception:
            return 0

    def _query_run_tokens(self, graph_id: str) -> TokenInfo | None:
        """Query telemetry for run-time token usage from workflow_run events."""
        try:
            events = self._telemetry.query_events(
                event_type="workflow_run",
                graph_id=graph_id,
                limit=5,
            )
            if not events:
                return None
            total_prompt = sum(e.get("prompt_tokens", 0) for e in events)
            total_completion = sum(e.get("completion_tokens", 0) for e in events)
            total_cost = sum(e.get("estimated_cost", 0.0) for e in events)
            if total_prompt == 0 and total_completion == 0:
                return None
            return TokenInfo(
                prompt_tokens=total_prompt,
                completion_tokens=total_completion,
                total_tokens=total_prompt + total_completion,
                estimated_cost=total_cost,
            )
        except Exception:
            return None

    def _query_telemetry(
        self, prompt_sent_at: float, graph_id: str,
    ) -> tuple[TokenInfo | None, str | None, dict | None]:
        prompt_sent_dt = datetime.fromtimestamp(prompt_sent_at)
        turn = self._telemetry.get_chat_turn(since=prompt_sent_dt, graph_id=graph_id)
        if not turn:
            return None, None, None

        tokens = TokenInfo(
            prompt_tokens=turn.get("prompt_tokens", 0),
            completion_tokens=turn.get("completion_tokens", 0),
            total_tokens=turn.get("total_tokens", 0),
            estimated_cost=turn.get("estimated_cost", 0.0),
        )
        return tokens, turn.get("model"), turn

    async def _query_audit(self, turn_id: str | None) -> dict[str, object] | None:
        if not turn_id:
            return None
        for attempt in range(3):
            try:
                record = self._audit.load_by_turn(turn_id)
            except Exception:
                log.debug("audit lookup failed for %s", turn_id, exc_info=True)
                return None
            if record is not None:
                return {
                    "prompt_module_ids": list(record.prompt_module_ids or []),
                    "workflow_guidance_injected": bool(record.workflow_guidance_injected),
                    "workflow_guidance_surface": str(record.workflow_guidance_surface or ""),
                }
            if attempt < 2:
                await asyncio.sleep(0.05)
        return None


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _flatten_edges(edges_raw: object) -> list[dict]:
    if isinstance(edges_raw, list):
        return [edge for edge in edges_raw if isinstance(edge, dict)]
    if isinstance(edges_raw, dict):
        flattened: list[dict] = []
        for value in edges_raw.values():
            if isinstance(value, list):
                flattened.extend(edge for edge in value if isinstance(edge, dict))
        return flattened
    return []


def _iter_graph_dicts(graph_data: object):
    if not isinstance(graph_data, dict):
        return
    yield graph_data
    sub_graphs = graph_data.get("sub_graphs", {})
    if not isinstance(sub_graphs, dict):
        return
    for sub_graph in sub_graphs.values():
        if isinstance(sub_graph, dict):
            yield from _iter_graph_dicts(sub_graph)


def _flatten_graph_nodes(graph_data: dict) -> list[dict]:
    nodes: list[dict] = []
    for graph in _iter_graph_dicts(graph_data):
        nodes.extend(
            node
            for node in graph.get("nodes", [])
            if isinstance(node, dict)
        )
    return nodes


def _flatten_graph_edges(graph_data: dict) -> list[dict]:
    edges: list[dict] = []
    for graph in _iter_graph_dicts(graph_data):
        edges.extend(_flatten_edges(graph.get("edges", [])))
    return edges


def _node_type_labels(node: dict) -> set[str]:
    raw = node.get("node_type") or node.get("type") or "unknown"
    labels = {raw}
    alias_map = {
        "llm_operator": {"llm"},
        "tool_operator": {"tool"},
        "code_operator": {"code"},
        "rag_operator": {"rag", "tool"},
        "while_loop": {"gate"},
        "if_else": {"gate"},
        "gate": {"gate"},
        "for_each": {"for_each"},
        "parallel_subagents": {"parallel_subagents"},
        "orchestrator": {"orchestrator"},
        "reduce": {"merge"},
        "human": {"human"},
        "human_in_the_loop": {"human"},
        "input": {"input"},
    }
    labels.update(alias_map.get(raw, set()))
    return labels


def _gate_mode(node: dict) -> str:
    direct = str(node.get("gate_mode", "")).lower()
    if direct:
        return direct
    config = node.get("config", {})
    if isinstance(config, dict):
        return str(config.get("gate_mode", "")).lower()
    return ""


def _build_graph_summary(graph_data: dict) -> GraphSummary:
    nodes = _flatten_graph_nodes(graph_data)
    edges = _flatten_graph_edges(graph_data)
    raw_node_types = {
        (node.get("node_type") or node.get("type"))
        for node in nodes
        if isinstance(node, dict)
    }
    node_types: set[str] = set()
    for node in nodes:
        if isinstance(node, dict):
            node_types.update(_node_type_labels(node))
    has_gate_while = any(
        (node.get("node_type") or node.get("type")) == "gate" and _gate_mode(node) == "while"
        for node in nodes
        if isinstance(node, dict)
    )
    has_gate_if_else = any(
        (node.get("node_type") or node.get("type")) == "gate" and _gate_mode(node) == "if_else"
        for node in nodes
        if isinstance(node, dict)
    )
    review_loop_count = sum(
        1
        for node in nodes
        if isinstance(node, dict)
        and (
            (node.get("node_type") or node.get("type")) == "while_loop"
            or (
                (node.get("node_type") or node.get("type")) == "gate"
                and _gate_mode(node) == "while"
            )
        )
    )
    has_loop = any(
        (node.get("node_type") or node.get("type")) in ("for_each", "while_loop")
        for node in nodes
        if isinstance(node, dict)
    ) or has_gate_while
    source_counts = Counter(
        edge.get("source_node_id", edge.get("source"))
        for edge in edges
        if isinstance(edge, dict)
    )
    target_counts = Counter(
        edge.get("target_node_id", edge.get("target"))
        for edge in edges
        if isinstance(edge, dict)
    )
    has_fan_out = (
        any(c > 1 for c in source_counts.values())
        or any(
            (node.get("node_type") or node.get("type")) in ("for_each", "parallel_subagents")
            for node in nodes
            if isinstance(node, dict)
        )
    )
    return GraphSummary(
        node_count=len(nodes),
        node_types=sorted(node_types),
        edge_count=len(edges),
        has_loop=has_loop,
        has_review_loop="while_loop" in raw_node_types or has_gate_while,
        review_loop_count=review_loop_count,
        has_conditional="if_else" in raw_node_types or has_gate_if_else,
        has_fan_out=has_fan_out,
        has_parallel_subagents="parallel_subagents" in raw_node_types,
        has_merge=any(c > 1 for c in target_counts.values()) or bool({"reduce", "orchestrator"} & raw_node_types),
    )


def _summarize_event(event: dict) -> dict:
    """Extract key fields from a stream event for the record, keeping payloads short."""
    out: dict = {"type": event.get("type", "")}
    for key in ("message_id", "content", "error", "graph_revision", "detected_mode"):
        val = event.get(key)
        if val:
            out[key] = str(val)[:300]
    if event.get("type") == "chat_generation_summary":
        for key in (
            "path_taken", "fallback_chain", "wall_clock_ms",
            "complexity_tier", "node_count", "retries_used", "quality_score",
        ):
            val = event.get(key)
            if val is not None:
                out[key] = val
    return out


def _extract_generation_summary(events: list[dict]) -> dict | None:
    """Find the first chat_generation_summary event in a stream event list."""
    return next(
        (e for e in events if e.get("type") == "chat_generation_summary"),
        None,
    )


def _extract_turn_id(events: list[dict]) -> str | None:
    for event in reversed(events):
        message_id = str(event.get("message_id") or "").strip()
        if message_id:
            return message_id
    return None


def _derive_lanes(fixture: PromptFixture) -> list[str]:
    lane = fixture.lane
    if lane == "both":
        return ["agent", "build"]
    return [lane]


def _fixture_tags(fixture: PromptFixture) -> set[str]:
    return {
        str(tag).strip().lower()
        for tag in (fixture.tags or [])
        if str(tag).strip()
    }


def _normalize_battery_name(name: str) -> str:
    normalized = str(name or "").strip().lower().replace("_", "-")
    alias_map = {
        "executionfriendly": "execution-friendly",
        "benchmarkprep": "benchmark-prep",
    }
    normalized = alias_map.get(normalized, normalized)
    if normalized not in AVAILABLE_BATTERIES:
        raise ValueError(f"Unsupported battery: {name}")
    return normalized


def _fixture_matches_battery(fixture: PromptFixture, battery: str) -> bool:
    normalized = _normalize_battery_name(battery)
    tags = _fixture_tags(fixture)
    if normalized == "small":
        return fixture.tier.upper() in _SMALL_BATTERY_TIERS
    if normalized == "complex":
        return fixture.tier.upper() == "T4" or bool(fixture.multi_turn_follow_ups)
    if normalized == "execution-friendly":
        return "execution_friendly" in tags
    if normalized == "benchmark-prep":
        return "benchmark_prep" in tags
    raise ValueError(f"Unsupported battery: {battery}")


def _order_prompt_fixtures(
    fixtures: list[PromptFixture],
    *,
    prefer_lr2: bool = False,
) -> list[PromptFixture]:
    if not prefer_lr2:
        return fixtures
    indexed = list(enumerate(fixtures))
    indexed.sort(
        key=lambda pair: (
            0 if "lr2_first" in _fixture_tags(pair[1]) else 1,
            pair[0],
        ),
    )
    return [fixture for _, fixture in indexed]


def _load_prompt_fixtures(path: Path | None = None) -> list[PromptFixture]:
    p = path or PROMPTS_FILE
    with open(p) as f:
        data = json.load(f)

    fixtures: list[PromptFixture] = []
    for item in data.get("prompts", []):
        fixtures.append(PromptFixture(
            id=item["id"],
            tier=item["tier"],
            lane=item.get("lane", "both"),
            prompt=item["prompt"],
            tags=item.get("tags", []),
            expected=item.get("expected") or {},
            multi_turn_follow_ups=item.get("multi_turn_follow_ups", []),
            pilot=item.get("pilot", False),
            edge_case=item.get("edge_case", False),
            expected_behavior=item.get("expected_behavior"),
        ))
    return fixtures


def describe_prompt_batteries(path: Path | None = None) -> dict[str, dict[str, object]]:
    fixtures = _load_prompt_fixtures(path)
    described: dict[str, dict[str, object]] = {}
    for battery in AVAILABLE_BATTERIES:
        selected = [fixture for fixture in fixtures if _fixture_matches_battery(fixture, battery)]
        ordered = _order_prompt_fixtures(
            selected,
            prefer_lr2=(battery == "benchmark-prep"),
        )
        entry: dict[str, object] = {
            "count": len(ordered),
            "ids": [fixture.id for fixture in ordered],
        }
        if battery == "benchmark-prep":
            lr2_ids = [
                fixture.id
                for fixture in ordered
                if "lr2_first" in _fixture_tags(fixture)
            ]
            entry["lr2_first_ids"] = lr2_ids
            entry["remaining_ids"] = [
                fixture.id
                for fixture in ordered
                if fixture.id not in lr2_ids
            ]
        described[battery] = entry
    return described


def _workflow_contract_variants(setting: str | None) -> list[str]:
    normalized = str(setting or "enabled").strip().lower()
    if normalized in {"", "enabled", "true", "1", "on"}:
        return ["enabled"]
    if normalized in {"disabled", "false", "0", "off"}:
        return ["disabled"]
    if normalized in {"compare", "both"}:
        return ["disabled", "enabled"]
    raise ValueError(f"Unsupported workflow contract setting: {setting}")


def _eval_surface_context(workflow_contract_variant: str) -> dict[str, bool]:
    normalized = str(workflow_contract_variant or "enabled").strip().lower()
    if normalized == "enabled":
        return {"workflow_generation_contract_enabled": True}
    if normalized == "disabled":
        return {"workflow_generation_contract_enabled": False}
    raise ValueError(
        f"Unsupported workflow contract variant: {workflow_contract_variant}",
    )


def _expects_no_graph(fixture: PromptFixture) -> bool:
    eb = (fixture.expected_behavior or "").lower()
    return any(kw in eb for kw in (
        "not build", "clarification", "answer directly", "conversationally",
    ))


def _check_expectations(
    fixture: PromptFixture, graph_summary: GraphSummary | None,
) -> list[str]:
    expected = fixture.expected
    if not expected or graph_summary is None:
        return []
    errors: list[str] = []
    if expected.get("min_nodes") is not None and graph_summary.node_count < expected["min_nodes"]:
        errors.append(f"node_count {graph_summary.node_count} < expected min {expected['min_nodes']}")
    if expected.get("max_nodes") is not None and graph_summary.node_count > expected["max_nodes"]:
        errors.append(f"node_count {graph_summary.node_count} > expected max {expected['max_nodes']}")
    for nt in expected.get("node_types", []):
        if nt not in graph_summary.node_types:
            errors.append(f"expected node type '{nt}' not found")
    node_types = set(graph_summary.node_types)
    for topo in expected.get("topology", []):
        if topo == "review_loop" and not graph_summary.has_review_loop:
            errors.append(f"expected topology '{topo}' not satisfied")
        elif topo == "fan_out" and not graph_summary.has_fan_out:
            errors.append(f"expected topology '{topo}' not satisfied")
        elif topo == "chain" and (
            not (
                graph_summary.node_count == 1
                and graph_summary.edge_count == 0
                and (expected.get("min_nodes") or 0) <= 1
            )
            and (graph_summary.node_count < 2 or graph_summary.edge_count < 1)
        ):
            errors.append(f"expected topology '{topo}' not satisfied")
        elif topo == "tools" and "tool" not in node_types:
            errors.append(f"expected topology '{topo}' not satisfied")
        elif topo == "code" and "code" not in node_types:
            errors.append(f"expected topology '{topo}' not satisfied")
        elif topo == "conditional" and not graph_summary.has_conditional:
            errors.append(f"expected topology '{topo}' not satisfied")
        elif topo == "rag_qa" and not ({"rag", "llm"} <= node_types):
            errors.append(f"expected topology '{topo}' not satisfied")
        elif topo == "human_review" and "human" not in node_types:
            errors.append(f"expected topology '{topo}' not satisfied")
        elif topo == "orchestrator" and "orchestrator" not in node_types:
            errors.append(f"expected topology '{topo}' not satisfied")
        elif topo == "parallel_subagents" and not graph_summary.has_parallel_subagents:
            errors.append(f"expected topology '{topo}' not satisfied")
        elif topo == "nested_review_loops" and graph_summary.review_loop_count < 2:
            errors.append(f"expected topology '{topo}' not satisfied")
        elif topo == "merge" and not graph_summary.has_merge:
            errors.append(f"expected topology '{topo}' not satisfied")
        elif topo == "data_ingest" and not ({"tool", "input"} & node_types):
            errors.append(f"expected topology '{topo}' not satisfied")
    return errors


def _determine_status(
    fixture: PromptFixture,
    graph_created: bool,
    validation: ValidationResult | None,
    generation_path: str | None,
    events: list[dict],
    total_time_ms: float = 0.0,
    response_text: str = "",
    graph_summary: GraphSummary | None = None,
    execution: ExecutionResult | None = None,
) -> tuple[str, str | None]:
    if fixture.edge_case:
        if _expects_no_graph(fixture):
            if not graph_created:
                if fixture.tier.upper() == "T5":
                    return "passed", "correct_refusal"
                return "passed", None
            return "failed", "misrouted"
        return "passed", None

    if not graph_created:
        guard_events = [e for e in events if "guard" in e.get("type", "")]
        if guard_events:
            return "failed", "guard_short_circuit"
        return "failed", _classify_no_graph(
            fixture, generation_path, events, total_time_ms, response_text,
        )

    if validation and not validation.passed:
        return "failed", "validation_error"

    if validation and not validation.run_ready:
        return "failed", validation.run_readiness_failure_mode or "not_run_ready"

    if execution and execution.status != "completed":
        if execution.status == "timeout":
            return "failed", "execution_timeout"
        if execution.status == "error":
            return "failed", "execution_error"
        return "failed", "execution_failed"

    if validation and validation.passed:
        if _check_expectations(fixture, graph_summary):
            return "failed", "expectation_mismatch"
        return "passed", None

    return "failed", "no_graph_created"


def _classify_no_graph(
    fixture: PromptFixture,
    generation_path: str | None,
    events: list[dict],
    total_time_ms: float,
    response_text: str,
) -> str:
    """Classify a no-graph-created failure into a granular subcategory.

    Priority: stream_error → routing_blocked → llm_error → correct_refusal
    → timeout_codegen → timeout_planning → codegen_failed → no_graph_created
    """
    meaningful = [
        e for e in events
        if e.get("type", "") not in ("", "connection", "ping", "heartbeat")
    ]
    if not meaningful:
        return "stream_error"

    lower_resp = (response_text or "").lower()
    if any(sig in lower_resp for sig in _ROUTING_BLOCKED_SIGNALS):
        return "routing_blocked"
    for e in events:
        if e.get("type") == "chat_complete" and e.get("detected_mode") == "confirm":
            return "routing_blocked"
        e_content = (e.get("content") or "").lower()
        if e_content and any(sig in e_content for sig in _ROUTING_BLOCKED_SIGNALS):
            return "routing_blocked"

    if any(e.get("type") == "chat_error" for e in events):
        return "llm_error"

    if fixture.tier.upper() == "T5" and _expects_no_graph(fixture):
        return "correct_refusal"

    codegen_reached = generation_path in ("codegen", "intent_compiler", "structured_generation")
    if codegen_reached and total_time_ms > _TIMEOUT_THRESHOLD_MS:
        return "timeout_codegen"
    if not codegen_reached and total_time_ms > _TIMEOUT_THRESHOLD_MS:
        return "timeout_planning"

    if generation_path == "codegen":
        if any(
            e.get("type") == "chat_validation_result" and not e.get("success", True)
            for e in events
        ):
            return "codegen_failed"

    return "no_graph_created"


def _error_record(
    fixture: PromptFixture,
    lane: str,
    error: str,
    *,
    graph_id: str | None = None,
    execution_path_requested: str | None = None,
    workflow_contract_variant: str | None = None,
) -> EvalRecord:
    now = time.time()
    return EvalRecord(
        id=fixture.id,
        tier=fixture.tier,
        lane=lane,
        prompt=fixture.prompt,
        timestamp=datetime.now().isoformat(),
        timing=TimingInfo(
            prompt_sent_at=now, first_token_at=None,
            complete_at=now, total_ms=0.0,
        ),
        graph_id=graph_id or "",
        status="error",
        failure_mode="error",
        error=error,
        execution_path_requested=execution_path_requested,
        workflow_contract_variant=workflow_contract_variant,
    )


def load_prompts(
    path: Path | None = None,
    *,
    tier: str | list[str] | None = None,
    pilot_only: bool = False,
    complex_only: bool = False,
    tags: list[str] | None = None,
    batteries: list[str] | None = None,
    prefer_lr2: bool = False,
) -> list[PromptFixture]:
    """Load and optionally filter prompt fixtures from JSON file."""
    fixtures = _load_prompt_fixtures(path)

    if tier:
        if isinstance(tier, str):
            tier = [tier]
        allowed = {t.upper() for t in tier}
        fixtures = [f for f in fixtures if f.tier.upper() in allowed]
    if pilot_only:
        fixtures = [f for f in fixtures if f.pilot]
    if complex_only:
        fixtures = [f for f in fixtures if f.tier.upper() == "T4" or f.multi_turn_follow_ups]
    if tags:
        required = {t.lower() for t in tags}
        fixtures = [f for f in fixtures if required & {t.lower() for t in f.tags}]
    if batteries:
        selected = {_normalize_battery_name(name) for name in batteries}
        fixtures = [
            f for f in fixtures
            if any(_fixture_matches_battery(f, battery) for battery in selected)
        ]
    fixtures = _order_prompt_fixtures(fixtures, prefer_lr2=prefer_lr2)

    return fixtures
