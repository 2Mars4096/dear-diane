from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from dan.worker import StandaloneWorkerRunner
from dan.worker.core.acquisition import LocalAcquisitionProvider
from dan.worker.core.contracts import AcquisitionFamily, AcquisitionPolicy, AcquisitionSource, ExecutionRequest
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse
from dan.worker.core.memory import InMemoryLifecycleProvider
from dan.worker.core.model import WorkerDefinition

CANONICAL_TASK_CLASSES = ("coding", "research", "structured_review")
PROMOTION_DIMENSIONS = ("quality", "contract_adherence", "budget_discipline", "recoverability")


@dataclass(frozen=True, slots=True)
class _GateScore:
    quality: int
    contract_adherence: int
    budget_discipline: int
    recoverability: int

    @property
    def total(self) -> int:
        return (
            self.quality
            + self.contract_adherence
            + self.budget_discipline
            + self.recoverability
        )


@dataclass(frozen=True, slots=True)
class _BaselineOutcome:
    prompt: str
    selected_refs: list[str]
    capability_ids: list[str]
    has_continuation: bool


class _AcceptanceCompletionProvider:
    def __init__(self) -> None:
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        task_class = str(request.metadata.get("task_class") or "unknown")
        payload = {
            "task_class": task_class,
            "selected_refs": [block.ref_id for block in request.evidence if block.ref_id],
            "capabilities": [capability.capability_id for capability in request.capabilities],
            "contract_seen": (
                "Definition of done:" in request.system_prompt
                and "Expected return shape:" in request.system_prompt
            ),
        }
        if task_class == "coding":
            payload["plan"] = "PATCH_PLAN_READY"
        elif task_class == "research":
            payload["citations"] = [
                block.ref_id
                for block in request.evidence
                if block.ref_id and block.ref_id.startswith(f"{AcquisitionFamily.FILE_INVENTORY.value}:")
            ]
        elif task_class == "structured_review":
            payload["verdict"] = "contract-pass"
            payload["memory_refs"] = [
                block.ref_id
                for block in request.evidence
                if block.ref_id and block.ref_id.startswith(f"{AcquisitionFamily.MEMORY_CATALOG.value}:")
            ]
        return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw=payload)


class _RecordingAcquisitionProvider(LocalAcquisitionProvider):
    def __init__(self, *, tool_catalogs: dict[str, object] | None = None) -> None:
        super().__init__(tool_catalogs=tool_catalogs)
        self.discover_calls: list[dict[str, object]] = []
        self.expand_calls: list[dict[str, object]] = []

    async def discover(self, request):
        self.discover_calls.append(
            {
                "source_id": request.source.source_id,
                "family": str(request.source.family),
            }
        )
        return await super().discover(request)

    async def expand(self, request):
        self.expand_calls.append(
            {
                "source_id": request.source.source_id,
                "family": str(request.source.family),
                "refs": [selection.ref_id for selection in request.selections],
            }
        )
        return await super().expand(request)


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _parse_payload(result) -> dict[str, object]:
    return json.loads(result.outputs["result"])


async def _run_coding_task(tmp_path: Path):
    root = tmp_path / "coding"
    _write_file(root / "src/apply_patch.py", "PATCH_TARGET_DETAIL\nfix the worker gate path\n")
    _write_file(root / "notes/random.md", "IRRELEVANT_CODING_NOISE\nleave this out of the prompt\n")

    worker = WorkerDefinition(
        id="coding-cell",
        role="coder",
        instruction="Produce a compact patch plan from the selected evidence only.",
        model="stub-model",
        tool_ids=["pytest_runner", "file_write"],
        acquisition_policy=AcquisitionPolicy(
            max_selected_items_per_source=1,
            max_expanded_items_per_source=1,
        ),
    )
    completion_provider = _AcceptanceCompletionProvider()
    acquisition_provider = _RecordingAcquisitionProvider(
        tool_catalogs={
            f"worker-tools::{worker.id}": [
                {
                    "id": "pytest_runner",
                    "summary": "Run focused worker-cell regression tests.",
                    "details": "Focus the worker test basket on the touched path.",
                },
                {
                    "id": "file_write",
                    "summary": "Write a patch to the current workspace.",
                    "details": "Apply a compact code change when the contract allows it.",
                },
            ]
        }
    )
    runner = StandaloneWorkerRunner(
        completion_provider=completion_provider,
        acquisition_provider=acquisition_provider,
        memory_provider=InMemoryLifecycleProvider(),
    )
    session = runner.create_session(worker, metadata={"surface": "acceptance-gate"})
    request = ExecutionRequest.from_harness(
        task="Inspect src/apply_patch.py and use pytest_runner to propose a minimal patch plan.",
        acquisition={
            "sources": [
                AcquisitionSource(
                    source_id="workspace",
                    family=AcquisitionFamily.FILE_INVENTORY,
                    metadata={"root": str(root)},
                )
            ]
        },
        output_contract={
            "definition_of_done": "Return a compact JSON patch plan that only cites the selected file and tool.",
            "expected_return_shape": '{"task_class": "coding", "selected_refs": [...], "capabilities": [...], "plan": "..."}',
        },
        metadata={"task_class": "coding"},
    )

    result = await runner.run(worker, request, session=session)
    return result, completion_provider.requests[0], acquisition_provider, session


