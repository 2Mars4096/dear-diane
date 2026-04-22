"""Deterministic Super DAN organism showcase.

This module is intentionally not a live many-agent runner yet. It builds the
coordination substrate first: cells, organs, board signals, claim graph updates,
resource requests, and reallocations. A later live mode can route selected cells
through the existing WorkerCoreExecutor while preserving this board contract.
"""

from __future__ import annotations

from collections import Counter
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


DEFAULT_SUPER_ORGANISM_TARGET = (
    "use the 20-cell Super DAN organism to turn an operator objective into a live execution contract"
)
DEFAULT_SUPER_ORGANISM_TRUTH_TARGET = "a new AI agent product"
DEFAULT_SUPER_ORGANISM_ID = "super-dan-20"
DEFAULT_SUPER_ORGANISM_CELL_COUNT = 20
DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP = 8

_REFERENCE_SUPER_ORGANISM_CELL_COUNT = 100
_DEFAULT_DISTRIBUTION: dict[str, int] = {
    "brain": 6,
    "scout": 24,
    "claim": 20,
    "immune": 18,
    "memory": 12,
    "experiment": 10,
    "synthesis": 10,
}

_ROLE_CATALOG: dict[str, list[str]] = {
    "brain": [
        "mission_cell",
        "decomposition_cell",
        "topology_cell",
        "resource_cell",
        "evidence_judge_cell",
        "integration_cell",
    ],
    "scout": [
        "docs_scout",
        "repo_scout",
        "paper_scout",
        "benchmark_scout",
        "adoption_scout",
        "changelog_scout",
    ],
    "claim": [
        "claim_extractor",
        "scope_extractor",
        "benchmark_claim_extractor",
        "adoption_claim_extractor",
    ],
    "immune": [
        "contradiction_hunter",
        "staleness_hunter",
        "citation_skeptic",
        "benchmark_skeptic",
        "adoption_skeptic",
        "scope_skeptic",
    ],
    "memory": [
        "claim_clusterer",
        "duplicate_merger",
        "evidence_indexer",
        "claim_graph_keeper",
    ],
    "experiment": [
        "install_probe",
        "docs_probe",
        "repo_probe",
        "benchmark_probe",
        "api_probe",
    ],
    "synthesis": [
        "memo_writer",
        "scorecard_writer",
        "trace_writer",
        "risk_writer",
        "verdict_writer",
    ],
}


class SuperOrgan(str, Enum):
    """Organs used by the first Super DAN showcase organism."""

    BRAIN = "brain"
    SCOUT = "scout"
    CLAIM = "claim"
    IMMUNE = "immune"
    MEMORY = "memory"
    EXPERIMENT = "experiment"
    SYNTHESIS = "synthesis"


class SuperOrganismScenario(str, Enum):
    """Supported deterministic showcase scenarios."""

    UNIVERSAL_AGENT = "universal_agent"
    TRUTH_AUDIT = "truth_audit"
    WEBSITE_BUILD = "website_build"


class ClaimStatus(str, Enum):
    """Claim graph status after immune and experiment passes."""

    VERIFIED = "verified"
    DISPUTED = "disputed"
    UNSUPPORTED = "unsupported"
    NEEDS_LIVE_CHECK = "needs_live_check"


class SuperOrganismCell(BaseModel):
    """One logical cell in the deterministic organism roster."""

    cell_id: str
    organ: SuperOrgan
    role: str
    focus: str
    status: Literal["assigned", "running", "retired", "completed"] = "assigned"


class BoardSignal(BaseModel):
    """Shared board signal emitted by cells and brain coordinators."""

    tick: int
    signal_type: Literal[
        "heartbeat",
        "state_delta",
        "resource_request",
        "reallocation",
        "completion",
    ]
    source_cell_id: str
    organ: SuperOrgan
    phase: str
    summary: str
    subject_id: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    payload: dict[str, Any] = Field(default_factory=dict)


class ActivityWave(BaseModel):
    """A scheduler wave proving the logical organism respects active-cell caps."""

    tick: int
    phase: str
    cell_ids: list[str] = Field(default_factory=list)

    @property
    def active_count(self) -> int:
        return len(self.cell_ids)


class ClaimNode(BaseModel):
    """One atomized claim in the showcase claim graph."""

    claim_id: str
    text: str
    cluster: str
    status: ClaimStatus
    confidence: float = Field(ge=0.0, le=1.0)
    support_count: int = Field(default=0, ge=0)
    challenge_count: int = Field(default=0, ge=0)
    experiment_count: int = Field(default=0, ge=0)
    evidence_refs: list[str] = Field(default_factory=list)
    challenged_by: list[str] = Field(default_factory=list)
    tested_by: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DeliveryNode(BaseModel):
    """One delivery-plan node for build-oriented showcase scenarios."""

    node_id: str
    title: str
    organ: SuperOrgan
    status: Literal["ready", "needs_input", "live_build_required", "planned"]
    assigned_cell_count: int = Field(ge=1)
    dependencies: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class EvidenceRef(BaseModel):
    """Compact evidence pointer for deterministic showcase outputs."""

    ref_id: str
    label: str
    source_family: str
    summary: str
    trust_hint: Literal["strong", "medium", "weak", "synthetic"] = "synthetic"


class ReallocationDecision(BaseModel):
    """Brain resource-cell decision made from board signals."""

    tick: int
    decision_id: str
    from_organ: SuperOrgan
    to_organ: SuperOrgan
    cell_count: int = Field(ge=1)
    reason: str
    target_claim_ids: list[str] = Field(default_factory=list)
    outcome: str = ""


class CoordinationTicket(BaseModel):
    """Explicit ownership ticket on the shared organism board."""

    ticket_id: str
    node_id: str
    title: str
    organ: SuperOrgan
    phase: str
    status: Literal[
        "completed",
        "awaiting_live_execution",
        "awaiting_result_handoff",
        "awaiting_acceptance",
        "blocked",
    ]
    owner_cell_id: str
    cell_ids: list[str] = Field(default_factory=list)
    dependency_ticket_ids: list[str] = Field(default_factory=list)
    artifact_targets: list[str] = Field(default_factory=list)
    acceptance_checks: list[str] = Field(default_factory=list)
    blocker: str = ""
    notes: list[str] = Field(default_factory=list)


class HandoffPacket(BaseModel):
    """Typed packet passed between tickets on the shared organism board."""

    packet_id: str
    phase: str
    packet_type: Literal[
        "objective",
        "context",
        "work_graph",
        "risk_gate",
        "execution",
        "result",
        "acceptance",
    ]
    status: Literal["published", "pending"]
    from_ticket_id: str
    to_ticket_id: str
    from_cell_id: str
    to_cell_id: str
    summary: str
    payload_keys: list[str] = Field(default_factory=list)
    release_condition: str = ""


class SharedBoardState(BaseModel):
    """Compact board snapshot for the final deterministic report."""

    board_id: str = "board-001"
    objective: str
    execution_family: str
    memory_clusters: list[str] = Field(default_factory=list)
    completed_ticket_ids: list[str] = Field(default_factory=list)
    waiting_ticket_ids: list[str] = Field(default_factory=list)
    blocked_ticket_ids: list[str] = Field(default_factory=list)
    reserve_cell_ids: list[str] = Field(default_factory=list)
    published_packet_ids: list[str] = Field(default_factory=list)
    pending_packet_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FinalAuditGate(BaseModel):
    """The organism's explicit stop/continue gate before exit."""

    audit_id: str = "audit-001"
    auditor_cell_id: str
    reviewer_cell_ids: list[str] = Field(default_factory=list)
    status: Literal["continue", "done", "clarify"] = "continue"
    satisfied: bool = False
    summary: str
    acceptance_checks: list[str] = Field(default_factory=list)
    blocker_ticket_ids: list[str] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)
    next_action: str = ""


class SuperOrganismReport(BaseModel):
    """Final report for the deterministic organism showcase run."""

    status: Literal["completed"] = "completed"
    mode: Literal["deterministic_demo"] = "deterministic_demo"
    scenario: SuperOrganismScenario = SuperOrganismScenario.TRUTH_AUDIT
    organism_id: str
    target: str
    cell_count: int
    active_cell_cap: int
    max_active_observed: int
    organ_counts: dict[str, int]
    signal_counts: dict[str, int]
    stage_sequence: list[str]
    final_verdict: str
    score_label: str = "Credibility Score"
    credibility_score: float = Field(ge=0.0, le=1.0)
    execution_family: str = "general_operator"
    final_memo: str
    caveat: str
    cells: list[SuperOrganismCell]
    evidence_refs: list[EvidenceRef]
    claim_graph: list[ClaimNode]
    delivery_plan: list[DeliveryNode] = Field(default_factory=list)
    shared_board: SharedBoardState | None = None
    coordination_tickets: list[CoordinationTicket] = Field(default_factory=list)
    handoff_packets: list[HandoffPacket] = Field(default_factory=list)
    final_audit: FinalAuditGate | None = None
    reallocation_decisions: list[ReallocationDecision]
    activity_waves: list[ActivityWave]
    board_signals: list[BoardSignal]

    @model_validator(mode="after")
    def _validate_counts(self) -> "SuperOrganismReport":
        if len(self.cells) != self.cell_count:
            raise ValueError("cell_count must match cells length")
        if self.max_active_observed > self.active_cell_cap:
            raise ValueError("activity waves exceeded active_cell_cap")
        valid_cell_ids = {cell.cell_id for cell in self.cells}
        valid_ticket_ids = {ticket.ticket_id for ticket in self.coordination_tickets}
        valid_packet_ids = {packet.packet_id for packet in self.handoff_packets}
        for ticket in self.coordination_tickets:
            if ticket.owner_cell_id not in valid_cell_ids:
                raise ValueError("coordination ticket owner_cell_id must reference a known cell")
            if not ticket.cell_ids:
                raise ValueError("coordination tickets must carry at least one assigned cell")
            if ticket.owner_cell_id not in ticket.cell_ids:
                raise ValueError("coordination ticket owner must appear in cell_ids")
            if any(cell_id not in valid_cell_ids for cell_id in ticket.cell_ids):
                raise ValueError("coordination ticket cell_ids must reference known cells")
            if any(ticket_id not in valid_ticket_ids for ticket_id in ticket.dependency_ticket_ids):
                raise ValueError("coordination ticket dependency ids must reference known tickets")
        for packet in self.handoff_packets:
            if packet.from_ticket_id not in valid_ticket_ids or packet.to_ticket_id not in valid_ticket_ids:
                raise ValueError("handoff packet ticket ids must reference known tickets")
            if packet.from_cell_id not in valid_cell_ids or packet.to_cell_id not in valid_cell_ids:
                raise ValueError("handoff packet cell ids must reference known cells")
        if self.shared_board is not None:
            board_ticket_ids = (
                list(self.shared_board.completed_ticket_ids)
                + list(self.shared_board.waiting_ticket_ids)
                + list(self.shared_board.blocked_ticket_ids)
            )
            if any(ticket_id not in valid_ticket_ids for ticket_id in board_ticket_ids):
                raise ValueError("shared board ticket ids must reference known tickets")
            if any(cell_id not in valid_cell_ids for cell_id in self.shared_board.reserve_cell_ids):
                raise ValueError("shared board reserve_cell_ids must reference known cells")
            board_packet_ids = list(self.shared_board.published_packet_ids) + list(
                self.shared_board.pending_packet_ids
            )
            if any(packet_id not in valid_packet_ids for packet_id in board_packet_ids):
                raise ValueError("shared board packet ids must reference known packets")
            accounted_cells = set(self.shared_board.reserve_cell_ids)
            for ticket in self.coordination_tickets:
                accounted_cells.update(ticket.cell_ids)
            if accounted_cells != valid_cell_ids:
                raise ValueError("shared board must account for every logical cell")
        if self.final_audit is not None:
            if self.final_audit.auditor_cell_id not in valid_cell_ids:
                raise ValueError("final audit auditor_cell_id must reference a known cell")
            if any(cell_id not in valid_cell_ids for cell_id in self.final_audit.reviewer_cell_ids):
                raise ValueError("final audit reviewer_cell_ids must reference known cells")
            if any(ticket_id not in valid_ticket_ids for ticket_id in self.final_audit.blocker_ticket_ids):
                raise ValueError("final audit blocker_ticket_ids must reference known tickets")
        return self


