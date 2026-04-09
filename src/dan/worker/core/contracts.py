"""DAN-independent worker-core contracts.

The reusable core accepts normalized execution requests instead of DAN-specific
prompt plumbing. Callers provide task, constraints, evidence, trust labels, and
an output contract; the core decides how to execute that request.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field


class TrustLabel(str, Enum):
    """Weighting hints for evidence blocks carried into execution."""

    AUTHORITATIVE = "authoritative"
    ADVISORY = "advisory"
    RETRIEVED = "retrieved"
    HISTORICAL = "historical"
    USER_PREFERENCE = "user-preference"


class ConstraintSet(BaseModel):
    """Normalized scope and execution constraints for a task."""

    scope: str | None = None
    hard_constraints: list[str] = Field(default_factory=list)
    soft_constraints: list[str] = Field(default_factory=list)


class EvidenceBlock(BaseModel):
    """Caller-supplied context block with explicit provenance weight."""

    label: str
    content: Any
    trust_label: TrustLabel = TrustLabel.RETRIEVED
    source: str | None = None
    ref_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AcquisitionFamily(str, Enum):
    """Compact evidence families supported by the worker-core membrane."""

    TOOL_CATALOG = "tool_catalog"
    FILE_INVENTORY = "file_inventory"
    MEMORY_CATALOG = "memory_catalog"
    EVIDENCE_SOURCE = "evidence_source"


class AcquisitionStage(str, Enum):
    """Lifecycle stages for progressive context acquisition."""

    DISCOVER = "discover"
    SELECT = "select"
    EXPAND = "expand"
    ACT = "act"


class AcquisitionPolicy(BaseModel):
    """Budget and policy limits for staged context acquisition."""

    discover_limit: int = Field(default=25, ge=1)
    max_selected_items_per_source: int = Field(default=3, ge=0)
    max_expanded_items_per_source: int = Field(default=3, ge=0)
    max_expanded_content_chars: int | None = Field(default=4000, ge=1)
    auto_select: bool = True
    auto_expand: bool = True
    reuse_continuation: bool = True


class AcquisitionSource(BaseModel):
    """A discoverable context source available to the worker."""

    source_id: str
    family: AcquisitionFamily | str
    label: str = ""
    query: str | None = None
    selectors: list[str] = Field(default_factory=list)
    discover_limit: int | None = Field(default=None, ge=1)
    max_selected_items: int | None = Field(default=None, ge=0)
    max_expanded_items: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiscoveryItem(BaseModel):
    """Compact catalog entry returned by the discovery stage."""

    ref_id: str
    source_id: str
    family: str
    item_id: str
    title: str
    summary: str = ""
    locator: str | None = None
    cursor: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiscoveryCatalog(BaseModel):
    """A compact discovered catalog with stable item refs and a resume cursor."""

    source_id: str
    family: str
    items: list[DiscoveryItem] = Field(default_factory=list)
    summary: str = ""
    cursor: str | None = None
    total_available: int | None = None
    truncated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class AcquisitionSelection(BaseModel):
    """A stable selection record emitted after catalog inspection."""

    ref_id: str
    source_id: str
    family: str
    reason: str = ""
    selected_by: Literal["request", "continuation", "policy"] = "policy"


class ExpandedContext(BaseModel):
    """Expanded detail for one selected catalog item."""

    ref_id: str
    source_id: str
    family: str
    title: str
    content: Any
    summary: str = ""
    trust_label: TrustLabel = TrustLabel.RETRIEVED
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def as_evidence_block(self) -> EvidenceBlock:
        return EvidenceBlock(
            label=self.title,
            content=self.content,
            trust_label=self.trust_label,
            source=self.source,
            ref_id=self.ref_id,
            metadata={
                "source_id": self.source_id,
                "family": self.family,
                **self.metadata,
            },
        )


class MemoryLayer(str, Enum):
    """Layered memory tiers for a standalone worker cell."""

    WORKING = "working"
    EPISODIC = "episodic"
    RETAINED = "retained"


class MemoryRecord(BaseModel):
    """Stored observation or summary preserved across worker turns."""

    ref_id: str
    layer: MemoryLayer = MemoryLayer.EPISODIC
    write_scope: str = "memory.cell"
    title: str
    summary: str = ""
    content: Any | None = None
    trust_label: TrustLabel = TrustLabel.HISTORICAL
    source: str | None = None
    freshness: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    compacted: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    def as_evidence_block(self, *, summary_only: bool = False) -> EvidenceBlock:
        content = (self.summary or self.title) if summary_only or self.compacted or self.content is None else self.content
        return EvidenceBlock(
            label=self.title,
            content=content,
            trust_label=self.trust_label,
            source=self.source,
            ref_id=self.ref_id,
            metadata={
                "family": AcquisitionFamily.MEMORY_CATALOG.value,
                "memory_layer": self.layer.value,
                "write_scope": self.write_scope,
                "freshness": self.freshness,
                "provenance": dict(self.provenance),
                "compacted": self.compacted,
                **self.metadata,
            },
        )

    def as_discovery_item(self, source_id: str) -> DiscoveryItem:
        item_id = self.ref_id.rsplit(":", 1)[-1]
        return DiscoveryItem(
            ref_id=self.ref_id,
            source_id=source_id,
            family=AcquisitionFamily.MEMORY_CATALOG.value,
            item_id=item_id,
            title=self.title,
            summary=self.summary or self.title,
            locator=self.ref_id,
            metadata={
                "memory_layer": self.layer.value,
                "write_scope": self.write_scope,
                "freshness": self.freshness,
                "provenance": dict(self.provenance),
                "compacted": self.compacted,
                **self.metadata,
            },
        )

    def as_expanded_context(self, source_id: str) -> ExpandedContext:
        content = (self.summary or self.title) if self.compacted or self.content is None else self.content
        return ExpandedContext(
            ref_id=self.ref_id,
            source_id=source_id,
            family=AcquisitionFamily.MEMORY_CATALOG.value,
            title=self.title,
            content=content,
            summary=self.summary or self.title,
            trust_label=self.trust_label,
            source=self.source,
            metadata={
                "memory_layer": self.layer.value,
                "write_scope": self.write_scope,
                "freshness": self.freshness,
                "provenance": dict(self.provenance),
                "compacted": self.compacted,
                **self.metadata,
            },
        )


class MemoryCompactionPolicy(BaseModel):
    """Bounded write/compaction rules for cell-local memory."""

    max_working_items: int = Field(default=4, ge=0)
    max_episodic_items: int = Field(default=6, ge=0)
    max_retained_items: int = Field(default=12, ge=0)
    promote_overflow_to_retained: bool = True
    summary_only_retained: bool = True


class MemoryWrite(BaseModel):
    """Explicit observation or summary to persist after a run."""

    title: str
    layer: MemoryLayer = MemoryLayer.EPISODIC
    write_scope: str = "memory.cell"
    summary: str = ""
    content: Any | None = None
    trust_label: TrustLabel = TrustLabel.HISTORICAL
    source: str | None = None
    freshness: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryExtractionMode(str, Enum):
    """How a cell may rehydrate memory for the current turn."""

    NONE = "none"
    WORKING_ONLY = "working_only"
    CATALOG_ONLY = "catalog_only"
    WORKING_AND_CATALOG = "working_and_catalog"


class MemoryRequest(BaseModel):
    """Request-scoped controls for memory recall, write-back, and compaction."""

    reuse_memory: bool = True
    inline_working_memory: bool = True
    extraction_mode: MemoryExtractionMode = MemoryExtractionMode.WORKING_AND_CATALOG
    auto_persist_episode: bool = True
    allowed_write_scopes: list[str] = Field(default_factory=list)
    compaction_policy: MemoryCompactionPolicy | None = None
    pending_writes: list[MemoryWrite] = Field(default_factory=list)


class MemorySnapshot(BaseModel):
    """Layered memory snapshot recalled for one worker."""

    working_memory: list[MemoryRecord] = Field(default_factory=list)
    episodic_memory: list[MemoryRecord] = Field(default_factory=list)
    retained_memory: list[MemoryRecord] = Field(default_factory=list)

    def all_records(self) -> list[MemoryRecord]:
        return [*self.working_memory, *self.episodic_memory, *self.retained_memory]


class ContinuationPayload(BaseModel):
    """Durable checkpoint for staged acquisition and resumable execution."""

    stage: AcquisitionStage = AcquisitionStage.DISCOVER
    catalogs: list[DiscoveryCatalog] = Field(default_factory=list)
    selections: list[AcquisitionSelection] = Field(default_factory=list)
    expanded_context: list[ExpandedContext] = Field(default_factory=list)
    task_state: dict[str, Any] = Field(default_factory=dict)


class AcquisitionRequest(BaseModel):
    """Request-scoped acquisition config for the current task."""

    sources: list[AcquisitionSource] = Field(default_factory=list)
    selections: list[AcquisitionSelection] = Field(default_factory=list)
    policy: AcquisitionPolicy | None = None


class ToolUseContract(BaseModel):
    """Explicit tool surface exposed to one cell invocation."""

    allowed_tool_ids: list[str] = Field(default_factory=list)
    preferred_tool_ids: list[str] = Field(default_factory=list)
    require_manifest_selection: bool = True
    max_tool_calls: int | None = Field(default=None, ge=0)


class OutputContract(BaseModel):
    """What the caller needs back from execution."""

    definition_of_done: str = ""
    expected_return_shape: str = ""
    output_schema: dict[str, Any] | None = None


class CommunicationChannelKind(str, Enum):
    """Canonical outward channels exposed by the cell membrane."""

    REPLY = "reply"
    STATUS = "status"
    COMPLETION = "completion"
    ESCALATION = "escalation"


class CommunicationChannel(BaseModel):
    """One structured communication channel for a cell invocation."""

    kind: CommunicationChannelKind | str
    address: str
    required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class CommunicationContract(BaseModel):
    """Explicit communication membrane for the current cell invocation."""

    channels: list[CommunicationChannel] = Field(default_factory=list)

    def address_for(self, kind: CommunicationChannelKind | str) -> str | None:
        target = kind.value if isinstance(kind, CommunicationChannelKind) else str(kind)
        for channel in self.channels:
            channel_kind = channel.kind.value if isinstance(channel.kind, CommunicationChannelKind) else str(channel.kind)
            if channel_kind == target:
                return channel.address
        return None


class ExecutionRequest(BaseModel):
    """Normalized worker execution payload shared across callers."""

    task: str
    constraints: ConstraintSet = Field(default_factory=ConstraintSet)
    evidence: list[EvidenceBlock] = Field(default_factory=list)
    acquisition: AcquisitionRequest = Field(default_factory=AcquisitionRequest)
    tooling: ToolUseContract = Field(default_factory=ToolUseContract)
    memory: MemoryRequest = Field(default_factory=MemoryRequest)
    communication: CommunicationContract = Field(default_factory=CommunicationContract)
    continuation: ContinuationPayload | None = None
    output_contract: OutputContract = Field(default_factory=OutputContract)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_handoff(
        cls,
        *,
        task: str,
        scope: str | None = None,
        hard_constraints: Iterable[str] | None = None,
        soft_constraints: Iterable[str] | None = None,
        evidence_blocks: Iterable[EvidenceBlock | dict[str, Any]] | None = None,
        acquisition: AcquisitionRequest | dict[str, Any] | None = None,
        tooling: ToolUseContract | dict[str, Any] | None = None,
        memory: MemoryRequest | dict[str, Any] | None = None,
        communication: CommunicationContract | dict[str, Any] | None = None,
        continuation: ContinuationPayload | dict[str, Any] | None = None,
        definition_of_done: str = "",
        expected_return_shape: str = "",
        output_schema: dict[str, Any] | None = None,
        input_payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ExecutionRequest":
        """Build the normalized request from a 46-6-style handoff envelope."""

        return cls(
            task=task,
            constraints=ConstraintSet(
                scope=scope,
                hard_constraints=list(hard_constraints or []),
                soft_constraints=list(soft_constraints or []),
            ),
            evidence=[
                block if isinstance(block, EvidenceBlock) else EvidenceBlock.model_validate(block)
                for block in (evidence_blocks or [])
            ],
            acquisition=(
                acquisition
                if isinstance(acquisition, AcquisitionRequest)
                else AcquisitionRequest.model_validate(acquisition or {})
            ),
            tooling=(
                tooling
                if isinstance(tooling, ToolUseContract)
                else ToolUseContract.model_validate(tooling or {})
            ),
            memory=(
                memory
                if isinstance(memory, MemoryRequest)
                else MemoryRequest.model_validate(memory or {})
            ),
            communication=(
                communication
                if isinstance(communication, CommunicationContract)
                else CommunicationContract.model_validate(communication or {})
            ),
            continuation=(
                continuation
                if isinstance(continuation, ContinuationPayload)
                else (
                    ContinuationPayload.model_validate(continuation)
                    if continuation is not None
                    else None
                )
            ),
            output_contract=OutputContract(
                definition_of_done=definition_of_done,
                expected_return_shape=expected_return_shape,
                output_schema=output_schema,
            ),
            input_payload=dict(input_payload or {}),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_harness(
        cls,
        *,
        task: str,
        constraints: ConstraintSet | dict[str, Any] | None = None,
        evidence: Iterable[EvidenceBlock | dict[str, Any]] | None = None,
        acquisition: AcquisitionRequest | dict[str, Any] | None = None,
        tooling: ToolUseContract | dict[str, Any] | None = None,
        memory: MemoryRequest | dict[str, Any] | None = None,
        communication: CommunicationContract | dict[str, Any] | None = None,
        continuation: ContinuationPayload | dict[str, Any] | None = None,
        output_contract: OutputContract | dict[str, Any] | None = None,
        input_payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ExecutionRequest":
        """Build the same request shape from a non-DAN harness."""

        resolved_constraints = constraints
        if resolved_constraints is None:
            resolved_constraints = ConstraintSet()
        elif not isinstance(resolved_constraints, ConstraintSet):
            resolved_constraints = ConstraintSet.model_validate(resolved_constraints)

        resolved_output_contract = output_contract
        if resolved_output_contract is None:
            resolved_output_contract = OutputContract()
        elif not isinstance(resolved_output_contract, OutputContract):
            resolved_output_contract = OutputContract.model_validate(resolved_output_contract)

        resolved_acquisition = acquisition
        if resolved_acquisition is None:
            resolved_acquisition = AcquisitionRequest()
        elif not isinstance(resolved_acquisition, AcquisitionRequest):
            resolved_acquisition = AcquisitionRequest.model_validate(resolved_acquisition)

        resolved_memory = memory
        if resolved_memory is None:
            resolved_memory = MemoryRequest()
        elif not isinstance(resolved_memory, MemoryRequest):
            resolved_memory = MemoryRequest.model_validate(resolved_memory)

        resolved_tooling = tooling
        if resolved_tooling is None:
            resolved_tooling = ToolUseContract()
        elif not isinstance(resolved_tooling, ToolUseContract):
            resolved_tooling = ToolUseContract.model_validate(resolved_tooling)

        resolved_communication = communication
        if resolved_communication is None:
            resolved_communication = CommunicationContract()
        elif not isinstance(resolved_communication, CommunicationContract):
            resolved_communication = CommunicationContract.model_validate(resolved_communication)

        resolved_continuation = continuation
        if resolved_continuation is not None and not isinstance(
            resolved_continuation,
            ContinuationPayload,
        ):
            resolved_continuation = ContinuationPayload.model_validate(resolved_continuation)

        return cls(
            task=task,
            constraints=resolved_constraints,
            evidence=[
                block if isinstance(block, EvidenceBlock) else EvidenceBlock.model_validate(block)
                for block in (evidence or [])
            ],
            acquisition=resolved_acquisition,
            tooling=resolved_tooling,
            memory=resolved_memory,
            communication=resolved_communication,
            continuation=resolved_continuation,
            output_contract=resolved_output_contract,
            input_payload=dict(input_payload or {}),
            metadata=dict(metadata or {}),
        )