async def _run_research_task(tmp_path: Path):
    root = tmp_path / "research"
    _write_file(root / "evidence/worker_cell_strategy.md", "RESEARCH_PRIMARY_SOURCE\ncells should prefer refs and summaries\n")
    _write_file(root / "evidence/worker_cell_risks.md", "RESEARCH_RISK_SOURCE\nhidden shared prompts collapse the membrane\n")
    _write_file(root / "evidence/lunch.md", "IRRELEVANT_RESEARCH_NOISE\nno bearing on the worker cell\n")

    worker = WorkerDefinition(
        id="research-cell",
        role="researcher",
        instruction="Cross-check the relevant sources and surface citations.",
        model="stub-model",
        tool_ids=["web_search", "cite_sources"],
        acquisition_policy=AcquisitionPolicy(
            max_selected_items_per_source=2,
            max_expanded_items_per_source=2,
        ),
    )
    completion_provider = _AcceptanceCompletionProvider()
    acquisition_provider = _RecordingAcquisitionProvider(
        tool_catalogs={
            f"worker-tools::{worker.id}": [
                {
                    "id": "web_search",
                    "summary": "Search current public sources.",
                    "details": "Retrieve current evidence when the local corpus is insufficient.",
                },
                {
                    "id": "cite_sources",
                    "summary": "Return grounded source citations.",
                    "details": "Format the final answer with inspectable source refs.",
                },
            ]
        }
    )
    runner = StandaloneWorkerRunner(
        completion_provider=completion_provider,
        acquisition_provider=acquisition_provider,
        memory_provider=InMemoryLifecycleProvider(),
    )
    request = ExecutionRequest.from_harness(
        task=(
            "Research the worker cell rollout, compare the strategy and risks notes, "
            "and cite the relevant sources."
        ),
        acquisition={
            "sources": [
                AcquisitionSource(
                    source_id="corpus",
                    family=AcquisitionFamily.FILE_INVENTORY,
                    metadata={"root": str(root)},
                )
            ]
        },
        output_contract={
            "definition_of_done": "Return grounded JSON with citations for the relevant research notes only.",
            "expected_return_shape": '{"task_class": "research", "selected_refs": [...], "capabilities": [...], "citations": [...]}',
        },
        metadata={"task_class": "research"},
    )

    result = await runner.run(worker, request)
    return result, completion_provider.requests[0], acquisition_provider