def resolve_super_organism_distribution(cell_count: int) -> dict[str, int]:
    """Return a proportional organ distribution for a logical cell count."""

    if cell_count < len(_DEFAULT_DISTRIBUTION):
        raise ValueError(f"cell_count must be at least {len(_DEFAULT_DISTRIBUTION)}")
    if cell_count == _REFERENCE_SUPER_ORGANISM_CELL_COUNT:
        return dict(_DEFAULT_DISTRIBUTION)

    weights = _DEFAULT_DISTRIBUTION
    raw = {organ: cell_count * count / _REFERENCE_SUPER_ORGANISM_CELL_COUNT for organ, count in weights.items()}
    counts = {organ: max(1, int(value)) for organ, value in raw.items()}

    while sum(counts.values()) < cell_count:
        organ = max(weights, key=lambda key: (raw[key] - int(raw[key]), weights[key], key))
        counts[organ] += 1
        raw[organ] = int(raw[organ])

    while sum(counts.values()) > cell_count:
        organ = max((key for key, value in counts.items() if value > 1), key=lambda key: counts[key])
        counts[organ] -= 1

    return counts


def build_super_organism_cells(
    *,
    cell_count: int = DEFAULT_SUPER_ORGANISM_CELL_COUNT,
) -> list[SuperOrganismCell]:
    """Build the deterministic logical cell roster."""

    distribution = resolve_super_organism_distribution(cell_count)
    cells: list[SuperOrganismCell] = []
    for organ_name, count in distribution.items():
        organ = SuperOrgan(organ_name)
        roles = _ROLE_CATALOG[organ_name]
        for index in range(count):
            role = roles[index % len(roles)]
            cells.append(
                SuperOrganismCell(
                    cell_id=f"{organ.value}-{index + 1:03d}",
                    organ=organ,
                    role=role,
                    focus=_focus_for_role(role),
                )
            )
    return cells


def run_super_organism_demo(
    target: str | None = None,
    *,
    organism_id: str = DEFAULT_SUPER_ORGANISM_ID,
    cell_count: int = DEFAULT_SUPER_ORGANISM_CELL_COUNT,
    active_cell_cap: int = DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP,
) -> SuperOrganismReport:
    """Run the deterministic coordination showcase."""

    raw_target = _normalize_objective_text(target)
    if not raw_target:
        raw_target = DEFAULT_SUPER_ORGANISM_TARGET
    return _run_universal_agent_demo(
        raw_target,
        organism_id=organism_id,
        cell_count=cell_count,
        active_cell_cap=active_cell_cap,
    )


def resolve_super_organism_scenario(
    target: str,
    *,
    scenario: str | SuperOrganismScenario = "auto",
) -> SuperOrganismScenario:
    """Return the current public run contract.

    Kept as a compatibility shim for older callers; public Super DAN runs are
    objective-first and no longer expose scenario selection.
    """

    _ = (target, scenario)
    return SuperOrganismScenario.UNIVERSAL_AGENT


def _normalize_objective_text(value: str | None) -> str:
    """Collapse pasted multiline shell objectives into one terminal-safe line."""

    return " ".join(str(value or "").split())


