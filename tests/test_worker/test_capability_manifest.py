from __future__ import annotations

import pytest

from diane.worker.core.acquisition import LocalAcquisitionProvider
from diane.worker.core.capabilities import CapabilityCostTier, CapabilityManifest
from diane.worker.core.contracts import AcquisitionFamily, AcquisitionPolicy, AcquisitionSource, ExecutionRequest
from diane.worker.core.executor import WorkerCoreExecutor
from diane.worker.core.interfaces import CompletionRequest, CompletionResponse
from diane.worker.core.model import WorkerDefinition


class _RecordingCompletionProvider:
    def __init__(self) -> None:
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        return CompletionResponse(text="done", raw=None)


async def _run_manifest_completion() -> CompletionRequest:
    completion_provider = _RecordingCompletionProvider()
    acquisition_provider = LocalAcquisitionProvider(
        tool_catalogs={
            "tools": [
                {
                    "id": "file_read",
                    "title": "Read file",
                    "summary": "Read one file from the workspace.",
                    "details": "Reads UTF-8 file content with optional line ranges.",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    "returns": "file text",
                    "category": "file",
                },
                {
                    "id": "web_search",
                    "title": "Search web",
                    "summary": "Look up current public information.",
                    "details": "Queries configured web providers and returns ranked snippets.",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                    "returns": "search results",
                    "category": "web",
                },
            ]
        }
    )
    worker = WorkerDefinition(
        id="researcher",
        model="stub-model",
        tool_ids=["file_read", "web_search"],
        acquisition_policy=AcquisitionPolicy(
            max_selected_items_per_source=2,
            max_expanded_items_per_source=2,
        ),
    )
    request = ExecutionRequest.from_harness(
        task="Read a file and then search the web for a confirmation.",
        acquisition={
            "sources": [
                AcquisitionSource(
                    source_id="tools",
                    family=AcquisitionFamily.TOOL_CATALOG,
                )
            ]
        },
    )

    await WorkerCoreExecutor(
        completion_provider=completion_provider,
        acquisition_provider=acquisition_provider,
    ).execute(worker, request)

    return completion_provider.requests[0]


def test_tool_catalog_discovery_keeps_compact_manifest_metadata() -> None:
    provider = LocalAcquisitionProvider(
        tool_catalogs={
            "tools": [
                {
                    "id": "file_read",
                    "summary": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    "returns": "text",
                    "category": "file",
                },
                {
                    "id": "web_search",
                    "summary": "Search public sources",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                    "returns": "results",
                    "category": "web",
                },
            ]
        }
    )
    request = type(
        "Request",
        (),
        {
            "source": AcquisitionSource(source_id="tools", family=AcquisitionFamily.TOOL_CATALOG),
            "policy": AcquisitionPolicy(),
            "request": ExecutionRequest.from_harness(task="List available capabilities."),
        },
    )()

    catalog = provider._discover_tools(request)

    assert [item.item_id for item in catalog.items] == ["file_read", "web_search"]
    assert catalog.items[0].metadata["family"] == "file"
    assert catalog.items[0].metadata["permission_scope"] == ["read"]
    assert catalog.items[1].metadata["side_effects"] == ["network"]
    assert "input_schema" not in catalog.items[0].metadata


@pytest.mark.asyncio
async def test_completion_request_receives_selected_capability_manifests() -> None:
    completion_request = await _run_manifest_completion()

    manifests = completion_request.capabilities

    assert [manifest.capability_id for manifest in manifests] == ["file_read", "web_search"]
    assert isinstance(manifests[0], CapabilityManifest)
    assert manifests[0].family == "file"
    assert manifests[0].cost_tier == CapabilityCostTier.LOW
    assert manifests[0].output_contract == "file text"
    assert manifests[1].family == "web"
    assert manifests[1].cost_tier == CapabilityCostTier.MEDIUM
    assert [tool["function"]["name"] for tool in completion_request.tools] == ["file_read", "web_search"]