async def _run_structured_review_task(tmp_path: Path):
    root = tmp_path / "review"
    _write_file(root / "reports/review_contract.md", "REVIEW_TARGET_DETAIL\ncheck the standalone cell contract\n")
    _write_file(root / "notes/random.txt", "IRRELEVANT_REVIEW_NOISE\nignore this file during review\n")

    worker = WorkerDefinition(
        id="review-cell",
        role="reviewer",
        instruction="Validate the contract against the saved checklist and surfaced memory only.",
        model="stub-model",
        tool_ids=["contract_reporter"],
        acquisition_policy=AcquisitionPolicy(
            max_selected_items_per_source=1,
            max_expanded_items_per_source=1,
        ),
    )
    completion_provider = _AcceptanceCompletionProvider()
    acquisition_provider = _RecordingAcquisitionProvider(
        tool_catalogs={
            f"worker-tools::{worker.id}": [
                {
                    "id": "contract_reporter",
                    "summary": "Emit a structured contract verdict.",
                    "details": "Return a concise validation summary for the selected checklist.",
                }
            ]
        }
    )
    runner = StandaloneWorkerRunner(
        completion_provider=completion_provider,
        acquisition_provider=acquisition_provider,
        memory_provider=InMemoryLifecycleProvider(),
    )
    session = runner.create_session(worker, metadata={"surface": "acceptance-gate"})

    first_request = ExecutionRequest.from_harness(
        task="Inspect reports/review_contract.md and build the review checklist before validation.",
        acquisition={
            "sources": [
                AcquisitionSource(
                    source_id="workspace",
                    family=AcquisitionFamily.FILE_INVENTORY,
                    metadata={"root": str(root)},
                )
            ]
        },
        output_contract={
            "definition_of_done": "Return a compact JSON checklist for the contract review.",
            "expected_return_shape": '{"task_class": "structured_review", "selected_refs": [...], "verdict": "..."}',
        },
        metadata={"task_class": "structured_review"},
    )
    first_result = await runner.run(worker, first_request, session=session)

    second_request = ExecutionRequest.from_harness(
        task="Validate the saved review checklist without rescanning the workspace.",
        output_contract={
            "definition_of_done": "Return a JSON contract verdict that reuses the saved checklist memory.",
            "expected_return_shape": '{"task_class": "structured_review", "selected_refs": [...], "memory_refs": [...], "verdict": "..."}',
        },
        metadata={"task_class": "structured_review"},
    )
    second_result = await runner.run(worker, second_request, session=session)
    return first_result, second_result, completion_provider.requests, acquisition_provider, session


def _pi_like_baseline_for_coding_task(root: Path) -> _BaselineOutcome:
    files = sorted(path for path in root.rglob("*") if path.is_file())
    prompt_parts = [
        "Task:\nInspect src/apply_patch.py and propose a minimal patch plan.",
        "Definition of done:\nReturn a compact JSON patch plan that cites the target file and chosen tool.",
        'Expected return shape:\n{"task_class": "coding", "selected_refs": [...], "capabilities": [...], "plan": "..."}',
        "Evidence:\n"
        + "\n\n".join(
            f"{path.relative_to(root)}\n{path.read_text(encoding='utf-8')}" for path in files
        ),
        "Tools:\n"
        + "\n".join(
            [
                "- pytest_runner: Focus the worker test basket on the touched path.",
                "- file_write: Apply a compact code change when the contract allows it.",
            ]
        ),
    ]
    return _BaselineOutcome(
        prompt="\n\n".join(prompt_parts),
        selected_refs=[str(path.relative_to(root)) for path in files],
        capability_ids=["pytest_runner", "file_write"],
        has_continuation=False,
    )


def _score_dan_coding_outcome(completion_request: CompletionRequest, result) -> _GateScore:
    payload = _parse_payload(result)
    file_refs = [
        ref for ref in result.observability.selected_refs if ref.startswith(f"{AcquisitionFamily.FILE_INVENTORY.value}:")
    ]
    tool_refs = [
        ref for ref in result.observability.selected_refs if ref.startswith(f"{AcquisitionFamily.TOOL_CATALOG.value}:")
    ]
    return _GateScore(
        quality=int(
            result.status == "completed"
            and "PATCH_TARGET_DETAIL" in completion_request.user_prompt
            and "IRRELEVANT_CODING_NOISE" not in completion_request.user_prompt
        ),
        contract_adherence=int(
            bool(payload.get("contract_seen"))
            and payload.get("task_class") == "coding"
            and payload.get("plan") == "PATCH_PLAN_READY"
        ),
        budget_discipline=int(len(file_refs) == 1 and len(tool_refs) == 1 and len(result.attempts) == 1),
        recoverability=int(result.continuation is not None and result.session.continuation is not None),
    )


def _score_pi_like_baseline(outcome: _BaselineOutcome) -> _GateScore:
    return _GateScore(
        quality=int("PATCH_TARGET_DETAIL" in outcome.prompt),
        contract_adherence=int(
            "Definition of done:" in outcome.prompt and "Expected return shape:" in outcome.prompt
        ),
        budget_discipline=int(
            "IRRELEVANT_CODING_NOISE" not in outcome.prompt and len(outcome.selected_refs) <= 1
        ),
        recoverability=int(outcome.has_continuation),
    )