def _run_universal_agent_demo(
    target: str = DEFAULT_SUPER_ORGANISM_TARGET,
    *,
    organism_id: str = DEFAULT_SUPER_ORGANISM_ID,
    cell_count: int = DEFAULT_SUPER_ORGANISM_CELL_COUNT,
    active_cell_cap: int = DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP,
) -> SuperOrganismReport:
    """Run the deterministic universal-agent coordination showcase."""

    if active_cell_cap < 1:
        raise ValueError("active_cell_cap must be at least 1")
    target = _normalize_objective_text(target) or DEFAULT_SUPER_ORGANISM_TARGET
    cells = build_super_organism_cells(cell_count=cell_count)
    by_organ: dict[SuperOrgan, list[SuperOrganismCell]] = {
        organ: [cell for cell in cells if cell.organ == organ] for organ in SuperOrgan
    }

    signals: list[BoardSignal] = []
    waves: list[ActivityWave] = []
    tick = 0
    stage_sequence: list[str] = []
    execution_family = _execution_family_for_target(target)

    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=by_organ[SuperOrgan.BRAIN],
        tick=tick,
        phase="objective_intake",
        active_cell_cap=active_cell_cap,
        summary="Brain cells accepted the raw operator request as the objective and selected the universal-agent contract.",
        payload={
            "scenario": SuperOrganismScenario.UNIVERSAL_AGENT.value,
            "objective": target,
            "execution_family": execution_family,
        },
    )
    stage_sequence.append("objective_intake")

    evidence_refs = _build_universal_evidence_refs(target, execution_family)
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=by_organ[SuperOrgan.SCOUT],
        tick=tick,
        phase="context_acquisition",
        active_cell_cap=active_cell_cap,
        summary="Scout cells mapped the objective, context gaps, capability needs, workspace signals, and acceptance evidence.",
        payload={"evidence_refs": [ref.ref_id for ref in evidence_refs], "execution_family": execution_family},
    )
    stage_sequence.append("context_acquisition")

    delivery_plan = _build_universal_delivery_plan(target, execution_family, cell_count=cell_count)
    shared_board, coordination_tickets, handoff_packets, final_audit = _build_universal_coordination_contract(
        target,
        execution_family,
        delivery_plan,
        by_organ=by_organ,
    )
    contract_cells = by_organ[SuperOrgan.CLAIM]
    for index, node in enumerate(delivery_plan):
        cell = contract_cells[index % len(contract_cells)]
        signals.append(
            BoardSignal(
                tick=tick,
                signal_type="state_delta",
                source_cell_id=cell.cell_id,
                organ=cell.organ,
                phase="contract_decomposition",
                summary=f"Contract cell created {node.node_id}: {node.title}",
                subject_id=node.node_id,
                confidence=0.79,
                payload={
                    "namespace": "universal.delivery_plan",
                    "op": "upsert_delivery_node",
                    "status": node.status,
                    "outputs": node.outputs,
                    "execution_family": execution_family,
                },
            )
        )
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=contract_cells,
        tick=tick + 1,
        phase="contract_decomposition",
        active_cell_cap=active_cell_cap,
        summary="Contract cells decomposed the objective into deliverable work nodes, dependencies, and acceptance outputs.",
    )
    stage_sequence.append("contract_decomposition")

    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=by_organ[SuperOrgan.MEMORY],
        tick=tick,
        phase="shared_memory_index",
        active_cell_cap=active_cell_cap,
        summary="Memory cells built the shared board index so cells can coordinate by objective, contract, risk, evidence, and output.",
        payload={
            "clusters": [
                "objective",
                "contracts",
                "context",
                "risk",
                "execution",
                "acceptance",
            ],
            "ticket_count": len(coordination_tickets),
            "published_packet_count": len(shared_board.published_packet_ids),
            "reserve_cell_ids": list(shared_board.reserve_cell_ids),
        },
    )
    signals.append(
        BoardSignal(
            tick=tick,
            signal_type="state_delta",
            source_cell_id=_first_cell_id(by_organ[SuperOrgan.MEMORY], "memory-001"),
            organ=SuperOrgan.MEMORY,
            phase="shared_memory_index",
            summary=(
                f"Memory cells indexed {len(coordination_tickets)} tickets and "
                f"{len(handoff_packets)} typed handoff packets on the shared board."
            ),
            subject_id=shared_board.board_id,
            confidence=0.81,
            payload={
                "namespace": "universal.shared_board",
                "op": "upsert_board_state",
                "completed_ticket_ids": list(shared_board.completed_ticket_ids),
                "waiting_ticket_ids": list(shared_board.waiting_ticket_ids),
                "published_packet_ids": list(shared_board.published_packet_ids),
                "pending_packet_ids": list(shared_board.pending_packet_ids),
            },
        )
    )
    stage_sequence.append("shared_memory_index")

    immune_cells = by_organ[SuperOrgan.IMMUNE]
    risk_targets = ["node-002", "node-005", "node-006", "node-008"]
    for index, node_id in enumerate(risk_targets):
        cell = immune_cells[index % len(immune_cells)]
        signals.append(
            BoardSignal(
                tick=tick,
                signal_type="state_delta",
                source_cell_id=cell.cell_id,
                organ=cell.organ,
                phase="contract_immune_check",
                summary=f"Immune cell challenged contract risk in {node_id}.",
                subject_id=node_id,
                confidence=0.84,
                payload={
                    "namespace": "universal.delivery_plan",
                    "op": "challenge_delivery_node",
                    "risks": [
                        "objective ambiguity",
                        "missing live context",
                        "unsafe or overbroad authority",
                        "weak acceptance test",
                    ],
                },
            )
        )
    signals.append(
        BoardSignal(
            tick=tick,
            signal_type="resource_request",
            source_cell_id="immune-001",
            organ=SuperOrgan.IMMUNE,
            phase="contract_immune_check",
            summary="Immune organ requested more execution-probe cells before declaring the objective deliverable.",
            subject_id="node-006",
            confidence=0.86,
            payload={
                "request_type": "need_more_cells",
                "suggested_cell_count": _scale_total_cell_count(cell_count, 8),
                "needed_capability": "bounded live execution probe",
                "target_node_ids": ["node-006", "node-007", "node-008"],
            },
        )
    )
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=immune_cells,
        tick=tick + 1,
        phase="contract_immune_check",
        active_cell_cap=active_cell_cap,
        summary="Immune cells tested whether the objective contract is clear, safe, bounded, and measurable.",
    )
    stage_sequence.append("contract_immune_check")

    reallocations = [
        ReallocationDecision(
            tick=tick,
            decision_id="realloc-001",
            from_organ=SuperOrgan.SCOUT,
            to_organ=SuperOrgan.EXPERIMENT,
            cell_count=_scale_organ_cell_count(by_organ[SuperOrgan.SCOUT], SuperOrgan.SCOUT, 6),
            reason="Context mapping is sufficient; the bottleneck is probing how the objective would execute live.",
            target_claim_ids=["node-006", "node-007"],
            outcome="Scout cell budget moved to execution probes.",
        ),
        ReallocationDecision(
            tick=tick,
            decision_id="realloc-002",
            from_organ=SuperOrgan.CLAIM,
            to_organ=SuperOrgan.IMMUNE,
            cell_count=_scale_organ_cell_count(by_organ[SuperOrgan.CLAIM], SuperOrgan.CLAIM, 4),
            reason="The work graph is stable; risk, authority, and acceptance checks now decide whether to proceed.",
            target_claim_ids=["node-005", "node-008"],
            outcome="Contract cell budget shifted into immune review.",
        ),
    ]
    for decision in reallocations:
        signals.append(
            BoardSignal(
                tick=decision.tick,
                signal_type="reallocation",
                source_cell_id=_first_cell_id(by_organ[SuperOrgan.BRAIN], "brain-001"),
                organ=SuperOrgan.BRAIN,
                phase="brain_reallocation",
                summary=decision.reason,
                subject_id=decision.decision_id,
                confidence=0.89,
                payload=decision.model_dump(mode="json"),
            )
        )
    stage_sequence.append("brain_reallocation")

    experiment_cells = by_organ[SuperOrgan.EXPERIMENT]
    for node_id in ["node-006", "node-007", "node-008"]:
        cell = experiment_cells[int(node_id.rsplit("-", 1)[-1]) % len(experiment_cells)]
        signals.append(
            BoardSignal(
                tick=tick + 1,
                signal_type="state_delta",
                source_cell_id=cell.cell_id,
                organ=cell.organ,
                phase="execution_probe",
                summary=f"Experiment cell defined live-execution probe for {node_id}.",
                subject_id=node_id,
                confidence=0.76,
                payload={
                    "namespace": "universal.delivery_plan",
                    "op": "attach_execution_probe",
                    "probe": "execution lane, authority boundary, output artifact, acceptance check",
                    "execution_family": execution_family,
                },
            )
        )
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=experiment_cells,
        tick=tick + 2,
        phase="execution_probe",
        active_cell_cap=active_cell_cap,
        summary="Experiment cells prepared bounded native execution probes for the lanes that would deliver the objective.",
    )
    stage_sequence.append("execution_probe")

    synthesis_cells = by_organ[SuperOrgan.SYNTHESIS]
    final_verdict = "universal-agent execution contract ready"
    readiness_score = 0.72
    final_memo = _build_universal_final_memo(
        target,
        delivery_plan,
        shared_board,
        coordination_tickets,
        handoff_packets,
        final_audit,
        readiness_score,
        final_verdict,
        execution_family,
        cell_count=cell_count,
    )
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=synthesis_cells,
        tick=tick,
        phase="synthesis",
        active_cell_cap=active_cell_cap,
        summary="Synthesis cells assembled the objective contract, organ allocation, execution probes, and acceptance gates.",
        payload={"final_verdict": final_verdict, "execution_readiness_score": readiness_score},
    )
    signals.append(
        BoardSignal(
            tick=tick,
            signal_type="state_delta",
            source_cell_id=final_audit.auditor_cell_id,
            organ=SuperOrgan.SYNTHESIS,
            phase="synthesis",
            summary="Final audit left the organism in continue mode until the live execution loop publishes result and acceptance packets.",
            subject_id=final_audit.audit_id,
            confidence=0.87,
            payload={
                "namespace": "universal.final_audit",
                "op": "set_audit_gate",
                "status": final_audit.status,
                "satisfied": final_audit.satisfied,
                "blocker_ticket_ids": list(final_audit.blocker_ticket_ids),
                "next_action": final_audit.next_action,
            },
        )
    )
    signals.append(
        BoardSignal(
            tick=tick,
            signal_type="completion",
            source_cell_id="synthesis-001",
            organ=SuperOrgan.SYNTHESIS,
            phase="synthesis",
            summary=f"Super DAN {cell_count}-cell universal-agent deterministic showcase completed.",
            confidence=readiness_score,
            payload={
                "delivery_node_count": len(delivery_plan),
                "execution_family": execution_family,
                "live_build_required": [
                    node.node_id for node in delivery_plan if node.status == "live_build_required"
                ],
            },
        )
    )
    stage_sequence.append("synthesis")

    retired_cell_ids = {
        *[cell.cell_id for cell in by_organ[SuperOrgan.SCOUT][-reallocations[0].cell_count :]],
        *[cell.cell_id for cell in by_organ[SuperOrgan.CLAIM][-reallocations[1].cell_count :]],
    }
    completed_cells = [
        cell.model_copy(update={"status": "retired" if cell.cell_id in retired_cell_ids else "completed"})
        for cell in cells
    ]
    max_active = max((wave.active_count for wave in waves), default=0)
    signal_counts = Counter(signal.signal_type for signal in signals)
    organ_counts = Counter(cell.organ.value for cell in completed_cells)

    return SuperOrganismReport(
        scenario=SuperOrganismScenario.UNIVERSAL_AGENT,
        organism_id=organism_id,
        target=target,
        cell_count=len(completed_cells),
        active_cell_cap=active_cell_cap,
        max_active_observed=max_active,
        organ_counts=dict(organ_counts),
        signal_counts=dict(signal_counts),
        stage_sequence=stage_sequence,
        final_verdict=final_verdict,
        score_label="Execution Readiness Score",
        credibility_score=readiness_score,
        execution_family=execution_family,
        final_memo=final_memo,
        caveat=(
            "This is still a deterministic coordination demo. It accepts the objective "
            "and produces a universal-agent execution contract, but it does not call the "
            "separate DAN Code or DAN Research products or fetch live evidence."
        ),
        cells=completed_cells,
        evidence_refs=evidence_refs,
        claim_graph=[],
        delivery_plan=delivery_plan,
        shared_board=shared_board,
        coordination_tickets=coordination_tickets,
        handoff_packets=handoff_packets,
        final_audit=final_audit,
        reallocation_decisions=reallocations,
        activity_waves=waves,
        board_signals=signals,
    )


