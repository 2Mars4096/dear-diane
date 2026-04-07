"""Reusable execution runtime for worker-core requests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from dan.worker.core.contracts import EvidenceBlock, ExecutionRequest
from dan.worker.core.interfaces import (
    CompletionProvider,
    CompletionRequest,
    EventSink,
    MemoryProvider,
    ToolCallRequest,
    ToolProvider,
)
from dan.worker.core.model import CompletionHints, WorkerDefinition


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, sort_keys=True, default=str)
    except Exception:
        return str(value)


@dataclass(slots=True)
class WorkerExecutionResult:
    """Normalized result returned by the worker core."""

    status: Literal["completed", "failed"]
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkerCoreExecutor:
    """Execute worker-core definitions against the normalized request contract."""

    def __init__(
        self,
        *,
        completion_provider: CompletionProvider | None = None,
        tool_provider: ToolProvider | None = None,
        memory_provider: MemoryProvider | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self._completion_provider = completion_provider
        self._tool_provider = tool_provider
        self._memory_provider = memory_provider
        self._event_sink = event_sink

    async def execute(
        self,
        worker: WorkerDefinition,
        request: ExecutionRequest,
    ) -> WorkerExecutionResult:
        effective_request = await self._hydrate_request(worker, request)
        await self._emit(
            "worker.started",
            {
                "worker_id": worker.id,
                "has_model": bool(worker.model),
                "tool_count": len(worker.tool_ids),
            },
        )

        if self._uses_completion(worker):
            result = await self._run_completion(worker, effective_request)
        elif worker.tool_ids:
            result = await self._run_direct_tool(worker, effective_request)
        else:
            result = WorkerExecutionResult(
                status="completed",
                outputs=self._passthrough_outputs(effective_request),
            )

        await self._emit(
            f"worker.{result.status}",
            {
                "worker_id": worker.id,
                "error": result.error,
            },
        )
        return result

    async def _hydrate_request(
        self,
        worker: WorkerDefinition,
        request: ExecutionRequest,
    ) -> ExecutionRequest:
        if self._memory_provider is None:
            return request
        extra_evidence = await self._memory_provider.get_evidence(worker, request)
        if not extra_evidence:
            return request
        return request.model_copy(
            update={"evidence": [*request.evidence, *extra_evidence]},
        )

    async def _run_completion(
        self,
        worker: WorkerDefinition,
        request: ExecutionRequest,
    ) -> WorkerExecutionResult:
        if self._completion_provider is None:
            return WorkerExecutionResult(
                status="failed",
                error=f"Worker '{worker.id}' requires a completion provider",
            )

        hints = worker.llm_hints or CompletionHints()
        completion_request = CompletionRequest(
            model=worker.model,
            system_prompt=self._build_system_prompt(worker, request, hints),
            user_prompt=self._build_user_prompt(request, hints),
            temperature=hints.temperature,
            max_tokens=hints.max_tokens,
            tools=list(worker.tool_ids),
            output_contract=request.output_contract,
            metadata={"worker_id": worker.id, **request.metadata},
        )
        response = await self._completion_provider.complete(completion_request)
        return WorkerExecutionResult(
            status="completed",
            outputs={
                "text": response.text,
                "result": response.text,
            },
            metadata={"raw_response": response.raw},
        )

    async def _run_direct_tool(
        self,
        worker: WorkerDefinition,
        request: ExecutionRequest,
    ) -> WorkerExecutionResult:
        if self._tool_provider is None:
            return WorkerExecutionResult(
                status="failed",
                error=f"Worker '{worker.id}' requires a tool provider",
            )
        if len(worker.tool_ids) != 1:
            return WorkerExecutionResult(
                status="failed",
                error=(
                    f"Worker '{worker.id}' has {len(worker.tool_ids)} tool refs but "
                    "direct tool execution requires exactly one tool"
                ),
            )

        call = ToolCallRequest(
            worker_id=worker.id,
            tool_id=worker.tool_ids[0],
            arguments=dict(request.input_payload or {"task": request.task}),
            request=request,
        )
        response = await self._tool_provider.call(call)
        return WorkerExecutionResult(
            status="completed",
            outputs={"result": response.output},
            metadata=dict(response.metadata),
        )

    @staticmethod
    def _uses_completion(worker: WorkerDefinition) -> bool:
        hints = worker.llm_hints
        return bool(worker.model) or (
            hints is not None
            and any([
                bool(hints.system_prompt),
                bool(hints.prompt_template),
                hints.max_tokens is not None,
            ])
        )

    @staticmethod
    def _build_system_prompt(
        worker: WorkerDefinition,
        request: ExecutionRequest,
        hints: CompletionHints,
    ) -> str:
        parts: list[str] = []
        if hints.system_prompt:
            parts.append(hints.system_prompt)
        if worker.role:
            parts.append(f"Role: {worker.role}")
        if worker.persona:
            parts.append(worker.persona)
        if worker.instruction:
            parts.append(worker.instruction)
        if request.output_contract.definition_of_done:
            parts.append(f"Definition of done:\n{request.output_contract.definition_of_done}")
        if request.output_contract.expected_return_shape:
            parts.append(f"Expected return shape:\n{request.output_contract.expected_return_shape}")
        return "\n\n".join(part for part in parts if part)

    @staticmethod
    def _build_user_prompt(
        request: ExecutionRequest,
        hints: CompletionHints,
    ) -> str:
        sections: list[str] = []
        if hints.prompt_template:
            format_values = dict(request.input_payload)
            format_values.setdefault("task", request.task)
            format_values.setdefault("input", request.input_payload.get("input", request.task))
            try:
                sections.append(hints.prompt_template.format(**format_values))
            except Exception:
                sections.append(hints.prompt_template)
        else:
            sections.append(f"Task:\n{request.task}")

        if request.constraints.scope:
            sections.append(f"Scope:\n{request.constraints.scope}")
        if request.constraints.hard_constraints:
            sections.append(
                "Hard constraints:\n" + "\n".join(f"- {item}" for item in request.constraints.hard_constraints)
            )
        if request.constraints.soft_constraints:
            sections.append(
                "Soft constraints:\n" + "\n".join(f"- {item}" for item in request.constraints.soft_constraints)
            )
        if request.evidence:
            sections.append(
                "Evidence:\n" + "\n\n".join(WorkerCoreExecutor._render_evidence_block(block) for block in request.evidence)
            )
        if request.input_payload:
            sections.append(f"Input payload:\n{_stringify(request.input_payload)}")
        return "\n\n".join(part for part in sections if part)

    @staticmethod
    def _render_evidence_block(block: EvidenceBlock) -> str:
        source = f" ({block.source})" if block.source else ""
        return f"[{block.trust_label.value}] {block.label}{source}\n{_stringify(block.content)}"

    @staticmethod
    def _passthrough_outputs(request: ExecutionRequest) -> dict[str, Any]:
        if request.input_payload:
            return {"result": dict(request.input_payload)}
        return {"result": request.task}

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._event_sink is None:
            return
        await self._event_sink.record(event, payload)
