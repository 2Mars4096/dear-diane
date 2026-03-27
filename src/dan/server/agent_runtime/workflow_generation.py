"""Workflow generation runtime extracted from ``ChatManager``."""

from __future__ import annotations

import copy
import ast
import asyncio
import logging
import os
import time
import uuid
from typing import Any, Awaitable, Callable

from dan.providers import CompletionResult
from dan.meta.workflow_contract import workflow_build_provenance
from dan.server.agent_runtime.workflow_generation_acceptance import (
    accept_candidate_graph,
    collect_validation_errors,
)
from dan.server.agent_runtime.workflow_generation_codegen import (
    request_builder_code,
)
from dan.server.chat.events import (
    ChatCodeGeneratedEvent,
    ChatCompleteEvent,
    ChatGenerationSummaryEvent,
    ChatGraphQualityEvent,
    ChatIntentExtractedEvent,
    ChatNoticeEvent,
    ChatStreamEvent,
    ChatValidationResultEvent,
)
from dan.server.chat.helpers import _is_transient_llm_error

logger = logging.getLogger(__name__)


class WorkflowGenerationRuntime:
    """Agent-runtime helper for empty-graph workflow generation."""

    async def generate(
        self,
        *,
        user_message: str,
        workflow_id: str,
        channel_id: str,
        effective_model: str | None,
        default_model: str,
        behavior_store: Any,
        resolve_provider: Callable[..., Any],
        emit_intent_extraction_telemetry: Callable[..., None],
        record_gen_outcome: Callable[..., None],
        get_generation_stats_hint: Callable[[], str],
        parse_intent_from_result: Callable[[CompletionResult], Any],
        exec_deterministic_builder_code: Callable[[str], dict | None],
        sandbox_exec_builder_code: Callable[[str], Awaitable[tuple[dict | None, Any]]],
        extract_code_from_response: Callable[[str], str],
    ) -> tuple[dict | None, list[ChatStreamEvent]]:
        """Generate a workflow graph and summary events for an empty graph."""

        from dan.meta.diagnosis import GenerationError, GenerationErrorType, GenerationStage
        from dan.meta.intent_compiler import CoverageChecker, IntentCompiler
        from dan.meta.intent_extraction import (
            build_intent_extraction_system_prompt,
            build_intent_tool_schema,
        )
        from dan.meta.intent_schema import WorkflowIntent
        from dan.meta.graph_quality import (
            compute_quality_report,
            estimate_prompt_complexity,
            expected_node_range,
            is_acceptable_simple_graph,
            tier_quality_threshold,
        )
        from dan.meta.planner import validate_codegen_output

        _model = effective_model or default_model
        events: list[ChatStreamEvent] = []
        _max_gen_seconds = int(os.environ.get("DAN_MAX_GENERATION_SECONDS", "120") or "120")
        _gen_start = time.monotonic()

        def _deadline_exceeded() -> bool:
            return (time.monotonic() - _gen_start) > _max_gen_seconds

        def _remaining_seconds() -> float:
            return max(0.0, _max_gen_seconds - (time.monotonic() - _gen_start))

        def _elapsed_ms() -> int:
            return int((time.monotonic() - _gen_start) * 1000)

        def _elapsed_s() -> float:
            return time.monotonic() - _gen_start

        complexity_tier = estimate_prompt_complexity(user_message)
        min_nodes, max_nodes = expected_node_range(user_message, tier=complexity_tier)
        _fast_path_slow_s = float(
            os.environ.get("DAN_INTENT_FAST_PATH_SLOW_SECONDS", "15.0") or "15.0"
        )

        fallback_chain: list[str] = []
        retries_used: dict[str, int] = {
            "intent_extraction": 0,
            "codegen": 0,
            "sandbox": 0,
            "automatic_recovery": 0,
        }
        _last_quality_score: int | None = None
        _result_node_count: int | None = None
        _path_taken = "none"
        _progress_emitted = False
        _final_failure_mode: str | None = None
        _last_build_provenance = workflow_build_provenance(None)
        _automatic_recovery: dict[str, Any] = {}

        def _emit_progress(phase: str) -> None:
            nonlocal _progress_emitted
            elapsed = _elapsed_s()
            if elapsed > 60:
                msg = f"This is taking longer than usual. {phase} ({elapsed:.0f}s elapsed)"
            elif elapsed > 10:
                msg = f"Generating workflow... ({elapsed:.0f}s, {phase})"
            else:
                return
            events.append(
                ChatCompleteEvent(
                    message_id=uuid.uuid4().hex[:12],
                    content=msg,
                    token_usage={},
                    context_window=0,
                    graph_revision="",
                    detected_mode="progress_ack",
                )
            )
            _progress_emitted = True

        _pre_generation_ms: int | None = None

        def _build_summary_event() -> ChatGenerationSummaryEvent:
            return ChatGenerationSummaryEvent(
                path_taken=_path_taken,
                retries_used=retries_used,
                quality_score=_last_quality_score,
                wall_clock_ms=_elapsed_ms(),
                fallback_chain=list(fallback_chain),
                node_count=_result_node_count,
                complexity_tier=complexity_tier,
                pre_generation_ms=_pre_generation_ms,
                failure_mode=_final_failure_mode,
                automatic_recovery=copy.deepcopy(_automatic_recovery),
                **_last_build_provenance,
            )

        async def _run_with_generation_budget(
            awaitable: Awaitable[Any],
            *,
            stage_label: str,
        ) -> Any:
            remaining = _remaining_seconds()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"Generation budget exhausted before {stage_label}"
                )
            return await asyncio.wait_for(awaitable, timeout=remaining)

        def _timeout_result(
            *,
            stage_label: str,
            outcome_method: str,
            path_taken: str,
        ) -> tuple[dict | None, list[ChatStreamEvent]]:
            nonlocal _path_taken, _final_failure_mode
            elapsed = _elapsed_s()
            logger.warning(
                "Generation timed out during %s (%.1fs / %ds budget)",
                stage_label,
                elapsed,
                _max_gen_seconds,
            )
            record_gen_outcome(
                outcome_method,
                success=False,
                error_type="generation_timeout",
                pattern=workflow_id,
            )
            _path_taken = path_taken
            _final_failure_mode = "generation_timeout"
            events.append(
                ChatValidationResultEvent(
                    success=False,
                    error_count=1,
                    errors=[
                        (
                            f"Generation timed out during {stage_label} after "
                            f"{elapsed:.0f}s (budget: {_max_gen_seconds}s)"
                        )
                    ],
                    failure_mode="generation_timeout",
                    **workflow_build_provenance(None),
                )
            )
            events.append(_build_summary_event())
            return None, events

        def _validation_event(validation: Any) -> ChatValidationResultEvent:
            nonlocal _final_failure_mode, _last_build_provenance
            errors = [e.message for e in collect_validation_errors(validation)[:5]]
            contract_report = getattr(validation, "contract_report", None)
            if contract_report is not None and not getattr(validation, "run_ready", True):
                for issue in contract_report.run_readiness_issues[:5]:
                    if issue not in errors:
                        errors.append(issue)
                _last_build_provenance = workflow_build_provenance(contract_report)
            elif contract_report is not None:
                _last_build_provenance = workflow_build_provenance(contract_report)
            if bool(validation.success and getattr(validation, "run_ready", True)):
                _final_failure_mode = None
            else:
                failure_mode = getattr(validation, "run_readiness_failure_mode", None)
                if failure_mode:
                    _final_failure_mode = failure_mode
            provenance = (
                _last_build_provenance
                if contract_report is not None
                else workflow_build_provenance(None)
            )
            return ChatValidationResultEvent(
                success=bool(validation.success and getattr(validation, "run_ready", True)),
                error_count=len(errors),
                errors=errors,
                failure_mode=getattr(validation, "run_readiness_failure_mode", None),
                **provenance,
            )

        def _fit_check(graph_dict: dict) -> None:
            nonlocal _result_node_count
            nodes = graph_dict.get("nodes", [])
            count = len(nodes) if isinstance(nodes, list) else 0
            _result_node_count = count
            if count < min_nodes * 0.5:
                logger.warning(
                    "Underspecified graph: %d nodes, expected %d-%d",
                    count,
                    min_nodes,
                    max_nodes,
                )

        _quality_threshold_raw = os.environ.get("DAN_GRAPH_QUALITY_THRESHOLD")
        try:
            _quality_threshold_override = (
                int(_quality_threshold_raw)
                if _quality_threshold_raw is not None
                else -1
            )
        except (ValueError, TypeError):
            _quality_threshold_override = -1
        provider = resolve_provider(pii_session_key=workflow_id, model=_model)

        def _quality_error_for_graph(
            graph_dict: dict,
            *,
            warning_message: str,
        ) -> GenerationError | None:
            nonlocal _last_quality_score
            if is_acceptable_simple_graph(graph_dict, user_message):
                return None
            report = compute_quality_report(graph_dict, user_message, tier=None)
            _last_quality_score = report.overall_score
            events.append(
                ChatGraphQualityEvent(
                    score=report.overall_score,
                    concerns=report.concerns,
                )
            )
            threshold = (
                _quality_threshold_override
                if _quality_threshold_override >= 0
                else tier_quality_threshold(None, user_message)
            )
            if threshold > 0 and report.overall_score < threshold:
                logger.warning(
                    warning_message,
                    report.overall_score,
                    threshold,
                )
                return GenerationError(
                    stage=GenerationStage.validation,
                    error_type=GenerationErrorType.unknown,
                    message=f"Quality score {report.overall_score} below threshold {threshold}",
                    recoverable=True,
                )
            return None

        def _diagnosis_graph_validator(graph_dict: dict) -> list[GenerationError]:
            validation = validate_codegen_output(graph_dict)
            if not (validation.success and validation.run_ready):
                return list(collect_validation_errors(validation))
            quality_error = _quality_error_for_graph(
                graph_dict,
                warning_message="Graph quality %d below threshold %d, retrying diagnosis",
            )
            return [quality_error] if quality_error is not None else []

        def _sandbox_failure_error(codegen_result: Any) -> GenerationError:
            err_msg = ""
            err_type = GenerationErrorType.no_output
            if codegen_result is not None:
                err_msg = (
                    getattr(codegen_result, "error_message", None) or ""
                ).strip()
                raw_type = str(
                    getattr(codegen_result, "error_type", "") or ""
                ).strip().lower()
                try:
                    err_type = GenerationErrorType(raw_type)
                except ValueError:
                    if "import" in raw_type:
                        err_type = GenerationErrorType.import_error
                    elif "name" in raw_type:
                        err_type = GenerationErrorType.name_error
                    elif raw_type:
                        err_type = GenerationErrorType.runtime_error
            return GenerationError(
                stage=GenerationStage.sandbox,
                error_type=err_type,
                message=err_msg or "Builder code produced no graph output",
                source_line=getattr(codegen_result, "error_line", None),
                recoverable=True,
            )

        def _diagnosis_attempt_summary(diag_result: Any) -> str:
            attempts = list(getattr(diag_result, "attempts", []) or [])
            if not attempts:
                return "Diagnosis exhausted without a successful repair attempt."
            lines: list[str] = []
            for attempt in attempts[:4]:
                strategy = getattr(getattr(attempt, "strategy_used", None), "value", None)
                strategy = strategy or str(getattr(attempt, "strategy_used", "") or "unknown")
                result = str(getattr(attempt, "result", "") or "failed")
                corrections = ", ".join(
                    str(item).strip()
                    for item in (getattr(attempt, "corrections_applied", None) or [])
                    if str(item).strip()
                )
                learning = "; ".join(
                    str(item).strip()
                    for item in (getattr(attempt, "learning_points", None) or [])
                    if str(item).strip()
                )
                line = f"- attempt {getattr(attempt, 'attempt_number', '?')}: strategy={strategy}, result={result}"
                if corrections:
                    line += f", corrections={corrections}"
                if learning:
                    line += f", learnings={learning}"
                lines.append(line)
            return "\n".join(lines)

        def _diagnosis_attempt_payload(diag_result: Any) -> list[dict[str, Any]]:
            payload: list[dict[str, Any]] = []
            for attempt in list(getattr(diag_result, "attempts", []) or []):
                strategy = getattr(attempt, "strategy_used", None)
                if hasattr(strategy, "value"):
                    strategy = getattr(strategy, "value")
                payload.append(
                    {
                        "kind": str(strategy or "unknown"),
                        "outcome": str(getattr(attempt, "result", "") or "failed"),
                        "cause": "diagnosis_repair",
                        "corrections_applied": [
                            str(item).strip()
                            for item in (getattr(attempt, "corrections_applied", None) or [])
                            if str(item).strip()
                        ],
                        "learning_points": [
                            str(item).strip()
                            for item in (getattr(attempt, "learning_points", None) or [])
                            if str(item).strip()
                        ],
                    }
                )
            return payload

        def _automatic_recovery_failure_lines(
            *,
            codegen_errors: list[Any],
            diag_result: Any,
        ) -> list[str]:
            return [
                str(getattr(err, "message", err) or "").strip()
                for err in (
                    list(getattr(diag_result, "final_errors", []) or [])
                    or codegen_errors
                )
                if str(getattr(err, "message", err) or "").strip()
            ]

        def _build_automatic_recovery_context(
            *,
            codegen_errors: list[Any],
            diag_result: Any,
            prior_code: str,
        ) -> str:
            failure_lines = _automatic_recovery_failure_lines(
                codegen_errors=codegen_errors,
                diag_result=diag_result,
            )
            recovery_summary = [
                "Automatic recovery handoff:",
                "- Previous generation exhausted codegen, sandbox validation, and bounded diagnosis.",
            ]
            if failure_lines:
                recovery_summary.append("- Primary failures:")
                recovery_summary.extend(f"  - {line}" for line in failure_lines[:5])
            diagnosis_summary = _diagnosis_attempt_summary(diag_result)
            if diagnosis_summary:
                recovery_summary.append("- Diagnosis attempts:")
                recovery_summary.extend(diagnosis_summary.splitlines())
            build_summary = str(_last_build_provenance.get("build_summary") or "").strip()
            if build_summary:
                recovery_summary.append(f"- Build/readiness summary: {build_summary}")
            failure_bucket = str(_last_build_provenance.get("failure_bucket") or "").strip()
            if failure_bucket:
                recovery_summary.append(f"- Failure bucket: {failure_bucket}")
            if prior_code.strip():
                recovery_summary.append("- Previous builder code (repair the smallest necessary part, do not restart from scratch unless required):")
                recovery_summary.append("```python")
                recovery_summary.append(prior_code[:6000])
                recovery_summary.append("```")
            recovery_summary.append(
                "- Return executable Python builder code only. Fix the smallest issue set needed to produce a runnable workflow."
            )
            return "\n".join(recovery_summary)

        detected_domain: str | None = None
        try:
            from dan.server.concierge.domain_learning import detect_domain

            detected_domain = detect_domain(
                user_message,
                None,
                behavior_store=behavior_store,
            )
        except Exception:
            pass

        intent: WorkflowIntent | None = None
        intent_tool = build_intent_tool_schema()
        intent_messages = [
            {"role": "system", "content": build_intent_extraction_system_prompt()},
            {"role": "user", "content": user_message},
        ]
        for attempt in range(2):
            try:
                intent_result = await provider.complete(
                    messages=intent_messages,
                    model=_model,
                    temperature=0.3,
                    tools=[intent_tool],
                    tool_choice="auto",
                )
                if (
                    not (intent_result.text or "").strip()
                    and not (intent_result.tool_calls or [])
                ):
                    if attempt == 0:
                        retries_used["intent_extraction"] += 1
                        logger.warning(
                            "Intent extraction empty response, retrying (attempt %d)",
                            attempt + 1,
                        )
                        await asyncio.sleep(2)
                        continue
                intent = parse_intent_from_result(intent_result)
                logger.info(
                    "Intent extraction: tool_call_present=%s, parsed=%s",
                    bool(intent_result.tool_calls),
                    intent is not None,
                )
                break
            except Exception as exc:
                if attempt == 0 and _is_transient_llm_error(exc):
                    retries_used["intent_extraction"] += 1
                    logger.warning(
                        "Intent extraction transient error (attempt %d): %s",
                        attempt + 1,
                        exc,
                    )
                    await asyncio.sleep(2)
                    continue
                logger.info("Intent extraction failed: %s", exc)
                break

        if intent is not None:
            from dan.meta.intent_extraction import validate_and_expand_intent

            intent = validate_and_expand_intent(intent, user_message)

        coverage_fully_covered = False
        coverage_recommendation = None
        coverage_patterns: list[str] | None = None
        if intent is not None:
            try:
                coverage = CoverageChecker().check(intent)
                coverage_fully_covered = coverage.fully_covered
                coverage_recommendation = coverage.recommendation
                coverage_patterns = coverage.constituent_patterns
            except Exception as exc:
                logger.info("Intent coverage check failed: %s", exc)
            logger.info(
                "Intent compiler readiness: fully_covered=%s, recommendation=%s, constituent_patterns=%s",
                coverage_fully_covered,
                coverage_recommendation,
                coverage_patterns,
            )
            events.append(
                ChatIntentExtractedEvent(
                    intent_summary=intent.goal[:200],
                    stage_count=len(intent.stages),
                    fully_covered=coverage_fully_covered,
                )
            )
            emit_intent_extraction_telemetry(
                workflow_id=workflow_id,
                extracted=True,
                fully_covered=coverage_fully_covered,
                recommendation=coverage_recommendation,
                stage_count=len(intent.stages),
                patterns=coverage_patterns,
            )
        else:
            emit_intent_extraction_telemetry(
                workflow_id=workflow_id,
                extracted=False,
                fully_covered=False,
                recommendation=None,
                stage_count=0,
                patterns=None,
            )

        if intent is not None and coverage_fully_covered:
            fallback_chain.append("intent_compiler")
            try:
                compiler = IntentCompiler()
                graph_dict = None

                try:
                    from dan.meta.intent_compiler import DirectBuildError

                    if coverage_recommendation == "compose" and coverage_patterns:
                        graph_obj = compiler.build_graph_composed(
                            intent,
                            coverage_patterns,
                            domain=detected_domain,
                        )
                    else:
                        graph_obj = compiler.build_graph(
                            intent,
                            domain=detected_domain,
                        )
                    graph_dict = graph_obj.model_dump(mode="json")
                    events.append(
                        ChatCodeGeneratedEvent(
                            code_snippet=f"# Direct build: {len(graph_obj.nodes)} nodes",
                            source="intent_compiler",
                        )
                    )
                except (DirectBuildError, Exception) as _build_exc:
                    logger.debug(
                        "build_graph() failed (%s), falling back to compile()",
                        _build_exc,
                    )
                    graph_dict = None
                    if coverage_recommendation == "compose" and coverage_patterns:
                        builder_code = compiler.compile_composed(
                            intent,
                            coverage_patterns,
                            domain=detected_domain,
                        )
                    else:
                        builder_code = compiler.compile(
                            intent,
                            domain=detected_domain,
                        )
                    if builder_code and builder_code.strip():
                        events.append(
                            ChatCodeGeneratedEvent(
                                code_snippet=builder_code[:500],
                                source="intent_compiler",
                            )
                        )
                        graph_dict = exec_deterministic_builder_code(builder_code)

                if graph_dict is not None:
                    acceptance = accept_candidate_graph(
                        graph_dict,
                        validate_graph=validate_codegen_output,
                        build_validation_event=_validation_event,
                        emit_event=events.append,
                        quality_error_for_graph=lambda candidate: _quality_error_for_graph(
                            candidate,
                            warning_message=(
                                "Graph quality %d below threshold %d, falling back to codegen"
                            ),
                        ),
                        fit_check=_fit_check,
                        record_gen_outcome=record_gen_outcome,
                        success_method="intent_compiler",
                        failure_method="intent_compiler",
                        failure_fix_needed=True,
                        pattern=workflow_id,
                    )
                    if acceptance.accepted_graph is not None:
                        _path_taken = "intent_compiler"
                        ic_elapsed = _elapsed_s()
                        if ic_elapsed > _fast_path_slow_s:
                            logger.warning(
                                "Intent compiler slow path: %.1fs (threshold: %.1fs)",
                                ic_elapsed,
                                _fast_path_slow_s,
                            )
                        events.append(_build_summary_event())
                        return acceptance.accepted_graph, events
                    logger.info(
                        "Intent compiler: validation failed (%d errors); falling back to codegen",
                        len(acceptance.errors),
                    )
                else:
                    logger.info(
                        "Intent compiler: produced no graph; falling back to codegen",
                    )
                    record_gen_outcome(
                        "intent_compiler",
                        success=False,
                        error_type="no_graph",
                        pattern=workflow_id,
                    )
            except Exception as exc:
                stage_types = [s.stage_type.value for s in intent.stages] if intent else []
                logger.warning(
                    "Intent compiler failed (stages=%s): %s, falling through to codegen",
                    stage_types,
                    exc,
                )
                logger.info(
                    "Intent compiler: %s; falling back to codegen",
                    exc,
                )
                record_gen_outcome(
                    "intent_compiler",
                    success=False,
                    error_type="compile_exception",
                    pattern=workflow_id,
                )

        if _deadline_exceeded():
            return _timeout_result(
                stage_label="code generation",
                outcome_method="codegen",
                path_taken="none",
            )

        fallback_chain.append("codegen")
        _emit_progress("codegen in progress")

        codegen_errors: list[Any] = []
        codegen_request = await request_builder_code(
            provider=provider,
            model=_model,
            user_message=user_message,
            intent_goal=intent.goal if intent else None,
            gen_stats_hint=get_generation_stats_hint(),
            detected_domain=detected_domain,
            complexity_tier=complexity_tier,
            min_nodes=min_nodes,
            max_nodes=max_nodes,
            extract_code_from_response=extract_code_from_response,
            retries_used=retries_used,
            record_gen_outcome=record_gen_outcome,
            pattern=workflow_id,
            is_transient_llm_error=_is_transient_llm_error,
            get_timeout_seconds=_remaining_seconds,
            log=logger,
        )
        builder_code = codegen_request.builder_code
        codegen_retries = codegen_request.codegen_retries
        terminal_codegen_failure = codegen_request.terminal_failure
        terminal_codegen_message = codegen_request.terminal_message

        if terminal_codegen_failure == "generation_timeout":
            return _timeout_result(
                stage_label="code generation",
                outcome_method="codegen",
                path_taken="codegen",
            )

        if not builder_code and terminal_codegen_failure in {"no_output", "llm_error"}:
            _path_taken = "codegen"
            if terminal_codegen_failure == "no_output":
                user_error = (
                    f"Codegen returned empty response after {codegen_retries} retries. "
                    "The LLM did not produce any builder code."
                )
                record_gen_outcome(
                    "codegen",
                    success=False,
                    error_type="no_output",
                    pattern=workflow_id,
                )
            else:
                user_error = (
                    f"Codegen LLM call failed: {terminal_codegen_message}. "
                    "Try again or simplify the prompt."
                )
            events.append(
                ChatValidationResultEvent(
                    success=False,
                    error_count=1,
                    errors=[user_error],
                )
            )
            logger.info(
                "Generation completed in %.1fs",
                time.monotonic() - _gen_start,
            )
            events.append(_build_summary_event())
            return None, events

        if builder_code:
            events.append(
                ChatCodeGeneratedEvent(
                    code_snippet=builder_code[:500],
                    source="codegen",
                    metadata=(
                        {"codegen_retries": codegen_retries}
                        if codegen_retries
                        else {}
                    ),
                )
            )

            syntax_error: SyntaxError | None = None
            try:
                ast.parse(builder_code)
            except SyntaxError as se:
                syntax_error = se
                codegen_errors = [
                    GenerationError(
                        stage=GenerationStage.sandbox,
                        error_type=GenerationErrorType.syntax_error,
                        message=f"Syntax error: {se.msg}",
                        source_line=se.lineno,
                        recoverable=True,
                    ),
                ]
                events.append(
                    ChatValidationResultEvent(
                        success=False,
                        error_count=1,
                        errors=[f"Syntax error: {se.msg}"],
                    )
                )
                record_gen_outcome(
                    "codegen",
                    success=False,
                    error_type="syntax_error",
                    pattern=workflow_id,
                )

            if syntax_error is None:
                _emit_progress("sandbox validation")
                try:
                    graph_dict, sandbox_codegen = await _run_with_generation_budget(
                        sandbox_exec_builder_code(builder_code),
                        stage_label="sandbox validation",
                    )
                except asyncio.TimeoutError:
                    return _timeout_result(
                        stage_label="sandbox validation",
                        outcome_method="codegen",
                        path_taken="codegen",
                    )
                if graph_dict is not None:
                    acceptance = accept_candidate_graph(
                        graph_dict,
                        validate_graph=validate_codegen_output,
                        build_validation_event=_validation_event,
                        emit_event=events.append,
                        quality_error_for_graph=lambda candidate: _quality_error_for_graph(
                            candidate,
                            warning_message=(
                                "Graph quality %d below threshold %d, falling back to diagnosis"
                            ),
                        ),
                        fit_check=_fit_check,
                        record_gen_outcome=record_gen_outcome,
                        success_method="codegen",
                        failure_method="codegen",
                        failure_fix_needed=True,
                        pattern=workflow_id,
                    )
                    if acceptance.accepted_graph is not None:
                        _path_taken = "codegen"
                        events.append(_build_summary_event())
                        return acceptance.accepted_graph, events
                    codegen_errors = list(acceptance.errors)
                else:
                    sandbox_error = _sandbox_failure_error(sandbox_codegen)
                    retries_used["sandbox"] += 1
                    err_msg = sandbox_error.message
                    is_timeout = (
                        "timeout" in err_msg.lower()
                        or "timed out" in err_msg.lower()
                    )
                    if is_timeout:
                        is_execution_timeout = (
                            "execution" in err_msg.lower()
                            or "code" in err_msg.lower()
                            or "runtime" in err_msg.lower()
                        )
                        if is_execution_timeout:
                            logger.warning(
                                "Sandbox execution timeout — builder code likely has infinite loop, skipping retry"
                            )
                            codegen_errors = [
                                GenerationError(
                                    stage=GenerationStage.sandbox,
                                    error_type=GenerationErrorType.runtime_error,
                                    message=(
                                        "Builder code execution timed out (possible infinite loop)"
                                    ),
                                    recoverable=True,
                                ),
                            ]
                            record_gen_outcome(
                                "codegen",
                                success=False,
                                error_type="execution_timeout",
                                pattern=workflow_id,
                            )
                            events.append(
                                ChatValidationResultEvent(
                                    success=False,
                                    error_count=1,
                                    errors=[
                                        "Builder code execution timed out. The generated code may contain an infinite loop."
                                    ],
                                )
                            )
                        else:
                            logger.warning(
                                "Sandbox process startup timeout, retrying sandbox once"
                            )
                            await asyncio.sleep(2.0)
                            try:
                                graph_dict, sandbox_codegen = await _run_with_generation_budget(
                                    sandbox_exec_builder_code(builder_code),
                                    stage_label="sandbox validation retry",
                                )
                            except asyncio.TimeoutError:
                                return _timeout_result(
                                    stage_label="sandbox validation retry",
                                    outcome_method="codegen",
                                    path_taken="codegen",
                                )
                            if graph_dict is not None:
                                acceptance = accept_candidate_graph(
                                    graph_dict,
                                    validate_graph=validate_codegen_output,
                                    build_validation_event=_validation_event,
                                    emit_event=events.append,
                                    quality_error_for_graph=lambda candidate: _quality_error_for_graph(
                                        candidate,
                                        warning_message=(
                                            "Graph quality %d below threshold %d, falling back to diagnosis"
                                        ),
                                    ),
                                    fit_check=_fit_check,
                                    record_gen_outcome=record_gen_outcome,
                                    success_method="codegen",
                                    failure_method="codegen",
                                    failure_fix_needed=True,
                                    pattern=workflow_id,
                                )
                                if acceptance.accepted_graph is not None:
                                    _path_taken = "codegen"
                                    events.append(_build_summary_event())
                                    return acceptance.accepted_graph, events
                                codegen_errors = list(acceptance.errors)
                            else:
                                sandbox_error = _sandbox_failure_error(sandbox_codegen)
                                codegen_errors = [sandbox_error]
                                record_gen_outcome(
                                    "codegen",
                                    success=False,
                                    error_type=sandbox_error.error_type.value,
                                    pattern=workflow_id,
                                )
                                events.append(
                                    ChatValidationResultEvent(
                                        success=False,
                                        error_count=1,
                                        errors=[sandbox_error.message],
                                    )
                                )
                    else:
                        codegen_errors = [sandbox_error]
                        record_gen_outcome(
                            "codegen",
                            success=False,
                            error_type=sandbox_error.error_type.value,
                            pattern=workflow_id,
                        )
                        events.append(
                            ChatValidationResultEvent(
                                success=False,
                                error_count=1,
                                errors=[sandbox_error.message],
                            )
                        )

        if _deadline_exceeded():
            return _timeout_result(
                stage_label="diagnosis repair",
                outcome_method="diagnosis",
                path_taken="codegen",
            )

        if builder_code and codegen_errors:
            fallback_chain.append("diagnosis")
            error_summary = codegen_errors[0].message if codegen_errors else "unknown error"
            logger.info("Codegen: %s; attempting diagnosis repair", error_summary)

            _emit_progress("diagnosis repair")

            try:
                from dan.meta.diagnosis import (
                    DiagnosisLoop,
                    GenerationError,
                    generation_repair_attempt_budget,
                )

                diagnosis = DiagnosisLoop(
                    max_attempts=generation_repair_attempt_budget()
                )
                gen_errors = [
                    GenerationError(
                        stage=e.stage,
                        error_type=e.error_type,
                        message=e.message,
                        source_line=e.source_line,
                        recoverable=e.recoverable,
                    )
                    for e in codegen_errors
                ]

                async def _llm_complete(sys_prompt: str, user_prompt: str) -> str:
                    r = await _run_with_generation_budget(
                        provider.complete(
                            messages=[
                                {"role": "system", "content": sys_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                            model=_model,
                            temperature=0.3,
                        ),
                        stage_label="diagnosis repair",
                    )
                    return r.text or ""

                try:
                    diag_result = await _run_with_generation_budget(
                        diagnosis.diagnose_and_repair(
                            goal=user_message,
                            generated_code=builder_code,
                            errors=gen_errors,
                            llm_complete=_llm_complete,
                            graph_validator=_diagnosis_graph_validator,
                        ),
                        stage_label="diagnosis repair",
                    )
                except asyncio.TimeoutError:
                    return _timeout_result(
                        stage_label="diagnosis repair",
                        outcome_method="diagnosis",
                        path_taken="diagnosis",
                    )
                if diag_result.success and diag_result.final_graph:
                    acceptance = accept_candidate_graph(
                        diag_result.final_graph,
                        validate_graph=validate_codegen_output,
                        build_validation_event=_validation_event,
                        emit_event=events.append,
                        quality_error_for_graph=lambda candidate: _quality_error_for_graph(
                            candidate,
                            warning_message=(
                                "Graph quality %d below threshold %d, rejecting repaired graph"
                            ),
                        ),
                        fit_check=_fit_check,
                        record_gen_outcome=record_gen_outcome,
                        success_method="diagnosis",
                        failure_method="diagnosis",
                        failure_fix_needed=True,
                        pattern=workflow_id,
                    )
                    if acceptance.accepted_graph is not None:
                        _path_taken = "diagnosis_repair"
                        events.append(_build_summary_event())
                        return acceptance.accepted_graph, events
                    if _last_quality_score is not None and _last_quality_score <= 20:
                        logger.warning(
                            "Diagnosis produced consecutive low-quality graphs (score=%d), terminating",
                            _last_quality_score,
                        )
                        events.append(
                            ChatValidationResultEvent(
                                success=False,
                                error_count=1,
                                errors=[
                                    "Unable to generate a graph that meets quality requirements "
                                    "for this prompt. Try simplifying the request or breaking "
                                    "it into smaller workflows."
                                ],
                            )
                        )
                    _path_taken = "diagnosis_repair"
                    events.append(_build_summary_event())
                    return None, events
                if not diag_result.success and not _deadline_exceeded():
                    fallback_chain.append("automatic_recovery")
                    retries_used["automatic_recovery"] += 1
                    failure_lines = _automatic_recovery_failure_lines(
                        codegen_errors=codegen_errors,
                        diag_result=diag_result,
                    )
                    _automatic_recovery = {
                        "scope": "generation",
                        "workflow_id": workflow_id,
                        "run_id": "",
                        "node_id": "",
                        "status": "in_progress",
                        "category": str(
                            _last_build_provenance.get("failure_bucket")
                            or _final_failure_mode
                            or "generation_failure"
                        ),
                        "selected_action": "regenerate_builder_code",
                        "available_actions": ["regenerate_builder_code"],
                        "message": "Automatic generation recovery in progress.",
                        "failure_summary": "; ".join(failure_lines[:3])
                        or "Workflow generation exhausted diagnosis repair.",
                        "attempted_fixes": _diagnosis_attempt_payload(diag_result),
                        "attempt_index": retries_used["automatic_recovery"],
                        "max_attempts": 1,
                        "recovery_budget_remaining": max(
                            0,
                            1 - retries_used["automatic_recovery"],
                        ),
                        "last_action": "regenerate_builder_code",
                        "last_outcome": "scheduled",
                        "build_status": _last_build_provenance.get("build_status"),
                        "failure_bucket": _last_build_provenance.get("failure_bucket"),
                    }
                    events.append(
                        ChatNoticeEvent(
                            content="Auto-repairing workflow generation with improved failure context.",
                            level="info",
                        )
                    )
                    recovery_context = _build_automatic_recovery_context(
                        codegen_errors=codegen_errors,
                        diag_result=diag_result,
                        prior_code=diag_result.final_code or builder_code,
                    )
                    recovery_hint = get_generation_stats_hint()
                    if recovery_context:
                        recovery_hint = (
                            f"{recovery_hint}\n\n{recovery_context}"
                            if recovery_hint
                            else recovery_context
                        )
                    recovery_request = await request_builder_code(
                        provider=provider,
                        model=_model,
                        user_message=user_message,
                        intent_goal=intent.goal if intent else None,
                        gen_stats_hint=recovery_hint,
                        detected_domain=detected_domain,
                        complexity_tier=complexity_tier,
                        min_nodes=min_nodes,
                        max_nodes=max_nodes,
                        extract_code_from_response=extract_code_from_response,
                        retries_used=retries_used,
                        record_gen_outcome=record_gen_outcome,
                        pattern=workflow_id,
                        is_transient_llm_error=_is_transient_llm_error,
                        get_timeout_seconds=_remaining_seconds,
                        log=logger,
                    )
                    recovery_code = recovery_request.builder_code
                    recovery_retries = recovery_request.codegen_retries
                    recovery_terminal_failure = recovery_request.terminal_failure
                    recovery_terminal_message = recovery_request.terminal_message
                    recovery_errors: list[Any] = []

                    if recovery_terminal_failure == "generation_timeout":
                        return _timeout_result(
                            stage_label="automatic recovery code generation",
                            outcome_method="automatic_recovery",
                            path_taken="automatic_recovery",
                        )

                    if recovery_code:
                        recovery_metadata = {
                            "recovery_reason": "diagnosis_exhausted",
                            "recovery_attempt": retries_used["automatic_recovery"],
                        }
                        if recovery_retries:
                            recovery_metadata["codegen_retries"] = recovery_retries
                        events.append(
                            ChatCodeGeneratedEvent(
                                code_snippet=recovery_code[:500],
                                source="automatic_recovery",
                                metadata=recovery_metadata,
                            )
                        )
                        syntax_error: SyntaxError | None = None
                        try:
                            ast.parse(recovery_code)
                        except SyntaxError as se:
                            syntax_error = se
                            recovery_errors = [
                                GenerationError(
                                    stage=GenerationStage.sandbox,
                                    error_type=GenerationErrorType.syntax_error,
                                    message=f"Syntax error: {se.msg}",
                                    source_line=se.lineno,
                                    recoverable=True,
                                ),
                            ]
                            events.append(
                                ChatValidationResultEvent(
                                    success=False,
                                    error_count=1,
                                    errors=[f"Syntax error: {se.msg}"],
                                )
                            )
                            record_gen_outcome(
                                "automatic_recovery",
                                success=False,
                                error_type="syntax_error",
                                pattern=workflow_id,
                            )

                        if syntax_error is None:
                            _emit_progress("automatic recovery sandbox validation")
                            try:
                                graph_dict, sandbox_codegen = await _run_with_generation_budget(
                                    sandbox_exec_builder_code(recovery_code),
                                    stage_label="automatic recovery sandbox validation",
                                )
                            except asyncio.TimeoutError:
                                return _timeout_result(
                                    stage_label="automatic recovery sandbox validation",
                                    outcome_method="automatic_recovery",
                                    path_taken="automatic_recovery",
                                )
                            if graph_dict is not None:
                                acceptance = accept_candidate_graph(
                                    graph_dict,
                                    validate_graph=validate_codegen_output,
                                    build_validation_event=_validation_event,
                                    emit_event=events.append,
                                    quality_error_for_graph=lambda candidate: _quality_error_for_graph(
                                        candidate,
                                        warning_message=(
                                            "Graph quality %d below threshold %d, rejecting automatic recovery graph"
                                        ),
                                    ),
                                    fit_check=_fit_check,
                                    record_gen_outcome=record_gen_outcome,
                                    success_method="automatic_recovery",
                                    failure_method="automatic_recovery",
                                    failure_fix_needed=True,
                                    pattern=workflow_id,
                                )
                                if acceptance.accepted_graph is not None:
                                    _automatic_recovery = {
                                        **_automatic_recovery,
                                        "status": "completed",
                                        "message": "Automatic generation recovery completed.",
                                        "attempted_fixes": [
                                            *list(_automatic_recovery.get("attempted_fixes") or []),
                                            {
                                                "kind": "regenerate_builder_code",
                                                "outcome": "completed",
                                                "cause": "diagnosis_exhausted",
                                                "next_step": "Accepted regenerated workflow.",
                                            },
                                        ],
                                        "last_outcome": "completed",
                                    }
                                    _path_taken = "automatic_recovery"
                                    events.append(_build_summary_event())
                                    return acceptance.accepted_graph, events
                                recovery_errors = list(acceptance.errors)
                            else:
                                sandbox_error = _sandbox_failure_error(sandbox_codegen)
                                retries_used["sandbox"] += 1
                                recovery_errors = [sandbox_error]
                                record_gen_outcome(
                                    "automatic_recovery",
                                    success=False,
                                    error_type=sandbox_error.error_type.value,
                                    pattern=workflow_id,
                                )
                                events.append(
                                    ChatValidationResultEvent(
                                        success=False,
                                        error_count=1,
                                        errors=[sandbox_error.message],
                                    )
                                )
                    elif recovery_terminal_failure in {"no_output", "llm_error"}:
                        if recovery_terminal_failure == "no_output":
                            recovery_errors = [
                                GenerationError(
                                    stage=GenerationStage.sandbox,
                                    error_type=GenerationErrorType.no_output,
                                    message=(
                                        "Automatic recovery returned empty builder code after retries"
                                    ),
                                    recoverable=True,
                                )
                            ]
                            record_gen_outcome(
                                "automatic_recovery",
                                success=False,
                                error_type="no_output",
                                pattern=workflow_id,
                            )
                        else:
                            recovery_errors = [
                                GenerationError(
                                    stage=GenerationStage.sandbox,
                                    error_type=GenerationErrorType.runtime_error,
                                    message=(
                                        recovery_terminal_message
                                        or "Automatic recovery LLM call failed"
                                    ),
                                    recoverable=True,
                                )
                            ]
                            record_gen_outcome(
                                "automatic_recovery",
                                success=False,
                                error_type="llm_error",
                                pattern=workflow_id,
                            )

                    if recovery_errors:
                        _automatic_recovery = {
                            **_automatic_recovery,
                            "status": "exhausted",
                            "message": "Automatic generation recovery exhausted.",
                            "attempted_fixes": [
                                *list(_automatic_recovery.get("attempted_fixes") or []),
                                {
                                    "kind": "regenerate_builder_code",
                                    "outcome": "exhausted",
                                    "cause": "diagnosis_exhausted",
                                    "next_step": (
                                        "Surface validation failure after bounded automatic recovery."
                                    ),
                                },
                            ],
                            "last_outcome": "exhausted",
                            "handoff_reason": "automatic_recovery_exhausted",
                            "build_status": "repair_exhausted",
                            "failure_bucket": "automatic_repair_exhausted",
                            "escalation_needed": True,
                            "escalation_summary": (
                                "Bounded automatic generation recovery is exhausted. Retry with a narrower request or manually adjust the workflow intent."
                            ),
                            "recommended_actions": [
                                "simplify_request",
                                "retry_generation_with_narrower_scope",
                                "edit_workflow_prompt_or_graph",
                            ],
                        }
                        _path_taken = "automatic_recovery"
                        events.append(
                            ChatValidationResultEvent(
                                success=False,
                                error_count=min(5, len(recovery_errors)),
                                errors=[
                                    "Automatic repair exhausted after diagnosis."
                                ] + [
                                    str(getattr(err, "message", err) or "").strip()
                                    for err in recovery_errors[:4]
                                    if str(getattr(err, "message", err) or "").strip()
                                ],
                                build_status="repair_exhausted",
                                failure_bucket="automatic_repair_exhausted",
                                handoff_reason="automatic_recovery_exhausted",
                                build_summary=(
                                    "Attempted bounded diagnosis repair and one automatic regeneration "
                                    "with improved failure context."
                                ),
                                automatic_recovery=copy.deepcopy(_automatic_recovery),
                            )
                        )
                record_gen_outcome(
                    "diagnosis",
                    success=False,
                    error_type="repair_failed",
                    fix_needed=True,
                    pattern=workflow_id,
                )
            except Exception as exc:
                logger.debug("Diagnosis loop failed: %s", exc)

        if not any(
            isinstance(e, ChatValidationResultEvent) and not e.success
            for e in events
        ):
            events.append(
                ChatValidationResultEvent(
                    success=False,
                    error_count=1,
                    errors=["Workflow generation failed. No graph was produced."],
                )
            )

        _path_taken = _path_taken or (
            "codegen" if "codegen" in fallback_chain else "none"
        )
        logger.info("Generation completed in %.1fs", time.monotonic() - _gen_start)
        events.append(_build_summary_event())
        return None, events