def _run_truth_audit_demo(
    target: str = DEFAULT_SUPER_ORGANISM_TARGET,
    *,
    organism_id: str = DEFAULT_SUPER_ORGANISM_ID,
    cell_count: int = DEFAULT_SUPER_ORGANISM_CELL_COUNT,
    active_cell_cap: int = DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP,
) -> SuperOrganismReport:
    """Run the deterministic truth-audit showcase."""

    if active_cell_cap < 1:
        raise ValueError("active_cell_cap must be at least 1")
    target = _normalize_objective_text(target) or DEFAULT_SUPER_ORGANISM_TARGET
    cells = build_super_organism_cells(cell_count=cell_count)
    by_organ: dict[SuperOrgan, list[SuperOrganismCell]] = {
        organ: [cell for cell in cells if cell.organ == organ] for organ in SuperOrgan
    }

    signals: list[BoardSignal] = []
    waves: list[ActivityWave] = []
    tick = 0
    stage_sequence: list[str] = []

    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=by_organ[SuperOrgan.BRAIN],
        tick=tick,
        phase="mission_lock",
        active_cell_cap=active_cell_cap,
        summary="Brain cells locked mission, success criteria, and proof standard.",
    )
    stage_sequence.append("mission_lock")

    evidence_refs = _build_evidence_refs(target)
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=by_organ[SuperOrgan.SCOUT],
        tick=tick,
        phase="scout_evidence",
        active_cell_cap=active_cell_cap,
        summary="Scout cells collected deterministic evidence lanes for docs, repo, benchmarks, adoption, and reproducibility.",
        payload={"evidence_refs": [ref.ref_id for ref in evidence_refs]},
    )
    stage_sequence.append("scout_evidence")

    claims = _build_claim_graph(target)
    claim_cells = by_organ[SuperOrgan.CLAIM]
    for index, claim in enumerate(claims):
        cell = claim_cells[index % len(claim_cells)]
        signals.append(
            BoardSignal(
                tick=tick,
                signal_type="state_delta",
                source_cell_id=cell.cell_id,
                organ=cell.organ,
                phase="claim_atomization",
                summary=f"Extracted {claim.claim_id}: {claim.text}",
                subject_id=claim.claim_id,
                confidence=claim.confidence,
                payload={
                    "namespace": "truth.claim_graph",
                    "op": "upsert_claim",
                    "cluster": claim.cluster,
                    "status": claim.status.value,
                },
            )
        )
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=claim_cells,
        tick=tick + 1,
        phase="claim_atomization",
        active_cell_cap=active_cell_cap,
        summary="Claim cells atomized broad source material into graph nodes.",
    )
    stage_sequence.append("claim_atomization")

    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=by_organ[SuperOrgan.MEMORY],
        tick=tick,
        phase="memory_clustering",
        active_cell_cap=active_cell_cap,
        summary="Memory cells merged duplicate claims and organized them by capability, proof, adoption, and reproducibility.",
        payload={"clusters": sorted({claim.cluster for claim in claims})},
    )
    stage_sequence.append("memory_clustering")

    immune_focus = [claim for claim in claims if claim.status in {ClaimStatus.DISPUTED, ClaimStatus.UNSUPPORTED}]
    immune_cells = by_organ[SuperOrgan.IMMUNE]
    for index, claim in enumerate(immune_focus):
        cell = immune_cells[index % len(immune_cells)]
        signals.append(
            BoardSignal(
                tick=tick,
                signal_type="state_delta",
                source_cell_id=cell.cell_id,
                organ=cell.organ,
                phase="immune_challenge",
                summary=f"Challenged weak claim {claim.claim_id}.",
                subject_id=claim.claim_id,
                confidence=max(0.1, 1.0 - claim.confidence),
                payload={
                    "namespace": "truth.claim_graph",
                    "op": "challenge_claim",
                    "challenge_count": claim.challenge_count,
                    "reason": "; ".join(claim.notes[:2]),
                },
            )
        )
    signals.append(
        BoardSignal(
            tick=tick,
            signal_type="resource_request",
            source_cell_id="immune-001",
            organ=SuperOrgan.IMMUNE,
            phase="immune_challenge",
            summary="Immune organ requested more cells for benchmark, adoption, and proof-standard pressure.",
            subject_id="claim-002",
            confidence=0.78,
            payload={
                "request_type": "need_more_cells",
                "suggested_cell_count": _scale_total_cell_count(cell_count, 8),
                "needed_capability": "skeptical verification",
                "target_claim_ids": [claim.claim_id for claim in immune_focus],
            },
        )
    )
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=immune_cells,
        tick=tick + 1,
        phase="immune_challenge",
        active_cell_cap=active_cell_cap,
        summary="Immune cells attacked unsupported, overbroad, stale, and benchmark-sensitive claims.",
    )
    stage_sequence.append("immune_challenge")

    reallocations = [
        ReallocationDecision(
            tick=tick,
            decision_id="realloc-001",
            from_organ=SuperOrgan.SCOUT,
            to_organ=SuperOrgan.IMMUNE,
            cell_count=_scale_organ_cell_count(by_organ[SuperOrgan.SCOUT], SuperOrgan.SCOUT, 6),
            reason="Scouting found enough raw material; contradiction pressure is now the bottleneck.",
            target_claim_ids=["claim-001", "claim-002", "claim-006", "claim-007"],
            outcome="Duplicate scout cell budget moved to immune review.",
        ),
        ReallocationDecision(
            tick=tick,
            decision_id="realloc-002",
            from_organ=SuperOrgan.CLAIM,
            to_organ=SuperOrgan.EXPERIMENT,
            cell_count=_scale_organ_cell_count(by_organ[SuperOrgan.CLAIM], SuperOrgan.CLAIM, 4),
            reason="Claim extraction stabilized; reproducibility and install/API probes now decide credibility.",
            target_claim_ids=["claim-004", "claim-008"],
            outcome="Claim cell budget shifted into experiment probes.",
        ),
    ]
    for decision in reallocations:
        signals.append(
            BoardSignal(
                tick=decision.tick,
                signal_type="reallocation",
                source_cell_id=_first_cell_id(by_organ[SuperOrgan.BRAIN], "brain-001"),
                organ=SuperOrgan.BRAIN,
                phase="brain_reallocation",
                summary=decision.reason,
                subject_id=decision.decision_id,
                confidence=0.86,
                payload=decision.model_dump(mode="json"),
            )
        )
    stage_sequence.append("brain_reallocation")

    experiment_cells = by_organ[SuperOrgan.EXPERIMENT]
    for claim in [claim for claim in claims if claim.experiment_count]:
        cell = experiment_cells[int(claim.claim_id.rsplit("-", 1)[-1]) % len(experiment_cells)]
        signals.append(
            BoardSignal(
                tick=tick + 1,
                signal_type="state_delta",
                source_cell_id=cell.cell_id,
                organ=cell.organ,
                phase="experiment_probe",
                summary=f"Experiment cell probed {claim.claim_id}.",
                subject_id=claim.claim_id,
                confidence=claim.confidence,
                payload={
                    "namespace": "truth.claim_graph",
                    "op": "attach_probe_result",
                    "experiment_count": claim.experiment_count,
                    "tested_by": claim.tested_by,
                },
            )
        )
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=experiment_cells,
        tick=tick + 2,
        phase="experiment_probe",
        active_cell_cap=active_cell_cap,
        summary="Experiment cells ran lightweight reproducibility, docs, repo, and benchmark sanity probes.",
    )
    stage_sequence.append("experiment_probe")

    synthesis_cells = by_organ[SuperOrgan.SYNTHESIS]
    final_verdict = "mixed: credible substrate, but benchmark/adoption claims need live proof"
    credibility_score = _credibility_score(claims)
    final_memo = _build_final_memo(
        target,
        claims,
        credibility_score,
        final_verdict,
        cell_count=cell_count,
    )
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=synthesis_cells,
        tick=tick,
        phase="synthesis",
        active_cell_cap=active_cell_cap,
        summary="Synthesis cells folded claim graph, immune pressure, probes, and brain decisions into a traceable verdict.",
        payload={"final_verdict": final_verdict, "credibility_score": credibility_score},
    )
    signals.append(
        BoardSignal(
            tick=tick,
            signal_type="completion",
            source_cell_id="synthesis-001",
            organ=SuperOrgan.SYNTHESIS,
            phase="synthesis",
            summary=f"Super DAN {cell_count}-cell deterministic showcase completed.",
            confidence=credibility_score,
            payload={
                "claim_count": len(claims),
                "verified_claims": [claim.claim_id for claim in claims if claim.status == ClaimStatus.VERIFIED],
                "disputed_claims": [claim.claim_id for claim in claims if claim.status == ClaimStatus.DISPUTED],
                "unsupported_claims": [claim.claim_id for claim in claims if claim.status == ClaimStatus.UNSUPPORTED],
            },
        )
    )
    stage_sequence.append("synthesis")

    retired_cell_ids = {
        *[cell.cell_id for cell in by_organ[SuperOrgan.SCOUT][-reallocations[0].cell_count :]],
        *[cell.cell_id for cell in by_organ[SuperOrgan.CLAIM][-reallocations[1].cell_count :]],
    }
    completed_cells = [
        cell.model_copy(update={"status": "retired" if cell.cell_id in retired_cell_ids else "completed"})
        for cell in cells
    ]
    max_active = max((wave.active_count for wave in waves), default=0)
    signal_counts = Counter(signal.signal_type for signal in signals)
    organ_counts = Counter(cell.organ.value for cell in completed_cells)

    return SuperOrganismReport(
        scenario=SuperOrganismScenario.TRUTH_AUDIT,
        organism_id=organism_id,
        target=target,
        cell_count=len(completed_cells),
        active_cell_cap=active_cell_cap,
        max_active_observed=max_active,
        organ_counts=dict(organ_counts),
        signal_counts=dict(signal_counts),
        stage_sequence=stage_sequence,
        final_verdict=final_verdict,
        score_label="Credibility Score",
        credibility_score=credibility_score,
        execution_family="research",
        final_memo=final_memo,
        caveat=(
            f"This is a deterministic coordination demo. It proves {cell_count}-cell organization "
            "and trace shape, not a live factual audit of the target."
        ),
        cells=completed_cells,
        evidence_refs=evidence_refs,
        claim_graph=claims,
        delivery_plan=[],
        reallocation_decisions=reallocations,
        activity_waves=waves,
        board_signals=signals,
    )


