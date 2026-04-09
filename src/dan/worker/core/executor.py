"""Reusable execution runtime for worker-core requests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from dan.worker.core.acquisition import (
    build_ref_id,
    ensure_default_sources,
    expansion_limit,
    family_name,
    merge_acquisition_policy,
    select_catalog_items,
    selection_limit,
)
from dan.worker.core.capabilities import CapabilityManifest, capability_manifest_from_descriptor
from dan.worker.core.contracts import (
    AcquisitionFamily,
    AcquisitionSelection,
    AcquisitionSource,
    AcquisitionStage,
    ContinuationPayload,
    DiscoveryCatalog,
    EvidenceBlock,
    ExecutionRequest,
    ExpandedContext,
    MemoryLayer,
    MemoryExtractionMode,
    MemorySnapshot,
)
from dan.worker.core.interfaces import (
    AcquisitionProvider,
    CompletionProvider,
    CompletionRequest,
    EventSink,
    ExpansionRequest,
    MemoryProvider,
    ToolCallRequest,
    ToolProvider,
)
from dan.worker.core.memory import build_memory_sources, working_memory_evidence
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
    continuation: ContinuationPayload | None = None


@dataclass(slots=True)
class _AcquisitionResult:
    request: ExecutionRequest
    continuation: ContinuationPayload | None


@dataclass(slots=True)
class _HydrationResult:
    request: ExecutionRequest
    memory_snapshot: MemorySnapshot | None = None


class WorkerCoreExecutor:
    """Execute worker-core definitions against the normalized request contract."""

    def __init__(
        self,
        *,
        completion_provider: CompletionProvider | None = None,
        tool_provider: ToolProvider | None = None,
        memory_provider: MemoryProvider | None = None,
        acquisition_provider: AcquisitionProvider | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self._completion_provider = completion_provider
        self._tool_provider = tool_provider
        self._memory_provider = memory_provider
        self._acquisition_provider = acquisition_provider
        self._event_sink = event_sink

    async def execute(
        self,
        worker: WorkerDefinition,
        request: ExecutionRequest,
    ) -> WorkerExecutionResult:
        tool_ids = self._effective_tool_ids(worker, request)
        hydration = await self._hydrate_request(worker, request)
        effective_request = hydration.request
        acquisition_result = await self._acquire_context(worker, effective_request)
        effective_request = acquisition_result.request

        await self._emit(
            "worker.started",
            {
                "worker_id": worker.id,
                "has_model": bool(worker.model),
                "tool_count": len(tool_ids),
                "acquisition_source_count": len(
                    effective_request.continuation.catalogs if effective_request.continuation is not None else []
                ),
                "selected_ref_count": len(
                    effective_request.continuation.selections if effective_request.continuation is not None else []
                ),
                "expanded_ref_count": len(
                    effective_request.continuation.expanded_context
                    if effective_request.continuation is not None
                    else []
                ),
            },
        )

        if self._uses_completion(worker):
            result = await self._run_completion(worker, effective_request)
        elif tool_ids:
            result = await self._run_direct_tool(worker, effective_request)
        else:
            result = WorkerExecutionResult(
                status="completed",
                outputs=self._passthrough_outputs(effective_request),
            )

        result.continuation = acquisition_result.continuation
        memory_snapshot = await self._persist_memory(
            worker,
            effective_request,
            result,
            fallback_snapshot=hydration.memory_snapshot,
        )
        result.metadata = {
            **result.metadata,
            "acquisition": self._summarize_acquisition(acquisition_result.continuation),
            "memory": self._summarize_memory(memory_snapshot, acquisition_result.continuation),
        }

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
    ) -> _HydrationResult:
        evidence = list(request.evidence)
        acquisition_sources = list(request.acquisition.sources)
        memory_snapshot: MemorySnapshot | None = None
        extraction_mode = request.memory.extraction_mode
        if (
            self._memory_provider is not None
            and hasattr(self._memory_provider, "recall")
            and request.memory.reuse_memory
            and extraction_mode != MemoryExtractionMode.NONE
        ):
            memory_snapshot = await self._memory_provider.recall(worker, request)
            if (
                request.memory.inline_working_memory
                and extraction_mode
                in {
                    MemoryExtractionMode.WORKING_ONLY,
                    MemoryExtractionMode.WORKING_AND_CATALOG,
                }
            ):
                evidence.extend(working_memory_evidence(memory_snapshot))
            if extraction_mode in {
                MemoryExtractionMode.CATALOG_ONLY,
                MemoryExtractionMode.WORKING_AND_CATALOG,
            }:
                acquisition_sources = [
                    *build_memory_sources(
                        worker.id,
                        memory_snapshot,
                        include_layers={MemoryLayer.EPISODIC, MemoryLayer.RETAINED},
                    ),
                    *acquisition_sources,
                ]
        elif (
            self._memory_provider is not None
            and hasattr(self._memory_provider, "get_evidence")
            and request.memory.reuse_memory
            and extraction_mode != MemoryExtractionMode.NONE
        ):
            extra_evidence = await self._memory_provider.get_evidence(worker, request)
            if extra_evidence:
                evidence.extend(extra_evidence)
        normalized_evidence = self._normalize_evidence_blocks(evidence)
        resolved_acquisition = request.acquisition.model_copy(update={"sources": acquisition_sources})
        if (
            normalized_evidence == list(request.evidence)
            and resolved_acquisition == request.acquisition
        ):
            return _HydrationResult(request=request, memory_snapshot=memory_snapshot)
        return _HydrationResult(
            request=request.model_copy(
                update={
                    "evidence": normalized_evidence,
                    "acquisition": resolved_acquisition,
                }
            ),
            memory_snapshot=memory_snapshot,
        )

    async def _acquire_context(
        self,
        worker: WorkerDefinition,
        request: ExecutionRequest,
    ) -> _AcquisitionResult:
        policy = merge_acquisition_policy(worker.acquisition_policy, request.acquisition.policy)
        initial_continuation = (
            request.continuation.model_copy(deep=True)
            if request.continuation is not None and policy.reuse_continuation
            else ContinuationPayload()
        )
        continuation = self._seed_continuation(request, initial_continuation)

        tool_ids = self._effective_tool_ids(worker, request)
        sources = ensure_default_sources(worker.id, tool_ids, list(request.acquisition.sources))
        sources = self._apply_tooling_contract_to_sources(sources, tool_ids)
        sources = self._merge_continuation_sources(sources, continuation)

        if not sources and not continuation.expanded_context:
            continuation.stage = AcquisitionStage.ACT
            return _AcquisitionResult(
                request=request.model_copy(update={"continuation": continuation}),
                continuation=continuation,
            )

        catalogs_by_key = {
            self._catalog_key(catalog.source_id, catalog.family): catalog for catalog in continuation.catalogs
        }
        if self._acquisition_provider is not None:
            continuation.stage = AcquisitionStage.DISCOVER
            for source in sources:
                key = self._catalog_key(source.source_id, family_name(source.family))
                if key in catalogs_by_key:
                    continue
                response = await self._acquisition_provider.discover(
                    request=self._discovery_request(
                        worker_id=worker.id,
                        request=request,
                        source=source,
                        continuation=continuation,
                        policy=policy,
                    )
                )
                catalogs_by_key[key] = response.catalog
                await self._emit(
                    "worker.acquisition.discovered",
                    {
                        "worker_id": worker.id,
                        "source_id": source.source_id,
                        "family": family_name(source.family),
                        "item_count": len(response.catalog.items),
                        "cursor": response.catalog.cursor,
                        "truncated": response.catalog.truncated,
                    },
                )
        continuation.catalogs = list(catalogs_by_key.values())

        continuation.stage = AcquisitionStage.SELECT
        selections_by_ref = (
            {selection.ref_id: selection for selection in continuation.selections}
            if policy.reuse_continuation
            else {}
        )
        for selection in request.acquisition.selections:
            selections_by_ref[selection.ref_id] = selection.model_copy(update={"selected_by": "request"})

        for source in sources:
            family = family_name(source.family)
            catalog = catalogs_by_key.get(self._catalog_key(source.source_id, family))
            if catalog is None or not policy.auto_select:
                continue
            existing_count = sum(
                1
                for selection in selections_by_ref.values()
                if selection.source_id == source.source_id and selection.family == family
            )
            if existing_count >= selection_limit(source, policy):
                continue
            preferred_ids = (
                set(self._preferred_tool_ids(worker, request))
                if family == AcquisitionFamily.TOOL_CATALOG.value
                else set()
            )
            auto_selected = select_catalog_items(
                task=request.task,
                source=source,
                catalog=catalog,
                policy=policy,
                preferred_item_ids=preferred_ids,
                excluded_refs=set(selections_by_ref),
            )
            added: list[AcquisitionSelection] = []
            for selection in auto_selected:
                if selection.ref_id in selections_by_ref:
                    continue
                added.append(selection)
                selections_by_ref[selection.ref_id] = selection
                existing_count += 1
                if existing_count >= selection_limit(source, policy):
                    break
            if added:
                await self._emit(
                    "worker.acquisition.selected",
                    {
                        "worker_id": worker.id,
                        "source_id": source.source_id,
                        "family": family,
                        "refs": [selection.ref_id for selection in added],
                        "reasons": {selection.ref_id: selection.reason for selection in added},
                    },
                )
        continuation.selections = list(selections_by_ref.values())

        continuation.stage = AcquisitionStage.EXPAND
        expanded_by_ref = {
            context.ref_id: context for context in continuation.expanded_context if context.ref_id
        }
        if self._acquisition_provider is not None and policy.auto_expand:
            for source in sources:
                family = family_name(source.family)
                source_selections = [
                    selection
                    for selection in continuation.selections
                    if selection.source_id == source.source_id and selection.family == family
                ]
                if not source_selections:
                    continue
                existing_count = sum(
                    1
                    for context in expanded_by_ref.values()
                    if context.source_id == source.source_id and context.family == family
                )
                remaining = max(expansion_limit(source, policy) - existing_count, 0)
                if remaining <= 0:
                    continue
                missing = [
                    selection
                    for selection in source_selections
                    if selection.ref_id not in expanded_by_ref
                ][:remaining]
                if not missing:
                    continue
                catalog = catalogs_by_key.get(self._catalog_key(source.source_id, family))
                response = await self._acquisition_provider.expand(
                    ExpansionRequest(
                        worker_id=worker.id,
                        request=request,
                        source=source,
                        selections=missing,
                        catalog=catalog,
                        policy=policy,
                        continuation=continuation,
                    )
                )
                for context in response.expanded_context:
                    expanded_by_ref[context.ref_id] = context
                await self._emit(
                    "worker.acquisition.expanded",
                    {
                        "worker_id": worker.id,
                        "source_id": source.source_id,
                        "family": family,
                        "refs": [context.ref_id for context in response.expanded_context],
                    },
                )
        continuation.expanded_context = self._ordered_expanded_context(
            request=request,
            selections=continuation.selections,
            expanded_by_ref=expanded_by_ref,
        )
        continuation.stage = AcquisitionStage.ACT
        continuation.task_state = {
            **continuation.task_state,
            "task": request.task,
            "input_payload_keys": sorted(request.input_payload.keys()),
            "selected_refs": [selection.ref_id for selection in continuation.selections],
        }

        effective_request = request.model_copy(
            update={
                "evidence": self._ordered_evidence(request, continuation),
                "continuation": continuation,
            }
        )
        return _AcquisitionResult(request=effective_request, continuation=continuation)

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
        capabilities = self._selected_capabilities(
            request,
            self._effective_tool_ids(worker, request),
        )
        completion_request = CompletionRequest(
            model=worker.model,
            system_prompt=self._build_system_prompt(worker, request, hints),
            user_prompt=self._build_user_prompt(request, hints),
            temperature=hints.temperature,
            max_tokens=hints.max_tokens,
            tools=[capability.to_tool_schema() for capability in capabilities],
            capabilities=capabilities,
            evidence=list(request.evidence),
            continuation=request.continuation,
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
        if request.tooling.max_tool_calls == 0:
            return WorkerExecutionResult(
                status="failed",
                error=f"Worker '{worker.id}' is not allowed to call tools for this request",
            )
        tool_ids = self._effective_tool_ids(worker, request)
        if len(tool_ids) != 1:
            return WorkerExecutionResult(
                status="failed",
                error=(
                    f"Worker '{worker.id}' has {len(tool_ids)} tool refs but "
                    "direct tool execution requires exactly one tool"
                ),
            )

        call = ToolCallRequest(
            worker_id=worker.id,
            tool_id=tool_ids[0],
            arguments=dict(request.input_payload or {"task": request.task}),
            contract=request.tooling.model_copy(deep=True),
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
        if request.continuation is not None and request.continuation.catalogs:
            sections.append(
                "Discovered context catalogs:\n"
                + "\n\n".join(WorkerCoreExecutor._render_catalog(catalog) for catalog in request.continuation.catalogs)
            )
        if request.continuation is not None and request.continuation.selections:
            sections.append(
                "Selected refs:\n"
                + "\n".join(
                    f"- {selection.ref_id}: {selection.reason or 'selected'}"
                    for selection in request.continuation.selections
                )
            )
        if request.evidence:
            sections.append(
                "Evidence:\n" + "\n\n".join(WorkerCoreExecutor._render_evidence_block(block) for block in request.evidence)
            )
        if request.input_payload:
            sections.append(f"Input payload:\n{_stringify(request.input_payload)}")
        return "\n\n".join(part for part in sections if part)

    @staticmethod
    def _render_catalog(catalog: DiscoveryCatalog) -> str:
        lines = [
            f"{catalog.family}::{catalog.source_id}",
            f"Summary: {catalog.summary or f'{len(catalog.items)} items'}",
        ]
        for item in catalog.items:
            summary = f" - {item.summary}" if item.summary else ""
            lines.append(f"- {item.ref_id}: {item.title}{summary}")
        if catalog.cursor:
            lines.append(f"Cursor: {catalog.cursor}")
        return "\n".join(lines)

    @staticmethod
    def _render_evidence_block(block: EvidenceBlock) -> str:
        source = f" ({block.source})" if block.source else ""
        ref = f" <{block.ref_id}>" if block.ref_id else ""
        return f"[{block.trust_label.value}] {block.label}{source}{ref}\n{_stringify(block.content)}"

    @staticmethod
    def _effective_tool_ids(
        worker: WorkerDefinition,
        request: ExecutionRequest,
    ) -> list[str]:
        return list(request.tooling.allowed_tool_ids or worker.tool_ids)

    @staticmethod
    def _preferred_tool_ids(
        worker: WorkerDefinition,
        request: ExecutionRequest,
    ) -> list[str]:
        return list(request.tooling.preferred_tool_ids or WorkerCoreExecutor._effective_tool_ids(worker, request))

    @staticmethod
    def _apply_tooling_contract_to_sources(
        sources: list[AcquisitionSource],
        tool_ids: list[str],
    ) -> list[AcquisitionSource]:
        if not tool_ids:
            return list(sources)
        allowed_tool_ids = set(tool_ids)
        constrained: list[AcquisitionSource] = []
        for source in sources:
            if family_name(source.family) != AcquisitionFamily.TOOL_CATALOG.value:
                constrained.append(source)
                continue
            metadata = dict(source.metadata)
            metadata["tool_ids"] = list(tool_ids)
            if "tools" in metadata:
                metadata["tools"] = WorkerCoreExecutor._filter_tool_catalog_entries(
                    metadata["tools"],
                    allowed_tool_ids,
                )
            selectors = [selector for selector in source.selectors if selector in allowed_tool_ids]
            if not selectors:
                selectors = list(tool_ids)
            constrained.append(
                source.model_copy(
                    update={
                        "selectors": selectors,
                        "metadata": metadata,
                    }
                )
            )
        return constrained

    @staticmethod
    def _filter_tool_catalog_entries(
        raw_catalog: Any,
        allowed_tool_ids: set[str],
    ) -> Any:
        if isinstance(raw_catalog, dict):
            return {
                tool_id: payload
                for tool_id, payload in raw_catalog.items()
                if str(tool_id) in allowed_tool_ids
            }
        if isinstance(raw_catalog, list):
            filtered: list[Any] = []
            for entry in raw_catalog:
                if isinstance(entry, str):
                    if entry in allowed_tool_ids:
                        filtered.append(entry)
                    continue
                if isinstance(entry, CapabilityManifest):
                    if entry.capability_id in allowed_tool_ids:
                        filtered.append(entry)
                    continue
                if isinstance(entry, dict):
                    tool_id = str(entry.get("id") or entry.get("name") or "").strip()
                    if tool_id in allowed_tool_ids:
                        filtered.append(entry)
                    continue
            return filtered
        return raw_catalog

    @staticmethod
    def _passthrough_outputs(request: ExecutionRequest) -> dict[str, Any]:
        if request.input_payload:
            return {"result": dict(request.input_payload)}
        return {"result": request.task}

    @staticmethod
    def _catalog_key(source_id: str, family: str) -> tuple[str, str]:
        return source_id, family

    @staticmethod
    def _discovery_request(
        *,
        worker_id: str,
        request: ExecutionRequest,
        source: AcquisitionSource,
        continuation: ContinuationPayload,
        policy,
    ):
        from dan.worker.core.interfaces import DiscoveryRequest

        return DiscoveryRequest(
            worker_id=worker_id,
            request=request,
            source=source,
            policy=policy,
            continuation=continuation,
        )

    @staticmethod
    def _normalize_evidence_blocks(evidence: list[EvidenceBlock]) -> list[EvidenceBlock]:
        normalized: list[EvidenceBlock] = []
        for index, block in enumerate(evidence):
            ref_id = block.ref_id or build_ref_id(
                AcquisitionFamily.EVIDENCE_SOURCE,
                "inline-evidence",
                str(index),
            )
            metadata = {
                "source_id": "inline-evidence",
                "family": AcquisitionFamily.EVIDENCE_SOURCE.value,
                **dict(block.metadata),
            }
            normalized.append(
                block.model_copy(
                    update={
                        "ref_id": ref_id,
                        "metadata": metadata,
                    }
                )
            )
        return normalized

    @staticmethod
    def _seed_continuation(
        request: ExecutionRequest,
        continuation: ContinuationPayload,
    ) -> ContinuationPayload:
        expanded_by_ref = {
            context.ref_id: context for context in continuation.expanded_context if context.ref_id
        }
        for block in request.evidence:
            if not block.ref_id or block.ref_id in expanded_by_ref:
                continue
            expanded_by_ref[block.ref_id] = ExpandedContext(
                ref_id=block.ref_id,
                source_id=str(block.metadata.get("source_id") or "inline-evidence"),
                family=str(block.metadata.get("family") or AcquisitionFamily.EVIDENCE_SOURCE.value),
                title=block.label,
                content=block.content,
                summary=block.label,
                trust_label=block.trust_label,
                source=block.source,
                metadata=dict(block.metadata),
            )
        continuation.expanded_context = list(expanded_by_ref.values())
        return continuation

    @staticmethod
    def _merge_continuation_sources(
        sources: list[AcquisitionSource],
        continuation: ContinuationPayload,
    ) -> list[AcquisitionSource]:
        merged = list(sources)
        seen = {
            WorkerCoreExecutor._catalog_key(source.source_id, family_name(source.family))
            for source in merged
        }
        for catalog in continuation.catalogs:
            key = WorkerCoreExecutor._catalog_key(catalog.source_id, catalog.family)
            if key in seen:
                continue
            merged.append(
                AcquisitionSource(
                    source_id=catalog.source_id,
                    family=catalog.family,
                    label=catalog.summary,
                )
            )
            seen.add(key)
        return merged

    @staticmethod
    def _ordered_expanded_context(
        *,
        request: ExecutionRequest,
        selections: list[AcquisitionSelection],
        expanded_by_ref: dict[str, ExpandedContext],
    ) -> list[ExpandedContext]:
        ordered: list[ExpandedContext] = []
        seen: set[str] = set()
        for block in request.evidence:
            if block.ref_id and block.ref_id in expanded_by_ref and block.ref_id not in seen:
                ordered.append(expanded_by_ref[block.ref_id])
                seen.add(block.ref_id)
        for selection in selections:
            if selection.ref_id in expanded_by_ref and selection.ref_id not in seen:
                ordered.append(expanded_by_ref[selection.ref_id])
                seen.add(selection.ref_id)
        for ref_id, context in expanded_by_ref.items():
            if ref_id not in seen:
                ordered.append(context)
        return ordered

    @staticmethod
    def _ordered_evidence(
        request: ExecutionRequest,
        continuation: ContinuationPayload,
    ) -> list[EvidenceBlock]:
        ordered: list[EvidenceBlock] = []
        seen: set[str] = set()
        for block in request.evidence:
            if not block.ref_id or block.ref_id in seen:
                continue
            ordered.append(block)
            seen.add(block.ref_id)
        for selection in continuation.selections:
            if selection.ref_id in seen:
                continue
            expanded = next(
                (context for context in continuation.expanded_context if context.ref_id == selection.ref_id),
                None,
            )
            if expanded is None:
                continue
            ordered.append(expanded.as_evidence_block())
            seen.add(selection.ref_id)
        for expanded in continuation.expanded_context:
            if expanded.ref_id in seen:
                continue
            ordered.append(expanded.as_evidence_block())
            seen.add(expanded.ref_id)
        return ordered

    @staticmethod
    def _selected_capabilities(
        request: ExecutionRequest,
        fallback_tool_ids: list[str],
    ) -> list[CapabilityManifest]:
        allowed_tool_ids = set(fallback_tool_ids)
        if request.continuation is None:
            return [capability_manifest_from_descriptor(tool_id) for tool_id in fallback_tool_ids]

        capabilities: list[CapabilityManifest] = []
        seen: set[str] = set()
        for context in request.continuation.expanded_context:
            if context.family != AcquisitionFamily.TOOL_CATALOG.value:
                continue
            try:
                capability = CapabilityManifest.model_validate(context.content)
            except Exception:
                tool_id = str(context.metadata.get("tool_id") or context.ref_id.rsplit(":", 1)[-1])
                capability = capability_manifest_from_descriptor(
                    tool_id,
                    context.content if isinstance(context.content, dict) else {"id": tool_id, "details": _stringify(context.content)},
                )
            if allowed_tool_ids and capability.capability_id not in allowed_tool_ids:
                continue
            if capability.capability_id in seen:
                continue
            seen.add(capability.capability_id)
            capabilities.append(capability)

        if request.tooling.require_manifest_selection and capabilities:
            return capabilities

        for tool_id in fallback_tool_ids:
            if tool_id in seen:
                continue
            seen.add(tool_id)
            capabilities.append(capability_manifest_from_descriptor(tool_id))
        return capabilities

    @staticmethod
    def _summarize_acquisition(continuation: ContinuationPayload | None) -> dict[str, Any]:
        if continuation is None:
            return {
                "stage": AcquisitionStage.ACT.value,
                "catalog_sources": [],
                "selected_refs": [],
                "expanded_refs": [],
            }
        return {
            "stage": continuation.stage.value,
            "catalog_sources": [
                {
                    "source_id": catalog.source_id,
                    "family": catalog.family,
                    "count": len(catalog.items),
                    "cursor": catalog.cursor,
                }
                for catalog in continuation.catalogs
            ],
            "selected_refs": [selection.ref_id for selection in continuation.selections],
            "expanded_refs": [context.ref_id for context in continuation.expanded_context],
        }

    @staticmethod
    def _summarize_memory(
        snapshot: MemorySnapshot | None,
        continuation: ContinuationPayload | None,
    ) -> dict[str, Any]:
        if snapshot is None:
            selected_memory_refs = []
        else:
            selected_memory_refs = [
                selection.ref_id
                for selection in (continuation.selections if continuation is not None else [])
                if selection.family == AcquisitionFamily.MEMORY_CATALOG.value
            ]
        if snapshot is None:
            return {
                "working_refs": [],
                "episodic_refs": [],
                "retained_refs": [],
                "selected_memory_refs": selected_memory_refs,
            }
        return {
            "working_refs": [record.ref_id for record in snapshot.working_memory],
            "episodic_refs": [record.ref_id for record in snapshot.episodic_memory],
            "retained_refs": [record.ref_id for record in snapshot.retained_memory],
            "selected_memory_refs": selected_memory_refs,
        }

    async def _persist_memory(
        self,
        worker: WorkerDefinition,
        request: ExecutionRequest,
        result: WorkerExecutionResult,
        *,
        fallback_snapshot: MemorySnapshot | None = None,
    ) -> MemorySnapshot | None:
        if self._memory_provider is None or not hasattr(self._memory_provider, "persist"):
            return fallback_snapshot
        persisted = await self._memory_provider.persist(
            worker,
            request,
            dict(result.outputs),
            status=result.status,
            continuation=result.continuation,
        )
        return persisted or fallback_snapshot

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._event_sink is None:
            return
        await self._event_sink.record(event, payload)