@pytest.mark.asyncio
async def test_single_cell_acceptance_gate_covers_canonical_tasks_and_promotion_bar(tmp_path: Path) -> None:
    coding_result, coding_request, coding_acquisition, _ = await _run_coding_task(tmp_path)
    research_result, research_request, research_acquisition = await _run_research_task(tmp_path)
    review_seed_result, review_result, review_requests, review_acquisition, review_session = (
        await _run_structured_review_task(tmp_path)
    )

    coding_payload = _parse_payload(coding_result)
    research_payload = _parse_payload(research_result)
    review_payload = _parse_payload(review_result)

    gate_report = {
        "canonical_tasks": set(CANONICAL_TASK_CLASSES),
        "dimensions": set(PROMOTION_DIMENSIONS),
        "coding_quality": (
            coding_result.status == "completed"
            and "PATCH_TARGET_DETAIL" in coding_request.user_prompt
            and "IRRELEVANT_CODING_NOISE" not in coding_request.user_prompt
        ),
        "coding_contract_adherence": (
            coding_payload["task_class"] == "coding"
            and coding_payload["contract_seen"] is True
            and coding_payload["plan"] == "PATCH_PLAN_READY"
        ),
        "coding_budget_discipline": len(
            [
                ref
                for ref in coding_result.observability.selected_refs
                if ref.startswith(f"{AcquisitionFamily.FILE_INVENTORY.value}:")
            ]
        )
        == 1,
        "research_quality": (
            research_result.status == "completed"
            and "RESEARCH_PRIMARY_SOURCE" in research_request.user_prompt
            and "RESEARCH_RISK_SOURCE" in research_request.user_prompt
            and "IRRELEVANT_RESEARCH_NOISE" not in research_request.user_prompt
        ),
        "research_contract_adherence": (
            research_payload["task_class"] == "research"
            and research_payload["contract_seen"] is True
            and len(research_payload["citations"]) == 2
        ),
        "research_budget_discipline": len(research_payload["citations"]) == 2,
        "review_quality": (
            review_seed_result.status == "completed"
            and review_result.status == "completed"
            and "REVIEW_TARGET_DETAIL" in review_requests[0].user_prompt
            and "IRRELEVANT_REVIEW_NOISE" not in review_requests[0].user_prompt
        ),
        "review_contract_adherence": (
            review_payload["task_class"] == "structured_review"
            and review_payload["contract_seen"] is True
            and review_payload["verdict"] == "contract-pass"
        ),
        "review_budget_discipline": [
            call["source_id"] for call in review_acquisition.discover_calls
        ].count("workspace")
        == 1,
        "recoverability": (
            "contract-pass" in review_requests[1].user_prompt
            and bool(review_result.observability.selected_memory_refs)
            and review_session.continuation is not None
            and any(
                call["family"] == "AcquisitionFamily.MEMORY_CATALOG"
                for call in review_acquisition.discover_calls
            )
        ),
    }

    assert gate_report["canonical_tasks"] == set(CANONICAL_TASK_CLASSES)
    assert gate_report["dimensions"] == set(PROMOTION_DIMENSIONS)
    assert gate_report["coding_quality"]
    assert gate_report["coding_contract_adherence"]
    assert gate_report["coding_budget_discipline"]
    assert gate_report["research_quality"]
    assert gate_report["research_contract_adherence"]
    assert gate_report["research_budget_discipline"]
    assert gate_report["review_quality"]
    assert gate_report["review_contract_adherence"]
    assert gate_report["review_budget_discipline"]
    assert gate_report["recoverability"]

    assert [call["source_id"] for call in coding_acquisition.discover_calls] == [
        "workspace",
        "worker-tools::coding-cell",
    ]
    assert [call["source_id"] for call in research_acquisition.discover_calls] == [
        "corpus",
        "worker-tools::research-cell",
    ]
    assert [call["source_id"] for call in review_acquisition.discover_calls].count(
        "worker-tools::review-cell"
    ) == 1
    assert review_payload["memory_refs"] == review_result.observability.selected_memory_refs


@pytest.mark.asyncio
async def test_single_cell_acceptance_gate_beats_pi_like_baseline_on_coding_task(
    tmp_path: Path,
) -> None:
    result, completion_request, _, _ = await _run_coding_task(tmp_path)

    baseline = _pi_like_baseline_for_coding_task(tmp_path / "coding")
    dan_score = _score_dan_coding_outcome(completion_request, result)
    baseline_score = _score_pi_like_baseline(baseline)

    assert dan_score.total == 4
    assert baseline_score.contract_adherence == 1
    assert baseline_score.budget_discipline == 0
    assert baseline_score.recoverability == 0
    assert dan_score.total > baseline_score.total
    assert "IRRELEVANT_CODING_NOISE" in baseline.prompt