def _run_website_build_demo(
    target: str = "build a product website",
    *,
    organism_id: str = DEFAULT_SUPER_ORGANISM_ID,
    cell_count: int = DEFAULT_SUPER_ORGANISM_CELL_COUNT,
    active_cell_cap: int = DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP,
) -> SuperOrganismReport:
    """Run the deterministic website-build coordination showcase."""

    if active_cell_cap < 1:
        raise ValueError("active_cell_cap must be at least 1")
    target = _normalize_objective_text(target) or "build a product website"
    cells = build_super_organism_cells(cell_count=cell_count)
    by_organ: dict[SuperOrgan, list[SuperOrganismCell]] = {
        organ: [cell for cell in cells if cell.organ == organ] for organ in SuperOrgan
    }

    signals: list[BoardSignal] = []
    waves: list[ActivityWave] = []
    tick = 0
    stage_sequence: list[str] = []

    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=by_organ[SuperOrgan.BRAIN],
        tick=tick,
        phase="mission_lock",
        active_cell_cap=active_cell_cap,
        summary="Brain cells translated the prompt into a website-build mission, audience, sections, and done criteria.",
        payload={"scenario": SuperOrganismScenario.WEBSITE_BUILD.value},
    )
    stage_sequence.append("mission_lock")

    evidence_refs = _build_website_evidence_refs(target)
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=by_organ[SuperOrgan.SCOUT],
        tick=tick,
        phase="creative_discovery",
        active_cell_cap=active_cell_cap,
        summary="Scout cells mapped product story, visual references, motion cues, and implementation constraints.",
        payload={"evidence_refs": [ref.ref_id for ref in evidence_refs]},
    )
    stage_sequence.append("creative_discovery")

    delivery_plan = _build_website_delivery_plan(target, cell_count=cell_count)
    spec_cells = by_organ[SuperOrgan.CLAIM]
    for index, node in enumerate(delivery_plan):
        cell = spec_cells[index % len(spec_cells)]
        signals.append(
            BoardSignal(
                tick=tick,
                signal_type="state_delta",
                source_cell_id=cell.cell_id,
                organ=cell.organ,
                phase="site_specification",
                summary=f"Specified {node.node_id}: {node.title}",
                subject_id=node.node_id,
                confidence=0.76,
                payload={
                    "namespace": "build.website_plan",
                    "op": "upsert_delivery_node",
                    "status": node.status,
                    "outputs": node.outputs,
                },
            )
        )
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=spec_cells,
        tick=tick + 1,
        phase="site_specification",
        active_cell_cap=active_cell_cap,
        summary="Requirement cells converted the vague website ask into concrete page sections, outputs, and dependencies.",
    )
    stage_sequence.append("site_specification")

    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=by_organ[SuperOrgan.MEMORY],
        tick=tick,
        phase="design_system_memory",
        active_cell_cap=active_cell_cap,
        summary="Memory cells clustered repeated ideas into one design system, avoiding duplicate section and motion work.",
        payload={"clusters": ["narrative", "visual_system", "motion_system", "implementation"]},
    )
    stage_sequence.append("design_system_memory")

    immune_cells = by_organ[SuperOrgan.IMMUNE]
    quality_targets = ["node-002", "node-005", "node-008"]
    for index, node_id in enumerate(quality_targets):
        cell = immune_cells[index % len(immune_cells)]
        signals.append(
            BoardSignal(
                tick=tick,
                signal_type="state_delta",
                source_cell_id=cell.cell_id,
                organ=cell.organ,
                phase="design_red_team",
                summary=f"Red-team cell challenged weak design risk in {node_id}.",
                subject_id=node_id,
                confidence=0.82,
                payload={
                    "namespace": "build.website_plan",
                    "op": "challenge_delivery_node",
                    "risks": ["generic AI slop", "motion without purpose", "unclear product proof"],
                },
            )
        )
    signals.append(
        BoardSignal(
            tick=tick,
            signal_type="resource_request",
            source_cell_id="immune-001",
            organ=SuperOrgan.IMMUNE,
            phase="design_red_team",
            summary="Design red-team requested more animation/prototype cells because motion quality is the differentiator.",
            subject_id="node-005",
            confidence=0.88,
            payload={
                "request_type": "need_more_cells",
                "suggested_cell_count": _scale_total_cell_count(cell_count, 10),
                "needed_capability": "interaction prototyping",
                "target_node_ids": ["node-005", "node-006", "node-008"],
            },
        )
    )
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=immune_cells,
        tick=tick + 1,
        phase="design_red_team",
        active_cell_cap=active_cell_cap,
        summary="Immune cells attacked generic copy, generic layout, gratuitous animation, and weak proof points.",
    )
    stage_sequence.append("design_red_team")

    reallocations = [
        ReallocationDecision(
            tick=tick,
            decision_id="realloc-001",
            from_organ=SuperOrgan.SCOUT,
            to_organ=SuperOrgan.EXPERIMENT,
            cell_count=_scale_organ_cell_count(by_organ[SuperOrgan.SCOUT], SuperOrgan.SCOUT, 6),
            reason="Discovery is sufficient; animation prototyping is now the bottleneck.",
            target_claim_ids=["node-005", "node-006"],
            outcome="Scout cell budget moved to interaction probes.",
        ),
        ReallocationDecision(
            tick=tick,
            decision_id="realloc-002",
            from_organ=SuperOrgan.CLAIM,
            to_organ=SuperOrgan.SYNTHESIS,
            cell_count=_scale_organ_cell_count(by_organ[SuperOrgan.CLAIM], SuperOrgan.CLAIM, 4),
            reason="Page structure is stable; copy and final implementation packaging need more synthesis.",
            target_claim_ids=["node-001", "node-003", "node-007"],
            outcome="Requirement cell budget shifted into launch-copy synthesis.",
        ),
    ]
    for decision in reallocations:
        signals.append(
            BoardSignal(
                tick=decision.tick,
                signal_type="reallocation",
                source_cell_id=_first_cell_id(by_organ[SuperOrgan.BRAIN], "brain-001"),
                organ=SuperOrgan.BRAIN,
                phase="brain_reallocation",
                summary=decision.reason,
                subject_id=decision.decision_id,
                confidence=0.9,
                payload=decision.model_dump(mode="json"),
            )
        )
    stage_sequence.append("brain_reallocation")

    experiment_cells = by_organ[SuperOrgan.EXPERIMENT]
    for node_id in ["node-005", "node-006", "node-008"]:
        cell = experiment_cells[int(node_id.rsplit("-", 1)[-1]) % len(experiment_cells)]
        signals.append(
            BoardSignal(
                tick=tick + 1,
                signal_type="state_delta",
                source_cell_id=cell.cell_id,
                organ=cell.organ,
                phase="motion_probe",
                summary=f"Experiment cell defined prototype check for {node_id}.",
                subject_id=node_id,
                confidence=0.74,
                payload={
                    "namespace": "build.website_plan",
                    "op": "attach_motion_probe",
                    "probe": "animation purpose, performance budget, reduced-motion fallback",
                },
            )
        )
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=experiment_cells,
        tick=tick + 2,
        phase="motion_probe",
        active_cell_cap=active_cell_cap,
        summary="Experiment cells specified motion, interaction, performance, and responsive QA probes.",
    )
    stage_sequence.append("motion_probe")

    synthesis_cells = by_organ[SuperOrgan.SYNTHESIS]
    final_verdict = "website-build blueprint ready for native materialization"
    readiness_score = 0.74
    final_memo = _build_website_final_memo(
        target,
        delivery_plan,
        readiness_score,
        final_verdict,
        cell_count=cell_count,
    )
    tick = _emit_phase(
        signals=signals,
        waves=waves,
        cells=synthesis_cells,
        tick=tick,
        phase="implementation_packaging",
        active_cell_cap=active_cell_cap,
        summary="Synthesis cells assembled page blueprint, animation direction, implementation package, and QA checklist.",
        payload={"final_verdict": final_verdict, "build_readiness_score": readiness_score},
    )
    signals.append(
        BoardSignal(
            tick=tick,
            signal_type="completion",
            source_cell_id="synthesis-001",
            organ=SuperOrgan.SYNTHESIS,
            phase="implementation_packaging",
            summary=f"Super DAN {cell_count}-cell website-build deterministic showcase completed.",
            confidence=readiness_score,
            payload={
                "delivery_node_count": len(delivery_plan),
                "ready_nodes": [node.node_id for node in delivery_plan if node.status == "ready"],
                "live_build_required": [
                    node.node_id for node in delivery_plan if node.status == "live_build_required"
                ],
            },
        )
    )
    stage_sequence.append("implementation_packaging")

    retired_cell_ids = {
        *[cell.cell_id for cell in by_organ[SuperOrgan.SCOUT][-reallocations[0].cell_count :]],
        *[cell.cell_id for cell in by_organ[SuperOrgan.CLAIM][-reallocations[1].cell_count :]],
    }
    completed_cells = [
        cell.model_copy(update={"status": "retired" if cell.cell_id in retired_cell_ids else "completed"})
        for cell in cells
    ]
    max_active = max((wave.active_count for wave in waves), default=0)
    signal_counts = Counter(signal.signal_type for signal in signals)
    organ_counts = Counter(cell.organ.value for cell in completed_cells)

    return SuperOrganismReport(
        scenario=SuperOrganismScenario.WEBSITE_BUILD,
        organism_id=organism_id,
        target=target,
        cell_count=len(completed_cells),
        active_cell_cap=active_cell_cap,
        max_active_observed=max_active,
        organ_counts=dict(organ_counts),
        signal_counts=dict(signal_counts),
        stage_sequence=stage_sequence,
        final_verdict=final_verdict,
        score_label="Build Readiness Score",
        credibility_score=readiness_score,
        execution_family="code",
        final_memo=final_memo,
        caveat=(
            "This is a deterministic coordination demo. It produces a website-build blueprint "
            "and organism trace for Super DAN's own materialization path."
        ),
        cells=completed_cells,
        evidence_refs=evidence_refs,
        claim_graph=[],
        delivery_plan=delivery_plan,
        reallocation_decisions=reallocations,
        activity_waves=waves,
        board_signals=signals,
    )


def _emit_phase(
    *,
    signals: list[BoardSignal],
    waves: list[ActivityWave],
    cells: list[SuperOrganismCell],
    tick: int,
    phase: str,
    active_cell_cap: int,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> int:
    for offset in range(0, len(cells), active_cell_cap):
        wave_cells = cells[offset : offset + active_cell_cap]
        wave_tick = tick + offset // active_cell_cap
        waves.append(
            ActivityWave(
                tick=wave_tick,
                phase=phase,
                cell_ids=[cell.cell_id for cell in wave_cells],
            )
        )
        for cell in wave_cells:
            signals.append(
                BoardSignal(
                    tick=wave_tick,
                    signal_type="heartbeat",
                    source_cell_id=cell.cell_id,
                    organ=cell.organ,
                    phase=phase,
                    summary=summary,
                    confidence=0.75,
                    payload={
                        "role": cell.role,
                        "focus": cell.focus,
                        **dict(payload or {}),
                    },
                )
            )
    return tick + max((len(cells) - 1) // active_cell_cap + 1, 1)


def _focus_for_role(role: str) -> str:
    return role.replace("_", " ").replace("cell", "").strip()


def _build_evidence_refs(target: str) -> list[EvidenceRef]:
    return [
        EvidenceRef(
            ref_id="evidence:public-docs",
            label="Public documentation and examples",
            source_family="docs",
            summary=f"Docs lane for {target}; used to test whether claims are operationally specific.",
            trust_hint="medium",
        ),
        EvidenceRef(
            ref_id="evidence:source-repo",
            label="Repository and maintenance surface",
            source_family="repo",
            summary=f"Repo lane for {target}; used to inspect activity, examples, tests, and release hygiene.",
            trust_hint="medium",
        ),
        EvidenceRef(
            ref_id="evidence:benchmark-claims",
            label="Benchmark and performance claims",
            source_family="benchmark",
            summary=f"Benchmark lane for {target}; treated as weak until methodology is explicit and reproducible.",
            trust_hint="weak",
        ),
        EvidenceRef(
            ref_id="evidence:adoption-signals",
            label="Adoption and customer proof",
            source_family="market",
            summary=f"Adoption lane for {target}; separates named proof from vague popularity claims.",
            trust_hint="weak",
        ),
        EvidenceRef(
            ref_id="evidence:reproducibility-probe",
            label="Install/API reproducibility probe",
            source_family="experiment",
            summary=f"Experiment lane for {target}; checks whether a lightweight user can reproduce the basic path.",
            trust_hint="strong",
        ),
    ]


def _build_website_evidence_refs(target: str) -> list[EvidenceRef]:
    return [
        EvidenceRef(
            ref_id="brief:product-request",
            label="Product website request",
            source_family="brief",
            summary=f"User request lane for {target}; anchors the website around product positioning and launch intent.",
            trust_hint="synthetic",
        ),
        EvidenceRef(
            ref_id="brief:audience-positioning",
            label="Audience and positioning",
            source_family="strategy",
            summary="Defines who must understand the product, why it matters, and which proof points the page needs.",
            trust_hint="synthetic",
        ),
        EvidenceRef(
            ref_id="brief:visual-direction",
            label="Visual direction",
            source_family="design",
            summary="Frames a non-generic organism aesthetic: many small cells becoming one legible system.",
            trust_hint="synthetic",
        ),
        EvidenceRef(
            ref_id="brief:motion-system",
            label="Motion and interaction system",
            source_family="interaction",
            summary="Converts 'cool animation' into purposeful motion: signal flow, cell pulses, and reduced-motion fallback.",
            trust_hint="synthetic",
        ),
        EvidenceRef(
            ref_id="brief:implementation-scope",
            label="Implementation scope",
            source_family="engineering",
            summary="Separates deterministic blueprint output from Super DAN's later live file-writing pass.",
            trust_hint="synthetic",
        ),
    ]


def _execution_family_for_target(target: str) -> str:
    text = str(target or "").lower()
    coding_cues = (
        "build",
        "implement",
        "code",
        "website",
        "app",
        "frontend",
        "backend",
        "fix",
        "patch",
        "refactor",
    )
    research_cues = (
        "research",
        "investigate",
        "compare",
        "analyze",
        "analyse",
        "latest",
        "current",
        "evidence",
        "market",
        "paper",
    )
    if any(cue in text for cue in coding_cues) and any(cue in text for cue in research_cues):
        return "code_plus_research"
    if any(cue in text for cue in coding_cues):
        return "code"
    if any(cue in text for cue in research_cues):
        return "research"
    return "general_operator"


def _build_universal_evidence_refs(target: str, execution_family: str) -> list[EvidenceRef]:
    return [
        EvidenceRef(
            ref_id="objective:raw-request",
            label="Raw operator objective",
            source_family="objective",
            summary=f"Original operator request: {target}",
            trust_hint="synthetic",
        ),
        EvidenceRef(
            ref_id="contract:delivery-shape",
            label="Delivery contract",
            source_family="contract",
            summary="Defines the output artifact, acceptance checks, authority boundary, and stop condition.",
            trust_hint="synthetic",
        ),
        EvidenceRef(
            ref_id="context:workspace-and-sources",
            label="Context acquisition surface",
            source_family="context",
            summary="Represents the files, docs, live sources, and user-provided facts that live cells would inspect.",
            trust_hint="synthetic",
        ),
        EvidenceRef(
            ref_id="capability:execution-lanes",
            label="Execution lane selection",
            source_family="capability",
            summary=f"Routes the objective toward the {execution_family} lane before live execution.",
            trust_hint="synthetic",
        ),
        EvidenceRef(
            ref_id="gate:acceptance-and-risk",
            label="Acceptance and immune gates",
            source_family="quality",
            summary="Captures the risks, tests, proof requirements, and operator approval boundaries.",
            trust_hint="synthetic",
        ),
    ]


def _scale_total_cell_count(cell_count: int, reference_count: int) -> int:
    """Scale a 100-cell reference allocation down to the requested organism size."""

    scaled = round(cell_count * reference_count / _REFERENCE_SUPER_ORGANISM_CELL_COUNT)
    return max(1, min(cell_count, int(scaled)))


def _scale_organ_cell_count(
    cells: list[SuperOrganismCell],
    organ: SuperOrgan,
    reference_count: int,
) -> int:
    """Scale a reference reallocation against the current organ size."""

    organ_size = len(cells)
    if organ_size <= 0:
        return 1
    reference_organ_size = _DEFAULT_DISTRIBUTION[organ.value]
    scaled = round(organ_size * reference_count / reference_organ_size)
    return max(1, min(organ_size, int(scaled)))


def _first_cell_id(cells: list[SuperOrganismCell], fallback: str) -> str:
    return cells[0].cell_id if cells else fallback


def _cycled_cell_ids(
    cells: list[SuperOrganismCell],
    count: int,
    *,
    start: int = 0,
) -> list[str]:
    if not cells:
        return []
    return [cells[(start + offset) % len(cells)].cell_id for offset in range(max(1, count))]


def _ticket_id_for_node(node_id: str) -> str:
    return node_id.replace("node-", "ticket-")


def _build_universal_coordination_contract(
    target: str,
    execution_family: str,
    delivery_plan: list[DeliveryNode],
    *,
    by_organ: dict[SuperOrgan, list[SuperOrganismCell]],
) -> tuple[SharedBoardState, list[CoordinationTicket], list[HandoffPacket], FinalAuditGate]:
    phase_by_node = {
        "node-001": "objective_intake",
        "node-002": "objective_intake",
        "node-003": "context_acquisition",
        "node-004": "contract_decomposition",
        "node-005": "contract_immune_check",
        "node-006": "execution_probe",
        "node-007": "shared_memory_index",
        "node-008": "synthesis",
    }
    status_by_node = {
        "node-001": "completed",
        "node-002": "completed",
        "node-003": "completed",
        "node-004": "completed",
        "node-005": "completed",
        "node-006": "awaiting_live_execution",
        "node-007": "awaiting_result_handoff",
        "node-008": "awaiting_acceptance",
    }
    artifact_targets = {
        "node-001": ["objective.md", "done_criteria.json"],
        "node-002": ["authority_contract.json"],
        "node-003": ["context_inventory.json", "missing_context.md"],
        "node-004": ["work_graph.json"],
        "node-005": ["risk_register.json", "approval_gate.md"],
        "node-006": ["execution_packet.json", "tool_budget.json"],
        "node-007": ["result_packet.json", "trace_links.json"],
        "node-008": ["acceptance_summary.json", "residual_risks.md"],
    }
    acceptance_checks = {
        "node-001": [
            "The operator objective is preserved verbatim enough to stay faithful.",
            "The done condition names a concrete artifact or observable result.",
        ],
        "node-002": [
            "Authority boundaries are explicit.",
            "The chosen execution lane matches the objective family.",
        ],
        "node-003": [
            "Context gaps are surfaced before execution.",
            "Required evidence lanes are indexed on the board.",
        ],
        "node-004": [
            "The work graph is objective-specific rather than a canned scenario.",
            "Dependencies and per-subtask outputs are explicit.",
        ],
        "node-005": [
            "Ambiguity, safety, and weak-proof risks are challenged.",
            "Unsafe or unbounded execution remains blocked.",
        ],
        "node-006": [
            "A bounded native execution lane actually runs.",
            "The run produces a concrete artifact or workspace mutation.",
        ],
        "node-007": [
            "Execution results are written back into shared memory.",
            "Changed files, trace links, and residual blockers are retained.",
        ],
        "node-008": [
            "The final auditor can see the artifact, validator output, and residual risks.",
            "The organism does not exit while live execution tickets remain unresolved.",
        ],
    }
    blocker_by_node = {
        "node-006": "No live execution run has happened yet.",
        "node-007": "Result integration depends on the native execution lane publishing outputs.",
        "node-008": "Acceptance synthesis depends on execution plus validator closure.",
    }
    cursor_by_organ: dict[SuperOrgan, int] = {organ: 0 for organ in SuperOrgan}
    tickets: list[CoordinationTicket] = []
    assigned_cells: set[str] = set()
    for node in delivery_plan:
        organ_cells = by_organ[node.organ]
        start = cursor_by_organ[node.organ]
        cell_ids = _cycled_cell_ids(organ_cells, node.assigned_cell_count, start=start)
        if organ_cells:
            cursor_by_organ[node.organ] = (start + min(len(organ_cells), max(1, node.assigned_cell_count))) % len(
                organ_cells
            )
        assigned_cells.update(cell_ids)
        tickets.append(
            CoordinationTicket(
                ticket_id=_ticket_id_for_node(node.node_id),
                node_id=node.node_id,
                title=node.title,
                organ=node.organ,
                phase=phase_by_node[node.node_id],
                status=status_by_node[node.node_id],
                owner_cell_id=cell_ids[0] if cell_ids else _first_cell_id(organ_cells, f"{node.organ.value}-001"),
                cell_ids=cell_ids
                or [_first_cell_id(organ_cells, f"{node.organ.value}-001")],
                dependency_ticket_ids=[_ticket_id_for_node(dep) for dep in node.dependencies],
                artifact_targets=list(artifact_targets[node.node_id]),
                acceptance_checks=list(acceptance_checks[node.node_id]),
                blocker=blocker_by_node.get(node.node_id, ""),
                notes=list(node.notes),
            )
        )
    ticket_by_node = {ticket.node_id: ticket for ticket in tickets}
    all_cell_ids = {
        cell.cell_id
        for organ_cells in by_organ.values()
        for cell in organ_cells
    }
    reserve_cell_ids = sorted(all_cell_ids - assigned_cells)
    packets = [
        HandoffPacket(
            packet_id="packet-001",
            phase="objective_intake",
            packet_type="objective",
            status="published",
            from_ticket_id=ticket_by_node["node-001"].ticket_id,
            to_ticket_id=ticket_by_node["node-002"].ticket_id,
            from_cell_id=ticket_by_node["node-001"].owner_cell_id,
            to_cell_id=ticket_by_node["node-002"].owner_cell_id,
            summary="Brain cells published the raw objective contract and done condition.",
            payload_keys=["objective", "done_condition", "operator_deliverable"],
            release_condition="Published after objective intake completes.",
        ),
        HandoffPacket(
            packet_id="packet-002",
            phase="context_acquisition",
            packet_type="context",
            status="published",
            from_ticket_id=ticket_by_node["node-002"].ticket_id,
            to_ticket_id=ticket_by_node["node-003"].ticket_id,
            from_cell_id=ticket_by_node["node-002"].owner_cell_id,
            to_cell_id=ticket_by_node["node-003"].owner_cell_id,
            summary="Authority and capability constraints were handed to scout cells.",
            payload_keys=["execution_family", "authority_boundary", "approval_boundary"],
            release_condition="Published after capability and authority contract closes.",
        ),
        HandoffPacket(
            packet_id="packet-003",
            phase="contract_decomposition",
            packet_type="work_graph",
            status="published",
            from_ticket_id=ticket_by_node["node-003"].ticket_id,
            to_ticket_id=ticket_by_node["node-004"].ticket_id,
            from_cell_id=ticket_by_node["node-003"].owner_cell_id,
            to_cell_id=ticket_by_node["node-004"].owner_cell_id,
            summary="Scout evidence and context gaps were atomized into the work graph.",
            payload_keys=["evidence_refs", "missing_context", "workspace_signals"],
            release_condition="Published after scout context acquisition finishes.",
        ),
        HandoffPacket(
            packet_id="packet-004",
            phase="contract_immune_check",
            packet_type="risk_gate",
            status="published",
            from_ticket_id=ticket_by_node["node-004"].ticket_id,
            to_ticket_id=ticket_by_node["node-005"].ticket_id,
            from_cell_id=ticket_by_node["node-004"].owner_cell_id,
            to_cell_id=ticket_by_node["node-005"].owner_cell_id,
            summary="The work graph was handed to immune cells for ambiguity and risk pressure.",
            payload_keys=["subtasks", "dependencies", "acceptance_outputs"],
            release_condition="Published after contract decomposition stabilizes.",
        ),
        HandoffPacket(
            packet_id="packet-005",
            phase="execution_probe",
            packet_type="execution",
            status="published",
            from_ticket_id=ticket_by_node["node-005"].ticket_id,
            to_ticket_id=ticket_by_node["node-006"].ticket_id,
            from_cell_id=ticket_by_node["node-005"].owner_cell_id,
            to_cell_id=ticket_by_node["node-006"].owner_cell_id,
            summary="Immune gates published the bounded execution packet for the native live lane.",
            payload_keys=["risk_list", "tool_budget", "stop_condition", "approval_boundary"],
            release_condition="Published once the risk gate is explicit and bounded.",
        ),
        HandoffPacket(
            packet_id="packet-006",
            phase="shared_memory_index",
            packet_type="result",
            status="pending",
            from_ticket_id=ticket_by_node["node-006"].ticket_id,
            to_ticket_id=ticket_by_node["node-007"].ticket_id,
            from_cell_id=ticket_by_node["node-006"].owner_cell_id,
            to_cell_id=ticket_by_node["node-007"].owner_cell_id,
            summary="The result packet is reserved for changed files, artifacts, and execution notes.",
            payload_keys=["changed_paths", "artifact_manifest", "trace_links", "run_notes"],
            release_condition="Publish only after the native live lane makes a real artifact or workspace change.",
        ),
        HandoffPacket(
            packet_id="packet-007",
            phase="synthesis",
            packet_type="acceptance",
            status="pending",
            from_ticket_id=ticket_by_node["node-007"].ticket_id,
            to_ticket_id=ticket_by_node["node-008"].ticket_id,
            from_cell_id=ticket_by_node["node-007"].owner_cell_id,
            to_cell_id=ticket_by_node["node-008"].owner_cell_id,
            summary="The acceptance packet is reserved for validator output and final artifact judgment.",
            payload_keys=["validation_summary", "result_summary", "residual_risks"],
            release_condition="Publish only after result integration and validator closure.",
        ),
    ]
    shared_board = SharedBoardState(
        objective=target,
        execution_family=execution_family,
        memory_clusters=[
            "objective",
            "contracts",
            "context",
            "risk",
            "execution",
            "acceptance",
        ],
        completed_ticket_ids=[
            ticket.ticket_id for ticket in tickets if ticket.status == "completed"
        ],
        waiting_ticket_ids=[
            ticket.ticket_id
            for ticket in tickets
            if ticket.status
            in {
                "awaiting_live_execution",
                "awaiting_result_handoff",
                "awaiting_acceptance",
            }
        ],
        blocked_ticket_ids=[ticket.ticket_id for ticket in tickets if ticket.status == "blocked"],
        reserve_cell_ids=reserve_cell_ids,
        published_packet_ids=[packet.packet_id for packet in packets if packet.status == "published"],
        pending_packet_ids=[packet.packet_id for packet in packets if packet.status == "pending"],
        notes=[
            "Every logical cell is accounted for either on a ticket or in the reserve pool.",
            "The board refuses closure until execution, result handoff, and acceptance tickets resolve in order.",
        ],
    )
    final_audit = FinalAuditGate(
        auditor_cell_id=ticket_by_node["node-008"].owner_cell_id,
        reviewer_cell_ids=[
            ticket_by_node["node-005"].owner_cell_id,
            ticket_by_node["node-007"].owner_cell_id,
        ],
        status="continue",
        satisfied=False,
        summary=(
            "The organism is internally organized and execution-ready, but the final audit stays open until "
            "the native execution lane publishes a result packet, shared memory integrates that result, and "
            "acceptance synthesis sees validator-backed evidence instead of only a planned contract."
        ),
        acceptance_checks=[
            "Native execution ticket produced a concrete artifact or workspace mutation.",
            "Result handoff packet was published into shared memory.",
            "Validator-backed acceptance packet was published before exit.",
        ],
        blocker_ticket_ids=[
            ticket_by_node["node-006"].ticket_id,
            ticket_by_node["node-007"].ticket_id,
            ticket_by_node["node-008"].ticket_id,
        ],
        residual_risks=[
            "No live artifact or workspace mutation has been observed in deterministic mode.",
            "Result integration is still a reserved packet rather than a published packet.",
            "Acceptance synthesis would be premature without validator-backed evidence.",
        ],
        next_action=(
            "Run the native execution lane, publish packet-006 and packet-007, then rerun the final audit."
        ),
    )
    return shared_board, tickets, packets, final_audit


def _build_universal_delivery_plan(
    target: str,
    execution_family: str,
    *,
    cell_count: int,
) -> list[DeliveryNode]:
    return [
        DeliveryNode(
            node_id="node-001",
            title="Objective contract",
            organ=SuperOrgan.BRAIN,
            status="ready",
            assigned_cell_count=_scale_total_cell_count(cell_count, 6),
            outputs=["objective statement", "done condition", "operator-facing deliverable"],
            notes=[
                f"Treat the full request as the objective: {target}.",
                "Do not switch to a canned scenario unless the operator explicitly asks for one.",
            ],
        ),
        DeliveryNode(
            node_id="node-002",
            title="Capability and authority contract",
            organ=SuperOrgan.BRAIN,
            status="ready",
            assigned_cell_count=_scale_total_cell_count(cell_count, 6),
            dependencies=["node-001"],
            outputs=["execution lane", "allowed authority", "approval boundary"],
            notes=[
                f"Selected execution family: {execution_family}.",
                "Live mode should execute inside Super DAN or an operator-approved lane according to this contract.",
            ],
        ),
        DeliveryNode(
            node_id="node-003",
            title="Context acquisition workstream",
            organ=SuperOrgan.SCOUT,
            status="planned",
            assigned_cell_count=_scale_total_cell_count(cell_count, 18),
            dependencies=["node-001", "node-002"],
            outputs=["workspace/source inventory", "missing context list", "evidence refs"],
            notes=[
                "Scout cells identify what must be read or fetched before execution.",
                "This keeps context acquisition objective-first instead of routing to a canned demo.",
            ],
        ),
        DeliveryNode(
            node_id="node-004",
            title="Work graph decomposition",
            organ=SuperOrgan.CLAIM,
            status="ready",
            assigned_cell_count=_scale_total_cell_count(cell_count, 20),
            dependencies=["node-001"],
            outputs=["subtasks", "dependencies", "per-subtask acceptance outputs"],
            notes=[
                "Claim cells are used here as contract atomizers, not truth-audit claim writers.",
                "The work graph should stay objective-specific and bounded.",
            ],
        ),
        DeliveryNode(
            node_id="node-005",
            title="Immune risk and ambiguity challenge",
            organ=SuperOrgan.IMMUNE,
            status="ready",
            assigned_cell_count=_scale_total_cell_count(cell_count, 18),
            dependencies=["node-002", "node-004"],
            outputs=["ambiguity list", "risk list", "approval blockers", "unsafe-claim blockers"],
            notes=[
                "Immune cells challenge vague objectives before live cells spend budget.",
                "This is the guardrail that keeps universal agents from pretending all tasks are already safe and clear.",
            ],
        ),
        DeliveryNode(
            node_id="node-006",
            title="Native execution lane",
            organ=SuperOrgan.EXPERIMENT,
            status="live_build_required",
            assigned_cell_count=_scale_total_cell_count(cell_count, 10),
            dependencies=["node-002", "node-003", "node-004", "node-005"],
            outputs=["Super DAN execution packet", "tool budget", "first bounded run objective"],
            notes=[
                "This is where a live Super DAN implementation should do the selected work, not just print a plan.",
                "Current deterministic mode prepares native execution artifacts and supported materializers.",
            ],
        ),
        DeliveryNode(
            node_id="node-007",
            title="Result integration and shared memory update",
            organ=SuperOrgan.MEMORY,
            status="live_build_required",
            assigned_cell_count=_scale_total_cell_count(cell_count, 12),
            dependencies=["node-006"],
            outputs=["result summary", "trace links", "retained decisions", "next-pass context"],
            notes=[
                "Live cells should write back what changed, what was learned, and what remains blocked.",
                "The shared memory board keeps follow-up passes grounded in the same trace.",
            ],
        ),
        DeliveryNode(
            node_id="node-008",
            title="Acceptance synthesis",
            organ=SuperOrgan.SYNTHESIS,
            status="planned",
            assigned_cell_count=_scale_total_cell_count(cell_count, 10),
            dependencies=["node-005", "node-006", "node-007"],
            outputs=["final artifact", "verification summary", "residual risks", "operator next step"],
            notes=[
                "Synthesis cells decide whether the objective is done, needs another bounded pass, or needs clarification.",
                "The review loop belongs to Super DAN for this command, not to a separate product shell.",
            ],
        ),
    ]


def _build_universal_final_memo(
    target: str,
    delivery_plan: list[DeliveryNode],
    shared_board: SharedBoardState,
    coordination_tickets: list[CoordinationTicket],
    handoff_packets: list[HandoffPacket],
    final_audit: FinalAuditGate,
    readiness_score: float,
    verdict: str,
    execution_family: str,
    *,
    cell_count: int,
) -> str:
    live_nodes = [node.node_id for node in delivery_plan if node.status == "live_build_required"]
    blockers = [node.node_id for node in delivery_plan if node.status == "needs_input"]
    reserve_cells = ", ".join(shared_board.reserve_cell_ids) or "none"
    return (
        f"Super DAN's {cell_count}-cell deterministic universal-agent organism accepts the objective "
        f"'{target}' and treats it as {verdict}. Execution family: {execution_family}. "
        f"Execution readiness score: {readiness_score:.2f}. Delivery nodes: {len(delivery_plan)}. "
        f"Board tickets: {len(coordination_tickets)}. Handoff packets: {len(handoff_packets)}. "
        f"Live-execution nodes: {', '.join(live_nodes) or 'none'}. Input blockers: "
        f"{', '.join(blockers) or 'none'}. Shared board: {len(shared_board.completed_ticket_ids)} completed tickets, "
        f"{len(shared_board.waiting_ticket_ids)} waiting tickets, {len(shared_board.published_packet_ids)} published "
        f"handoff packets, {len(shared_board.pending_packet_ids)} pending packets, reserve cells {reserve_cells}. "
        f"Final audit: {final_audit.status} until {', '.join(final_audit.blocker_ticket_ids) or 'none'} clear. "
        f"The organized behavior is the point: brain cells "
        "lock objective and authority, scouts acquire context, contract cells decompose work, "
        "memory cells maintain the board, immune cells challenge ambiguity and risk, experiment "
        "cells prepare native execution probes, typed packets carry handoffs between tickets, "
        "and synthesis cells decide done/continue/clarify only after the final audit gate."
    )


def _build_website_delivery_plan(target: str, *, cell_count: int) -> list[DeliveryNode]:
    return [
        DeliveryNode(
            node_id="node-001",
            title="Product narrative and positioning",
            organ=SuperOrgan.BRAIN,
            status="ready",
            assigned_cell_count=_scale_total_cell_count(cell_count, 6),
            outputs=[
                "one-line promise",
                "three proof pillars",
                "primary call to action",
            ],
            notes=[
                f"Anchor the page around the actual request: {target}.",
                "The selling point is organization and synchronized cell behavior, not raw agent count.",
            ],
        ),
        DeliveryNode(
            node_id="node-002",
            title="Visual direction: organism command theater",
            organ=SuperOrgan.SCOUT,
            status="ready",
            assigned_cell_count=_scale_total_cell_count(cell_count, 12),
            dependencies=["node-001"],
            outputs=[
                "dark lab canvas with warm signal accents",
                f"{cell_count}-cell field motif",
                "organ-specific color tokens",
            ],
            notes=[
                "Avoid generic SaaS gradients; make the cell swarm the distinctive artifact.",
                "Use contrast between tiny autonomous cells and one composed macro-organism.",
            ],
        ),
        DeliveryNode(
            node_id="node-003",
            title="Information architecture and section sequence",
            organ=SuperOrgan.CLAIM,
            status="ready",
            assigned_cell_count=_scale_total_cell_count(cell_count, 14),
            dependencies=["node-001", "node-002"],
            outputs=[
                "hero",
                "how the organism works",
                "coordination trace",
                "contracts and safety",
                "demo call to action",
            ],
            notes=[
                "Each section should show division of labor and then synthesis.",
                f"Do not imply more than the configured {cell_count}-cell organism is active.",
            ],
        ),
        DeliveryNode(
            node_id="node-004",
            title=f"Hero animation: {cell_count} cells synchronize into organs",
            organ=SuperOrgan.EXPERIMENT,
            status="live_build_required",
            assigned_cell_count=_scale_total_cell_count(cell_count, 10),
            dependencies=["node-002", "node-003"],
            outputs=[
                "cell pulse choreography",
                "organ clustering transition",
                "scheduler cap visual",
            ],
            notes=[
                "The animation should teach the product: many cells, bounded concurrency, one verdict or build package.",
                "Respect reduced-motion and avoid GPU-heavy particle spam.",
            ],
        ),
        DeliveryNode(
            node_id="node-005",
            title="Interaction system: signal trails and traceable contracts",
            organ=SuperOrgan.EXPERIMENT,
            status="live_build_required",
            assigned_cell_count=_scale_total_cell_count(cell_count, 10),
            dependencies=["node-004"],
            outputs=[
                "scroll reveal waves",
                "hover cell details",
                "board signal timeline",
                "contract status cards",
            ],
            notes=[
                "Motion must reveal causality: scout -> claim -> immune -> experiment -> synthesis.",
                "Interactions should make organization obvious within five seconds.",
            ],
        ),
        DeliveryNode(
            node_id="node-006",
            title="Frontend implementation plan",
            organ=SuperOrgan.SYNTHESIS,
            status="planned",
            assigned_cell_count=_scale_total_cell_count(cell_count, 8),
            dependencies=["node-003", "node-004", "node-005"],
            outputs=[
                "component breakdown",
                "CSS token map",
                "animation state map",
                "responsive QA checklist",
            ],
            notes=[
                "Good first live slice: one static page with CSS/JS animation, then wire product data later.",
                "Super DAN can materialize this through its own deterministic artifact path before a later live mode exists.",
            ],
        ),
        DeliveryNode(
            node_id="node-007",
            title="Copy and proof points",
            organ=SuperOrgan.IMMUNE,
            status="needs_input",
            assigned_cell_count=_scale_total_cell_count(cell_count, 12),
            dependencies=["node-001"],
            outputs=[
                "claim-safe headline set",
                "proof placeholders",
                "limitations copy",
            ],
            notes=[
                "Needs real product facts before public launch copy can be strong.",
                "Immune cells should block benchmark/adoption claims that are not yet proven.",
            ],
        ),
        DeliveryNode(
            node_id="node-008",
            title="QA, performance, and accessibility",
            organ=SuperOrgan.IMMUNE,
            status="planned",
            assigned_cell_count=_scale_total_cell_count(cell_count, 8),
            dependencies=["node-004", "node-005", "node-006"],
            outputs=[
                "Lighthouse budget",
                "keyboard and reduced-motion checks",
                "mobile breakpoint review",
                "copy risk review",
            ],
            notes=[
                "A flashy organism demo fails if it is slow, inaccessible, or overclaims.",
                "Quality gates should be visible in the report, not hidden as engineering cleanup.",
            ],
        ),
    ]


def _build_website_final_memo(
    target: str,
    delivery_plan: list[DeliveryNode],
    readiness_score: float,
    verdict: str,
    *,
    cell_count: int,
) -> str:
    live_nodes = [node.node_id for node in delivery_plan if node.status == "live_build_required"]
    input_nodes = [node.node_id for node in delivery_plan if node.status == "needs_input"]
    return (
        f"Super DAN's {cell_count}-cell deterministic website organism treats {target} as {verdict}. "
        f"Build readiness score: {readiness_score:.2f}. Delivery nodes: {len(delivery_plan)}. "
        f"Live-build nodes: {', '.join(live_nodes) or 'none'}. Nodes needing user/product input: "
        f"{', '.join(input_nodes) or 'none'}. The organized behavior is the point: brain cells lock "
        "the mission, scouts map direction, requirement cells atomize the page, memory cells merge "
        "the system, immune cells block generic design and unsafe claims, experiment cells define motion "
        "probes, and synthesis cells hand off a buildable website contract."
    )


def _build_claim_graph(target: str) -> list[ClaimNode]:
    return [
        ClaimNode(
            claim_id="claim-001",
            text=f"{target} is production-ready for complex agent orchestration.",
            cluster="capability",
            status=ClaimStatus.UNSUPPORTED,
            confidence=0.42,
            support_count=1,
            challenge_count=4,
            evidence_refs=["evidence:public-docs", "evidence:source-repo"],
            challenged_by=["immune-001", "immune-006", "immune-012", "immune-018"],
            notes=[
                "Production-ready is broader than docs/examples alone.",
                "Needs live reliability, recovery, and operator-boundary evidence.",
            ],
        ),
        ClaimNode(
            claim_id="claim-002",
            text=f"{target} materially outperforms other agent frameworks on benchmarks.",
            cluster="proof",
            status=ClaimStatus.DISPUTED,
            confidence=0.34,
            support_count=1,
            challenge_count=5,
            experiment_count=1,
            evidence_refs=["evidence:benchmark-claims"],
            challenged_by=["immune-002", "immune-004", "immune-010", "immune-014", "immune-017"],
            tested_by=["experiment-004"],
            notes=[
                "Benchmark claims are high-variance unless methodology and baselines are inspectable.",
                "Experiment cells mark this as live-check dependent.",
            ],
        ),
        ClaimNode(
            claim_id="claim-003",
            text=f"{target} has public docs and examples sufficient for first-pass evaluation.",
            cluster="capability",
            status=ClaimStatus.VERIFIED,
            confidence=0.78,
            support_count=3,
            challenge_count=1,
            evidence_refs=["evidence:public-docs"],
            challenged_by=["immune-007"],
            notes=["Docs/examples are enough for a first-pass audit, not enough for production proof."],
        ),
        ClaimNode(
            claim_id="claim-004",
            text=f"{target} shows signs of active maintenance and inspectable implementation work.",
            cluster="reproducibility",
            status=ClaimStatus.NEEDS_LIVE_CHECK,
            confidence=0.58,
            support_count=2,
            challenge_count=1,
            experiment_count=2,
            evidence_refs=["evidence:source-repo", "evidence:reproducibility-probe"],
            challenged_by=["immune-003"],
            tested_by=["experiment-001", "experiment-003"],
            notes=["Repo-health judgments should be refreshed live before being used as a factual conclusion."],
        ),
        ClaimNode(
            claim_id="claim-005",
            text=f"{target} integrates with common developer workflows and tools.",
            cluster="capability",
            status=ClaimStatus.VERIFIED,
            confidence=0.69,
            support_count=2,
            challenge_count=1,
            evidence_refs=["evidence:public-docs", "evidence:source-repo"],
            challenged_by=["immune-009"],
            notes=["Integration breadth looks plausible in the demo graph but needs live connector checks."],
        ),
        ClaimNode(
            claim_id="claim-006",
            text=f"{target} has strong adoption or customer proof.",
            cluster="adoption",
            status=ClaimStatus.DISPUTED,
            confidence=0.31,
            support_count=1,
            challenge_count=4,
            evidence_refs=["evidence:adoption-signals"],
            challenged_by=["immune-005", "immune-008", "immune-011", "immune-016"],
            notes=[
                "Adoption proof needs named users, usage evidence, or independent references.",
                "Generic popularity signals are not enough.",
            ],
        ),
        ClaimNode(
            claim_id="claim-007",
            text=f"{target} is transparent about evaluation methodology and limitations.",
            cluster="proof",
            status=ClaimStatus.UNSUPPORTED,
            confidence=0.39,
            support_count=1,
            challenge_count=3,
            evidence_refs=["evidence:benchmark-claims", "evidence:public-docs"],
            challenged_by=["immune-013", "immune-015", "immune-018"],
            notes=["Limitations and methodology often decide whether a strong claim is credible or hype."],
        ),
        ClaimNode(
            claim_id="claim-008",
            text=f"{target} has a reproducible install or API path for a lightweight evaluator.",
            cluster="reproducibility",
            status=ClaimStatus.NEEDS_LIVE_CHECK,
            confidence=0.55,
            support_count=2,
            challenge_count=1,
            experiment_count=3,
            evidence_refs=["evidence:reproducibility-probe", "evidence:public-docs"],
            challenged_by=["immune-006"],
            tested_by=["experiment-001", "experiment-002", "experiment-005"],
            notes=["This is exactly where a later live mode should run tools instead of simulating probes."],
        ),
    ]


def _credibility_score(claims: list[ClaimNode]) -> float:
    if not claims:
        return 0.0
    weighted = 0.0
    for claim in claims:
        status_weight = {
            ClaimStatus.VERIFIED: 1.0,
            ClaimStatus.NEEDS_LIVE_CHECK: 0.72,
            ClaimStatus.DISPUTED: 0.35,
            ClaimStatus.UNSUPPORTED: 0.25,
        }[claim.status]
        weighted += claim.confidence * status_weight
    return round(min(max(weighted / len(claims) + 0.16, 0.0), 1.0), 2)


def _build_final_memo(
    target: str,
    claims: list[ClaimNode],
    credibility_score: float,
    verdict: str,
    *,
    cell_count: int,
) -> str:
    verified = [claim.claim_id for claim in claims if claim.status == ClaimStatus.VERIFIED]
    disputed = [claim.claim_id for claim in claims if claim.status == ClaimStatus.DISPUTED]
    unsupported = [claim.claim_id for claim in claims if claim.status == ClaimStatus.UNSUPPORTED]
    live_check = [claim.claim_id for claim in claims if claim.status == ClaimStatus.NEEDS_LIVE_CHECK]
    return (
        f"Super DAN's {cell_count}-cell deterministic organism treats {target} as {verdict}. "
        f"Credibility score: {credibility_score:.2f}. Verified claims: {', '.join(verified) or 'none'}. "
        f"Disputed claims: {', '.join(disputed) or 'none'}. Unsupported claims: {', '.join(unsupported) or 'none'}. "
        f"Claims requiring live checks: {', '.join(live_check) or 'none'}. The organized behavior is the point: "
        "scouts gather, claim cells atomize, memory clusters, immune cells attack, experiment cells probe, "
        "brain cells reallocate, and synthesis cells produce one traceable verdict."
    )


__all__ = [
    "BoardSignal",
    "ClaimNode",
    "ClaimStatus",
    "DEFAULT_SUPER_ORGANISM_ACTIVE_CELL_CAP",
    "DEFAULT_SUPER_ORGANISM_CELL_COUNT",
    "DEFAULT_SUPER_ORGANISM_ID",
    "DEFAULT_SUPER_ORGANISM_TARGET",
    "DEFAULT_SUPER_ORGANISM_TRUTH_TARGET",
    "DeliveryNode",
    "EvidenceRef",
    "ReallocationDecision",
    "SuperOrgan",
    "SuperOrganismCell",
    "SuperOrganismReport",
    "SuperOrganismScenario",
    "build_super_organism_cells",
    "resolve_super_organism_distribution",
    "resolve_super_organism_scenario",
    "run_super_organism_demo",
]
