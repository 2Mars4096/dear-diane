from __future__ import annotations

import json
from pathlib import Path

import pytest

from dan.worker import (
    CellAddress,
    CellAuthorityLimits,
    CellBudgetLimits,
    CellHandoffPacket,
    CodingTask,
    ContinuationHooks,
    ContinuationPayload,
    CrossCellTraceLog,
    EvidenceRef,
    HandoffTask,
    SignalTrace,
    WorkerCoreExecutor,
    WorkerAuthority,
    coding_execution_organism,
    execute_coding_organism,
)
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse
from dan.worker.organisms.coding_execution import _apply_frontend_contract_hygiene
from dan.worker.organisms.coding_execution import _apply_short_timeout_frontend_contract_hygiene
from dan.worker.organisms.coding_execution import _child_packet
from dan.worker.organisms.coding_execution import _normalize_orchestrator_plan
from dan.worker.organisms.coding_execution import _worker_pool_pattern
from dan.worker.organisms.coding_execution import _worker_tool_ids_for_plan


class _CodingOrganismProvider:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def _count(self, worker_id: str) -> int:
        current = self._counts.get(worker_id, 0) + 1
        self._counts[worker_id] = current
        return current

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")
        call_index = self._count(worker_id)

        if worker_id == "coding-build.orchestrator":
            if call_index == 1:
                payload = {
                    "public_response": "I understand this as a bounded runtime repair request, so I will inspect the validator and delivery path before patching.",
                    "worker_count": 2,
                    "worker_briefs": [
                        "Inspect the narrow failure path and propose the smallest viable patch.",
                        "Focus on keeping the validation command and delivery summary explicit.",
                    ],
                    "aggregation_focus": "Merge the worker outputs into one narrow candidate.",
                    "validator_focus": "Reject any candidate that hides validation or delivery gaps.",
                    "pass_threshold": 0.9,
                }
            else:
                payload = {
                    "public_response": "The validator still sees a delivery gap, so I will run one repair-focused attempt with explicit validation and delivery coverage.",
                    "worker_count": 3,
                    "worker_briefs": [
                        "Keep the patch narrow in the validator path.",
                        "Preserve an explicit focused validation command.",
                        "Make the delivery summary and residual risks explicit.",
                    ],
                    "aggregation_focus": "Prefer the candidate that closes the validator gap without widening the patch.",
                    "validator_focus": "Score the repaired candidate against the full acceptance bar.",
                    "pass_threshold": 0.9,
                }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.worker-1":
            payload = {
                "candidate_fragment": "Keep the change in the validator path only.",
                "change_summary": "Narrow validator-path patch.",
                "target_files": ["src/dan/worker/organisms/coding_execution.py"],
                "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py"],
                "risks": ["May still under-specify delivery wording."],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.worker-2":
            payload = {
                "candidate_fragment": "Keep the focused validation command explicit in the delivery path.",
                "change_summary": "Add explicit validation-command coverage.",
                "target_files": ["src/dan/cli/code.py"],
                "test_plan": ["PYTHONPATH=src pytest -q tests/test_cli/test_code.py"],
                "risks": ["The delivery summary may still be implicit."],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.worker-3":
            payload = {
                "candidate_fragment": "Make the final delivery summary and residual risks explicit.",
                "change_summary": "Add explicit delivery-language coverage.",
                "target_files": ["README.md"],
                "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py"],
                "risks": ["Docs could drift if not aligned with the runtime change."],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.aggregation.lead":
            if call_index == 1:
                payload = {
                    "candidate_id": "candidate-1",
                    "change_summary": "Patch the validator path but leave the focused validation command implicit.",
                    "target_files": ["src/dan/worker/organisms/coding_execution.py"],
                    "test_plan": [],
                    "risks": ["The candidate still hides the focused validation step."],
                }
            else:
                payload = {
                    "candidate_id": "candidate-2",
                    "change_summary": "Patch the validator path, preserve the focused validation command, and keep delivery notes explicit.",
                    "target_files": [
                        "src/dan/worker/organisms/coding_execution.py",
                        "src/dan/cli/code.py",
                        "README.md",
                    ],
                    "test_plan": [
                        "PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py tests/test_cli/test_code.py"
                    ],
                    "risks": ["Residual risk is low after the repair-focused validation pass."],
                }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.validator.reviewer-a":
            return CompletionResponse(text="repair" if call_index == 1 else "pass", raw={"worker_id": worker_id})
        if worker_id == "coding-build.validator.reviewer-b":
            return CompletionResponse(text="repair" if call_index == 1 else "pass", raw={"worker_id": worker_id})

        if worker_id == "coding-build.validator.lead":
            if call_index == 1:
                payload = {
                    "passed": False,
                    "overall_score": 0.76,
                    "dimension_scores": {
                        "boundedness": 0.91,
                        "validation_specificity": 0.58,
                        "delivery_explicitness": 0.63,
                    },
                    "repair_brief": "Make the focused validation command explicit and keep the delivery summary inspectable.",
                    "missing_requirements": [
                        "focused validation command",
                        "explicit delivery summary",
                    ],
                    "comparison_note": "Candidate 1 is narrow but still under-specifies validation and delivery.",
                }
            else:
                payload = {
                    "passed": True,
                    "overall_score": 0.97,
                    "dimension_scores": {
                        "boundedness": 0.96,
                        "validation_specificity": 0.98,
                        "delivery_explicitness": 0.97,
                    },
                    "repair_brief": "",
                    "missing_requirements": [],
                    "comparison_note": "Candidate 2 closes the validator gap while staying bounded.",
                }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        raise AssertionError(f"Unexpected worker_id: {worker_id}")


class _MetadataCaptureCodingOrganismProvider(_CodingOrganismProvider):
    def __init__(self) -> None:
        super().__init__()
        self.metadata_by_worker: dict[str, list[dict[str, object]]] = {}

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")
        self.metadata_by_worker.setdefault(worker_id, []).append(dict(request.metadata))
        return await super().complete(request)


class _FencedVerboseCodingOrganismProvider(_CodingOrganismProvider):
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        response = await super().complete(request)
        worker_id = str(request.metadata.get("worker_id") or "")
        if worker_id == "coding-build.aggregation.lead":
            payload = json.loads(response.text)
            payload["extra_note"] = "aggregation-extra"
            return CompletionResponse(
                text=f"```json\n{json.dumps(payload, sort_keys=True)}\n```",
                raw={"worker_id": worker_id},
            )
        if worker_id == "coding-build.validator.lead":
            payload = json.loads(response.text)
            payload["extra_note"] = "validation-extra"
            return CompletionResponse(
                text=f"```json\n{json.dumps(payload, sort_keys=True)}\n```",
                raw={"worker_id": worker_id},
            )
        return response


class _AggregationEscalationProvider:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")
        if worker_id == "coding-build.orchestrator":
            payload = {
                "public_response": "Create one bounded candidate and keep its material outputs visible even if validation cannot run yet.",
                "worker_count": 1,
                "worker_briefs": ["Draft one small website slice."],
                "aggregation_focus": "Merge the worker output into one candidate.",
                "validator_focus": "Validate the merged candidate.",
                "pass_threshold": 0.9,
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})
        if worker_id == "coding-build.worker-1":
            payload = {
                "candidate_fragment": "Create the static site files.",
                "change_summary": "Create the site scaffold.",
                "target_files": ["/workspace/absharks/index.html"],
                "test_plan": ["open /workspace/absharks/index.html"],
                "risks": ["Styling quality still needs refinement."],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})
        if worker_id == "coding-build.aggregation.lead":
            payload = {
                "candidate_id": "candidate-partial",
                "change_summary": "Created the shark site files but omitted one required contract field.",
                "target_files": [
                    "/workspace/absharks/index.html",
                    "/workspace/absharks/styles.css",
                ],
                "test_plan": ["open /workspace/absharks/index.html"],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})
        raise AssertionError(f"Unexpected worker_id: {worker_id}")


class _NoMaterialWorkerProvider:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")
        if worker_id == "coding-build.orchestrator":
            payload = {
                "public_response": "Inspect the bounded website refresh in one worker lane.",
                "worker_count": 1,
                "worker_briefs": ["Inspect the existing website files and return one bounded candidate."],
                "aggregation_focus": "Merge the worker output into one candidate.",
                "validator_focus": "Validate the merged candidate.",
                "pass_threshold": 0.9,
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})
        if worker_id == "coding-build.worker-1":
            payload = {
                "candidate_fragment": {},
                "change_summary": "The provider timed out before the coding worker produced a bounded candidate.",
                "target_files": [],
                "test_plan": [],
                "risks": [
                    "No code candidate was produced because the provider timed out before completion."
                ],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})
        raise AssertionError(f"Unexpected worker_id: {worker_id}")


class _NoMaterialWebsiteRefreshProvider:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")
        if worker_id == "coding-build.orchestrator":
            payload = {
                "public_response": "Refresh the existing website files.",
                "worker_count": 3,
                "worker_briefs": [
                    "EXCLUSIVE WRITE OWNER: index.html. Patch the page markup only.",
                    "EXCLUSIVE WRITE OWNER: styles.css. Patch the stylesheet only.",
                    "EXCLUSIVE WRITE OWNER: app.js. Patch the interaction layer only.",
                ],
                "aggregation_focus": "Merge the website refresh.",
                "validator_focus": "Validate the website refresh.",
                "pass_threshold": 0.9,
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id.startswith("coding-build.worker-"):
            payload = {
                "candidate_fragment": {},
                "change_summary": "The provider timed out before the coding worker produced a bounded candidate.",
                "target_files": [],
                "test_plan": [],
                "risks": [
                    "No code candidate was produced because the provider timed out before completion."
                ],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id in {"coding-build.validator.reviewer-a", "coding-build.validator.reviewer-b"}:
            return CompletionResponse(text="pass", raw={"worker_id": worker_id})

        if worker_id == "coding-build.validator.lead":
            payload = {
                "passed": True,
                "overall_score": 0.95,
                "dimension_scores": {"boundedness": 0.95, "materiality": 0.95},
                "repair_brief": "",
                "missing_requirements": [],
                "comparison_note": "The deterministic existing-website refresh produced material file changes.",
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        raise AssertionError(f"Unexpected worker_id: {worker_id}")


class _NoMaterialThenRepairSuccessProvider:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def _count(self, worker_id: str) -> int:
        current = self._counts.get(worker_id, 0) + 1
        self._counts[worker_id] = current
        return current

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")
        call_index = self._count(worker_id)

        if worker_id == "coding-build.orchestrator":
            payload = {
                "public_response": (
                    "The previous attempt failed to materialize a bounded candidate, so I will retry with one smaller "
                    "write-first patch."
                    if call_index > 1
                    else "Inspect the bounded runtime seam in one worker lane."
                ),
                "worker_count": 1,
                "worker_briefs": [
                    (
                        "Make one small write-first patch in the owned file and return the bounded candidate."
                        if call_index > 1
                        else "Inspect the existing runtime seam and return one bounded candidate."
                    )
                ],
                "aggregation_focus": "Merge the worker output into one candidate.",
                "validator_focus": "Validate the merged candidate.",
                "pass_threshold": 0.9,
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.worker-1":
            if call_index == 1:
                payload = {
                    "candidate_fragment": {},
                    "change_summary": "The provider timed out before the coding worker produced a bounded candidate.",
                    "target_files": [],
                    "test_plan": [],
                    "risks": [
                        "No code candidate was produced because the provider timed out before completion."
                    ],
                }
            else:
                payload = {
                    "candidate_fragment": "Make the validator and delivery wording explicit in one file.",
                    "change_summary": "Materialize the focused runtime patch.",
                    "target_files": ["src/dan/worker/organisms/coding_execution.py"],
                    "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py"],
                    "risks": [],
                }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.aggregation.lead":
            payload = {
                "candidate_id": "candidate-repaired",
                "change_summary": "Materialize the focused runtime patch.",
                "target_files": ["src/dan/worker/organisms/coding_execution.py"],
                "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py"],
                "risks": [],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id in {"coding-build.validator.reviewer-a", "coding-build.validator.reviewer-b"}:
            return CompletionResponse(text="pass", raw={"worker_id": worker_id})

        if worker_id == "coding-build.validator.lead":
            payload = {
                "passed": True,
                "overall_score": 0.95,
                "dimension_scores": {
                    "boundedness": 0.95,
                    "validation_specificity": 0.95,
                    "delivery_explicitness": 0.95,
                },
                "repair_brief": "",
                "missing_requirements": [],
                "comparison_note": "The repaired candidate is bounded and explicit.",
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        raise AssertionError(f"Unexpected worker_id: {worker_id}")


class _BelowThresholdPassProvider:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")

        if worker_id == "coding-build.orchestrator":
            payload = {
                "public_response": "I will try one bounded candidate.",
                "worker_count": 1,
                "worker_briefs": ["Patch the one failing seam."],
                "aggregation_focus": "Merge the one worker result into one candidate.",
                "validator_focus": "Validate the candidate against the configured quality bar.",
                "pass_threshold": 0.9,
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.worker-1":
            payload = {
                "candidate_fragment": "Patch the failing seam in one file.",
                "change_summary": "One focused patch path.",
                "target_files": ["src/dan/worker/organisms/coding_execution.py"],
                "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py"],
                "risks": ["The score may still stay below the requested bar."],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.aggregation.lead":
            payload = {
                "candidate_id": "candidate-below-threshold",
                "change_summary": "Merged the one focused worker result into one bounded candidate.",
                "target_files": ["src/dan/worker/organisms/coding_execution.py"],
                "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py"],
                "risks": ["Validator score still sits below the requested bar."],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id in {"coding-build.validator.reviewer-a", "coding-build.validator.reviewer-b"}:
            return CompletionResponse(text="pass", raw={"worker_id": worker_id})

        if worker_id == "coding-build.validator.lead":
            payload = {
                "passed": True,
                "overall_score": 0.6,
                "dimension_scores": {
                    "boundedness": 0.8,
                    "validation_specificity": 0.55,
                    "delivery_explicitness": 0.45,
                },
                "repair_brief": "",
                "missing_requirements": [],
                "comparison_note": "The candidate is acceptable in shape but still below the requested quality bar.",
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        raise AssertionError(f"Unexpected worker_id: {worker_id}")


class _NearThresholdPassProvider(_BelowThresholdPassProvider):
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")
        if worker_id == "coding-build.aggregation.lead":
            payload = {
                "candidate_id": "candidate-near-threshold",
                "change_summary": "Merged the one focused worker result into one bounded candidate.",
                "target_files": ["src/dan/worker/organisms/coding_execution.py"],
                "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py"],
                "risks": ["Validator confidence is slightly below the requested bar."],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})
        if worker_id == "coding-build.validator.lead":
            payload = {
                "passed": True,
                "overall_score": 0.88,
                "dimension_scores": {
                    "boundedness": 0.93,
                    "validation_specificity": 0.87,
                    "delivery_explicitness": 0.84,
                },
                "repair_brief": "",
                "missing_requirements": [],
                "comparison_note": "The candidate is validator-passable and narrowly under the requested quality bar.",
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})
        return await super().complete(request)


def _task(tmp_path: Path) -> CodingTask:
    return CodingTask(
        task_id="coding-organism-acceptance",
        objective="Repair the coding organism so the orchestrated candidate keeps validation and delivery explicit.",
        acceptance_criteria=[
            "Use an explicit orchestrator -> worker pool -> direct single-worker candidate or aggregation fallback -> validator shape.",
            "Allow the orchestrator to increase worker fan-out after validator feedback.",
            "Return one bounded candidate with explicit files and validation.",
            "Preserve an inspectable repair brief and validator score.",
        ],
        research_findings=[
            "The first candidate often omits the focused validation command.",
            "The delivery summary must stay explicit after repair.",
        ],
        repair_brief="",
        evidence_refs=[
            EvidenceRef(
                ref_id="brief:acceptance",
                label="Acceptance brief",
                summary="The coding organism must stay bounded and inspectable.",
                source="test-brief",
                locator=str(tmp_path / "acceptance.md"),
            )
        ],
        session_context={
            "product_name": "DAN Code",
            "workspace_root": str(tmp_path),
        },
        hard_constraints=[
            "Reuse universal-agent roles instead of inventing a separate coding runtime.",
        ],
        soft_constraints=[
            "Keep the worker pool homogeneous and dynamically sized.",
        ],
    )


def test_coding_execution_preset_keeps_roles_explicit() -> None:
    organism = coding_execution_organism(model="stub-model")

    assert organism.orchestrator_worker.role == "coding_orchestrator"
    assert "non-overlapping" in organism.orchestrator_worker.instruction
    assert organism.worker_role == "coding_worker"
    assert "Prefer the most specific structured tool" in organism.worker_instruction
    assert organism.aggregator_organ.kind.value == "coding_aggregation"
    assert "avoid shell-based file creation" in organism.aggregator_organ.lead_worker.instruction
    assert organism.validator_organ.kind.value == "universal_validator"
    assert organism.metadata["coding_flow"] == (
        "orchestrator -> worker pool -> direct single-worker candidate or aggregation fallback -> validator"
    )


def test_repair_orchestrator_plan_fallback_uses_repair_brief(tmp_path) -> None:
    organism = coding_execution_organism(model="stub-model")
    plan = _normalize_orchestrator_plan(
        outputs={
            "public_response": "Repair the missing runtime layers.",
            "worker_count": 1,
            "aggregation_focus": "Merge the repair candidate.",
            "validator_focus": "Verify the repair scope.",
            "pass_threshold": 0.9,
        },
        organism=organism,
        task=_task(tmp_path),
        repair_brief="Create main.py, processor.py, tests, and README from validator feedback.",
    )

    assert plan.worker_briefs == [
        "Investigate one bounded candidate path for objective: Create main.py, processor.py, tests, and README from validator feedback."
    ]


def test_benchmark_orchestrator_plan_strengthens_validator_focus(tmp_path) -> None:
    organism = coding_execution_organism(model="stub-model")
    task = _task(tmp_path).model_copy(
        update={
            "session_context": {
                "product_name": "DAN Code",
                "workspace_root": str(tmp_path),
                "benchmark_context": {
                    "benchmark_name": "SWE-bench",
                    "instance_id": "astropy__astropy-14365",
                },
            }
        }
    )
    plan = _normalize_orchestrator_plan(
        outputs={
            "public_response": "Plan one narrow benchmark repair attempt.",
            "worker_count": 1,
            "worker_briefs": ["Inspect the parser contract."],
            "aggregation_focus": "Merge one bounded parser fix.",
            "validator_focus": "Reject incomplete parser fixes.",
            "pass_threshold": 0.9,
        },
        organism=organism,
        task=task,
    )

    assert "illustrative rather than exhaustive" in plan.validator_focus
    assert "helpers, parametrizations" in plan.validator_focus
    assert "parser/read/write/round-trip paths" in plan.validator_focus


def test_orchestrator_plan_uses_generic_artifact_partitions_for_parallel_lanes(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<main></main>\n", encoding="utf-8")
    (tmp_path / "styles.css").write_text("body { margin: 0; }\n", encoding="utf-8")
    (tmp_path / "app.js").write_text("console.log('ready');\n", encoding="utf-8")
    organism = coding_execution_organism(model="stub-model")
    task = _task(tmp_path).model_copy(
        update={
            "objective": (
                "Inspect all entry-point files in this workspace, then apply a bounded visual refresh across the "
                "existing artifacts without adding dependencies."
            ),
            "acceptance_criteria": [
                "Keep the patch bounded to existing files.",
                "Name every modified file.",
                "Read back each modified artifact.",
            ],
        }
    )

    plan = _normalize_orchestrator_plan(
        outputs={
            "public_response": "I will inspect the website and make it look cooler.",
            "worker_count": 1,
            "worker_briefs": ["Inspect the website and prepare one bounded refresh."],
            "aggregation_focus": "Merge the worker outputs into one candidate.",
            "validator_focus": "Validate the bounded refresh candidate.",
            "pass_threshold": 0.9,
        },
        organism=organism,
        task=task,
    )

    assert plan.worker_count == 3
    assert len(plan.worker_briefs) == 3
    assert plan.scheduler_policy_source == "generic_artifact_partition"
    assert plan.artifact_owner_paths == ["index.html", "styles.css", "app.js"]
    assert any("scheduler artifact partition" in brief for brief in plan.worker_briefs)
    assert all("EXCLUSIVE WRITE OWNER" in brief for brief in plan.worker_briefs)
    assert all("Do not edit any other artifact path" in brief for brief in plan.worker_briefs)
    assert all("global modified-file" in brief for brief in plan.worker_briefs)
    assert not any("Shared selector contract" in brief for brief in plan.worker_briefs)
    assert not any("HTML/layout/content hierarchy" in brief for brief in plan.worker_briefs)
    assert not any("Explicitly name every modified file" in brief for brief in plan.worker_briefs)
    assert "parallel artifact-owner fragments" in plan.aggregation_focus

    organism_with_tools = organism.model_copy(
        update={
            "worker_tool_ids": ["file_read", "file_edit", "file_write"],
            "parallel_worker_tool_ids": ["file_read"],
        }
    )
    assert _worker_tool_ids_for_plan(organism=organism_with_tools, plan=plan) == [
        "file_read",
        "file_edit",
        "file_write",
    ]
    organism_with_extra_tools = organism.model_copy(
        update={
            "worker_tool_ids": [
                "list_directory",
                "file_read",
                "file_edit",
                "file_write",
                "shell_command",
                "web_search",
            ],
            "parallel_worker_tool_ids": ["file_read"],
        }
    )
    assert _worker_tool_ids_for_plan(organism=organism_with_extra_tools, plan=plan) == [
        "file_read",
        "file_edit",
        "file_write",
    ]
    worker_pool, _ = _worker_pool_pattern(
        organism=organism_with_tools,
        attempt=1,
        plan=plan,
    )
    assert [
        member.metadata.get("exclusive_write_owner_path")
        for member in worker_pool.members
    ] == ["index.html", "styles.css", "app.js"]
    assert all(
        "The global entry-point inspection, modified-file listing, full updated-content return, and read-back "
        "confirmation requirements are satisfied across the whole organism"
        in (member.instruction_suffix or "")
        for member in worker_pool.members
    )
    assert all(
        "the very next model response must be `file_edit` or `file_write`"
        in (member.instruction_suffix or "")
        for member in worker_pool.members
    )


def test_broad_website_repair_brief_suppresses_forced_parallel_design_fanout(
    tmp_path,
) -> None:
    (tmp_path / "index.html").write_text("<main></main>\n", encoding="utf-8")
    (tmp_path / "styles.css").write_text("body { margin: 0; }\n", encoding="utf-8")
    (tmp_path / "app.js").write_text("console.log('ready');\n", encoding="utf-8")
    organism = coding_execution_organism(model="stub-model")
    task = _task(tmp_path).model_copy(
        update={
            "objective": (
                "Inspect the website workspace, apply a dark visual refresh, improve typography and spacing, "
                "refine layout structure, and add one or two motion/transition details."
            ),
            "acceptance_criteria": [
                "Keep the patch bounded to existing website files.",
                "Switch to a dark theme.",
                "Improve layout, typography, spacing, and motion.",
            ],
        }
    )

    plan = _normalize_orchestrator_plan(
        outputs={
            "public_response": "Retry with one smaller write-first website patch.",
            "worker_count": 1,
            "worker_briefs": ["Inspect the website and prepare one bounded refresh."],
            "aggregation_focus": "Merge the worker outputs into one candidate.",
            "validator_focus": "Validate the bounded refresh candidate.",
            "pass_threshold": 0.9,
        },
        organism=organism,
        task=task,
        repair_brief=(
            "Workers produced no material bounded candidate to merge; stop and re-plan instead of rediscovering the "
            "workspace in aggregation. Re-plan with a smaller write-first attempt. Prefer 1 worker unless another "
            "split has a concrete immediate first write."
        ),
    )

    assert plan.worker_count == 1
    assert plan.worker_briefs == ["Inspect the website and prepare one bounded refresh."]


def test_short_timeout_frontend_contract_hygiene_repairs_cross_lane_drift(tmp_path) -> None:
    (tmp_path / "index.html").write_text(
        """
<main>
  <section id="features" class="features" data-animate="features">
    <article data-delay="1">
      <h2>Decision trails</h2>
    </article>
  </section>
  <section id="features" class="features">
    <article>
      <h2>Duplicate</h2>
    </article>
  </section>
</main>
</main>
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "styles.css").write_text(
        """
[data-animate] {
  opacity: 0;
}

[data-animate="in"] {
  animation: fadeInUp 0.7s ease forwards;
}

.scroll-cue {
  opacity: 0.7;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        'document.querySelectorAll("[data-animate]").forEach((el) => el.classList.add("is-visible"));\n',
        encoding="utf-8",
    )
    task = _task(tmp_path).model_copy(
        update={
            "session_context": {
                "workspace_root": str(tmp_path),
                "completion_timeout_seconds": 45.0,
                "short_completion_timeout": True,
            }
        }
    )
    candidate_payload = {
        "candidate_id": "candidate-1-worker-merge",
        "change_summary": "Merged parallel website lanes.",
        "target_files": ["index.html", "styles.css", "app.js"],
        "test_plan": [],
        "risks": [],
        "workspace_effect": "modified",
    }

    report = _apply_short_timeout_frontend_contract_hygiene(
        task=task,
        candidate_payload=candidate_payload,
        owner_paths=["index.html", "styles.css", "app.js"],
    )

    assert report is not None
    assert report["changed_files"] == ["index.html", "styles.css"]
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert html.count('id="features"') == 1
    assert html.count("</main>") == 1
    assert 'data-animate="feature"' in html
    assert 'data-animate-delay="1"' in html
    assert "data-delay" not in html
    css = (tmp_path / "styles.css").read_text(encoding="utf-8")
    assert "[data-animate].is-visible" in css
    assert ".js-reveal.is-visible" in css
    assert ".scroll-cue.is-hidden" in css
    assert "prefers-reduced-motion" in css
    assert "Applied deterministic frontend contract hygiene" in candidate_payload["change_summary"]
    assert any("CSS/JS reveal hooks agree" in step for step in candidate_payload["test_plan"])


def test_frontend_contract_hygiene_runs_without_short_timeout_and_repairs_document_tail_drift(tmp_path) -> None:
    (tmp_path / "index.html").write_text(
        """
<!doctype html>
<html lang="en">
<head>
  <link rel="stylesheet" href="./styles.css" />
  <style>
    body { color: white; }
    <section class="organs reveal-on-scroll">
</head>
<body>
  <main>
    <section id="features" class="features">
      <article data-delay="1"><h2>Decision trails</h2></article>
    </section>
    <script src="./app.js"></script>
  </main>
</body>
</html>

    <script src="./app.js"></script>
  </main>
</body>
</html>
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "styles.css").write_text("body { color: white; }\n", encoding="utf-8")
    (tmp_path / "app.js").write_text("console.log('ready');\n", encoding="utf-8")
    task = _task(tmp_path).model_copy(
        update={
            "session_context": {
                "workspace_root": str(tmp_path),
                "completion_timeout_seconds": 90.0,
                "short_completion_timeout": False,
            }
        }
    )
    candidate_payload = {
        "candidate_id": "candidate-1-worker-merge",
        "change_summary": "Merged parallel website lanes.",
        "target_files": ["index.html", "styles.css", "app.js"],
        "test_plan": [],
        "risks": [],
        "workspace_effect": "modified",
    }

    report = _apply_frontend_contract_hygiene(
        task=task,
        candidate_payload=candidate_payload,
        owner_paths=["index.html", "styles.css", "app.js"],
    )

    assert report is not None
    assert report["errors"] == []
    assert "closed unclosed style blocks before </head>" in report["repairs"]
    assert "removed duplicate document tail tags" in report["repairs"]
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    head = html.split("</head>", 1)[0]
    assert "<section" not in head
    assert html.count("</style>") == 1
    assert html.count('src="./app.js"') == 1
    assert html.count("</main>") == 1
    assert html.count("</body>") == 1
    assert html.count("</html>") == 1
    assert "data-animate-delay" in html
    assert "Applied deterministic frontend contract hygiene" in candidate_payload["change_summary"]


def test_child_packet_preserves_parent_handoff_membrane() -> None:
    parent = CellHandoffPacket(
        trace=SignalTrace(trace_id="trace:coding", root_task_id="coding-root"),
        sender=CellAddress(cell_id="user.request", organism_id="coding-organism"),
        recipient=CellAddress(cell_id="coding-build.orchestrator", organism_id="coding-organism"),
        task=HandoffTask(task_id="coding-root", instruction="Plan the next attempt."),
        budget_limits=CellBudgetLimits(max_completion_rounds=2, max_tool_calls=7),
        authority_limits=CellAuthorityLimits(
            acting_authority=WorkerAuthority.LEAD,
            max_spawned_cells=1,
            allow_memory_write_scopes=["notes"],
            allowed_toolset_refs=["local-read"],
        ),
        continuation=ContinuationPayload(task_state={"resume": "here"}),
        continuation_hooks=ContinuationHooks(
            status_topic="coding.status",
            completion_topic="coding.completion",
            escalation_topic="coding.escalation",
        ),
        metadata={"parent_marker": "kept"},
    )

    child = _child_packet(
        sender=parent.recipient,
        recipient=CellAddress(cell_id="coding-build.worker-pool", organism_id="coding-organism"),
        parent_packet=parent,
        parent_signal_id="signal:parent",
        lineage_suffix="tissue:coding-build.worker-pool",
        task_id="coding-root:workers:1",
        instruction="Run the worker pool.",
        scope="coding-organism.workers",
        hard_constraints=[],
        soft_constraints=[],
        input_payload={"objective": "fix it"},
        evidence_refs=[],
        output_contract=parent.output_contract,
        authority=WorkerAuthority.DELEGATE,
        metadata={"child_marker": "set"},
    )

    assert child.budget_limits.max_completion_rounds == 2
    assert child.budget_limits.max_tool_calls == 7
    assert child.authority_limits.acting_authority == WorkerAuthority.DELEGATE
    assert child.authority_limits.max_spawned_cells == 1
    assert child.authority_limits.allow_memory_write_scopes == ["notes"]
    assert child.authority_limits.allowed_toolset_refs == ["local-read"]
    assert child.continuation is not None
    assert child.continuation.task_state == {"resume": "here"}
    assert child.continuation_hooks.reply_to_cell_id == parent.recipient.cell_id
    assert child.continuation_hooks.resume_from_packet_id == parent.packet_id
    assert child.continuation_hooks.status_topic == "coding.status"
    assert child.metadata["parent_marker"] == "kept"
    assert child.metadata["child_marker"] == "set"


@pytest.mark.asyncio
async def test_coding_execution_runtime_closes_a_bounded_repair_loop(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_CodingOrganismProvider())
    organism = coding_execution_organism(model="stub-model")
    trace_log = CrossCellTraceLog()

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=trace_log,
    )

    assert execution.result is not None
    assert execution.result.status == "completed"
    assert execution.result.selected_attempt == 2
    assert execution.result.improved_via_repair is True
    assert execution.result.validation_scores == [0.76, 0.97]
    assert execution.result.final_output["candidate_id"] == "candidate-2"
    assert execution.result.final_output["validation_report"]["passed"] is True
    assert execution.result.metadata["orchestrator_runs"] == 2
    assert execution.result.metadata["worker_pool_attempts"] == 2
    assert execution.worker_pool_executions[0].result.metadata["successful_member_ids"] == ["worker-1", "worker-2"]
    assert execution.worker_pool_executions[1].result.metadata["successful_member_ids"] == ["worker-1", "worker-2", "worker-3"]
    assert [record.stage for record in execution.result.observability.stage_records] == [
        "orchestration",
        "workers",
        "aggregation",
        "validation",
        "orchestration",
        "workers",
        "aggregation",
        "validation",
    ]


@pytest.mark.asyncio
async def test_coding_execution_caps_validation_timeout_metadata(tmp_path) -> None:
    provider = _MetadataCaptureCodingOrganismProvider()
    executor = WorkerCoreExecutor(completion_provider=provider)
    organism = coding_execution_organism(model="stub-model")
    task = _task(tmp_path).model_copy(
        update={
            "session_context": {
                **dict(_task(tmp_path).session_context),
                "completion_timeout_seconds": 90.0,
                "short_completion_timeout": False,
            }
        }
    )

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=task,
        trace_log=CrossCellTraceLog(),
    )

    assert execution.result.status == "completed"
    worker_metadata = provider.metadata_by_worker["coding-build.worker-1"][0]
    assert worker_metadata["completion_timeout_seconds"] == 90.0
    validator_metadata = provider.metadata_by_worker["coding-build.validator.reviewer-a"][0]
    assert validator_metadata["completion_timeout_seconds"] == 30.0
    assert validator_metadata["short_completion_timeout"] is True
    assert validator_metadata["disable_timeout_recovery"] is True
    assert validator_metadata["validation_timeout_cap_seconds"] == 30.0
    assert validator_metadata["inherited_completion_timeout_seconds"] == 90.0


@pytest.mark.asyncio
async def test_coding_execution_tolerates_fenced_verbose_organ_outputs(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_FencedVerboseCodingOrganismProvider())
    organism = coding_execution_organism(model="stub-model")

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
    )

    assert execution.result is not None
    assert execution.result.status == "completed"
    assert execution.result.final_output["candidate_id"] == "candidate-2"
    assert execution.result.final_output["extra_note"] == "aggregation-extra"
    assert execution.result.final_output["validation_report"]["extra_note"] == "validation-extra"


@pytest.mark.asyncio
async def test_coding_execution_preserves_material_output_when_aggregation_contract_escalates(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_AggregationEscalationProvider())
    organism = coding_execution_organism(model="stub-model")

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
    )

    assert execution.result is not None
    assert execution.result.status == "failed"
    assert execution.result.final_output["candidate_id"] == "candidate-partial"
    assert execution.result.final_output["target_files"] == [
        "/workspace/absharks/index.html",
        "/workspace/absharks/styles.css",
    ]
    assert execution.result.error == "Aggregation failed: Organ output contract failed. (missing_output:risks)"


@pytest.mark.asyncio
async def test_coding_execution_fails_fast_when_workers_produce_no_material_candidate(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_NoMaterialWorkerProvider())
    organism = coding_execution_organism(model="stub-model").model_copy(
        update={"max_repair_rounds": 0}
    )

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
    )

    assert execution.result is not None
    assert execution.result.status == "failed"
    assert execution.result.final_output == {}
    assert execution.result.error == (
        "Aggregation failed: Workers produced no material bounded candidate to merge; stop and re-plan instead of "
        "rediscovering the workspace in aggregation."
    )
    assert execution.result.metadata["aggregation_attempts"] == 0


@pytest.mark.asyncio
async def test_coding_execution_no_material_existing_artifacts_fails_without_task_specific_materializer(
    tmp_path,
) -> None:
    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Demo</title>
  <link rel="stylesheet" href="./styles.css">
</head>
<body>
  <header class="site-header">Demo</header>
  <main>
    <section class="hero"><p class="eyebrow">Network</p><h1>DAN</h1><p class="lede">Agent runtime.</p></section>
    <section class="features" id="features"><article><h2>Fast</h2><p>Parallel paths.</p></article></section>
  </main>
  <script src="./app.js"></script>
</body>
</html>
""",
        encoding="utf-8",
    )
    (tmp_path / "styles.css").write_text(
        "body { margin: 0; font-family: system-ui; }\n",
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        "console.log('ready');\n",
        encoding="utf-8",
    )
    task = CodingTask(
        task_id="website-refresh",
        objective=(
            "Inspect the website entry-point files, then apply a bounded dark visual refresh "
            "with typography, spacing, layout, transitions, and micro-animations."
        ),
        acceptance_criteria=[
            "Only existing files are modified.",
            "Return complete updated content for every modified file.",
        ],
        session_context={"workspace_root": str(tmp_path)},
    )
    executor = WorkerCoreExecutor(completion_provider=_NoMaterialWebsiteRefreshProvider())
    organism = coding_execution_organism(model="stub-model").model_copy(
        update={"max_repair_rounds": 0}
    )
    events: list[dict[str, object]] = []

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=task,
        trace_log=CrossCellTraceLog(),
        event_callback=events.append,
    )

    assert execution.result is not None
    assert execution.result.status == "failed"
    assert execution.result.final_output == {}
    assert execution.result.error == (
        "Aggregation failed: Workers produced no material bounded candidate to merge; stop and re-plan instead of "
        "rediscovering the workspace in aggregation."
    )
    assert "DAN Code deterministic website refresh fallback" not in (tmp_path / "styles.css").read_text(encoding="utf-8")
    assert "DAN Code deterministic website refresh fallback" not in (tmp_path / "app.js").read_text(encoding="utf-8")
    assert 'class="dan-code-refresh"' not in (tmp_path / "index.html").read_text(encoding="utf-8")
    assert not any(event["event"] == "deterministic_materializer.applied" for event in events)


@pytest.mark.asyncio
async def test_coding_execution_replans_after_no_material_candidate_when_repair_budget_exists(
    tmp_path,
) -> None:
    executor = WorkerCoreExecutor(completion_provider=_NoMaterialThenRepairSuccessProvider())
    organism = coding_execution_organism(model="stub-model")

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
    )

    assert execution.result is not None
    assert execution.result.status == "completed"
    assert execution.result.selected_attempt == 2
    assert execution.result.final_output["target_files"] == [
        "src/dan/worker/organisms/coding_execution.py"
    ]
    assert execution.result.metadata["selected_candidate_source"] == "aggregation"
    assert len(execution.result.final_output["repair_history"]) == 2
    assert execution.result.final_output["repair_history"][0]["candidate_id"] is None
    assert "smaller write-first attempt" in execution.result.final_output["repair_history"][0]["repair_brief"]


@pytest.mark.asyncio
async def test_coding_execution_does_not_complete_below_pass_threshold(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_BelowThresholdPassProvider())
    organism = coding_execution_organism(model="stub-model").model_copy(
        update={"max_repair_rounds": 0}
    )

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
    )

    assert execution.result is not None
    assert execution.result.status == "failed"
    assert execution.result.selected_attempt == 1
    assert execution.result.final_output["candidate_id"] == "candidate-below-threshold"
    assert execution.result.validation_scores == [0.6]
    assert execution.result.metadata["selected_pass_threshold"] == pytest.approx(0.9)
    assert "below the required pass threshold" in (execution.result.error or "")


@pytest.mark.asyncio
async def test_coding_execution_completes_when_validator_pass_is_within_delivery_tolerance(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_NearThresholdPassProvider())
    organism = coding_execution_organism(model="stub-model").model_copy(
        update={"max_repair_rounds": 0}
    )

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
    )

    assert execution.result is not None
    assert execution.result.status == "completed"
    assert execution.result.selected_attempt == 1
    assert execution.result.final_output["candidate_id"] == "candidate-near-threshold"
    assert execution.result.validation_scores == [0.88]
    assert execution.result.metadata["selected_pass_threshold"] == pytest.approx(0.9)
    assert execution.result.metadata["delivery_pass_tolerance"] == pytest.approx(0.03)
    assert execution.result.metadata["selected_within_delivery_tolerance"] is True
    assert execution.result.error is None


@pytest.mark.asyncio
async def test_coding_execution_emits_public_progress_events(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_CodingOrganismProvider())
    organism = coding_execution_organism(model="stub-model")
    events: list[dict[str, object]] = []

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
        event_callback=events.append,
    )

    assert execution.result is not None
    event_names = [str(event["event"]) for event in events]
    assert event_names[0] == "organism.started"
    assert "attempt.started" in event_names
    assert event_names.count("stage.started") == 8
    assert event_names.count("stage.completed") == 8
    assert event_names.count("status.update") >= 10
    assert "repair.requested" in event_names
    assert event_names[-1] == "organism.completed"
    repair_event = next(event for event in events if event["event"] == "repair.requested")
    assert repair_event["attempt"] == 1
    assert repair_event["missing_requirements"] == [
        "focused validation command",
        "explicit delivery summary",
    ]
    orchestration_event = next(
        event
        for event in events
        if event["event"] == "stage.completed" and event["stage"] == "orchestration"
    )
    assert orchestration_event["worker_count"] == 2
    assert orchestration_event["worker_briefs"] == [
        "Inspect the narrow failure path and propose the smallest viable patch.",
        "Focus on keeping the validation command and delivery summary explicit.",
    ]
    aggregation_event = next(
        event
        for event in events
        if event["event"] == "stage.completed" and event["stage"] == "aggregation"
    )
    assert aggregation_event["candidate_id"] == "candidate-1"
    assert aggregation_event["target_files"] == ["src/dan/worker/organisms/coding_execution.py"]
    assert aggregation_event["test_plan"] == []
    validation_event = next(
        event
        for event in events
        if event["event"] == "stage.completed"
        and event["stage"] == "validation"
        and event["attempt"] == 1
    )
    assert validation_event["missing_requirements"] == [
        "focused validation command",
        "explicit delivery summary",
    ]
    assert validation_event["comparison_note"] == (
        "Candidate 1 is narrow but still under-specifies validation and delivery."
    )
    orchestration_update = next(
        event
        for event in events
        if event["event"] == "status.update"
        and event["actor"] == "orchestrator"
        and event["phase"] == "orchestration"
        and event["attempt"] == 1
    )
    assert orchestration_update["worker_count"] == 2
    assert orchestration_update["message"] == (
        "I understand this as a bounded runtime repair request, so I will inspect the validator and delivery path before patching."
    )
    worker_update = next(
        event
        for event in events
        if event["event"] == "status.update"
        and event["actor"] == "worker-1"
        and event["phase"] == "workers"
        and event["attempt"] == 1
    )
    assert worker_update["message"] == "Narrow validator-path patch."
    assert worker_update["target_files"] == ["src/dan/worker/organisms/coding_execution.py"]
    validator_update = next(
        event
        for event in events
        if event["event"] == "status.update"
        and event["actor"] == "validator"
        and event["phase"] == "validation"
        and event["attempt"] == 1
    )
    assert validator_update["status"] == "needs_repair"
    assert validator_update["message"] == (
        "Candidate 1 is narrow but still under-specifies validation and delivery."
    )
    repair_update = next(
        event
        for event in events
        if event["event"] == "status.update"
        and event["actor"] == "validator"
        and event["phase"] == "repair"
        and event["attempt"] == 1
    )
    assert repair_update["message"] == (
        "Make the focused validation command explicit and keep the delivery summary inspectable."
    )
    delivery_update = next(
        event
        for event in events
        if event["event"] == "status.update"
        and event["actor"] == "orchestrator"
        and event["phase"] == "delivery"
    )
    assert delivery_update["candidate_id"] == "candidate-2"
    assert delivery_update["attempt"] == 2
    completed_event = events[-1]
    assert completed_event["status"] == "completed"
    assert completed_event["candidate_id"] == "candidate-2"


class _MissingWorkerCountProvider:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")

        if worker_id == "coding-build.orchestrator":
            payload = {
                "public_response": "This is a narrow fix, so I will start with one focused worker.",
                "worker_briefs": [
                    "Inspect the concrete failing seam and produce one bounded fix."
                ],
                "aggregation_focus": "Merge the one worker result into one bounded candidate.",
                "validator_focus": "Reject any candidate that leaves the core fix incomplete.",
                "pass_threshold": 0.9,
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.worker-1":
            payload = {
                "candidate_fragment": "Patch the concrete failing seam in one file.",
                "change_summary": "One focused patch path.",
                "target_files": ["src/dan/worker/organisms/coding_execution.py"],
                "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py"],
                "risks": ["Could still need a follow-up if the validator finds a gap."],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.aggregation.lead":
            payload = {
                "candidate_id": "candidate-single-worker",
                "change_summary": "Merged the one focused worker result into one bounded candidate.",
                "target_files": ["src/dan/worker/organisms/coding_execution.py"],
                "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py"],
                "risks": ["Residual regression risk is low."],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id in {"coding-build.validator.reviewer-a", "coding-build.validator.reviewer-b"}:
            return CompletionResponse(text="pass", raw={"worker_id": worker_id})

        if worker_id == "coding-build.validator.lead":
            payload = {
                "passed": True,
                "overall_score": 0.96,
                "dimension_scores": {
                    "boundedness": 0.97,
                    "validation_specificity": 0.95,
                    "delivery_explicitness": 0.96,
                },
                "repair_brief": "",
                "missing_requirements": [],
                "comparison_note": "The single focused worker path was sufficient.",
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        raise AssertionError(f"Unexpected worker_id: {worker_id}")


class _ValidatorTieProvider:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")

        if worker_id == "coding-build.orchestrator":
            payload = {
                "public_response": "Inspect one bounded candidate path before validating it.",
                "worker_count": 1,
                "worker_briefs": ["Inspect one focused repair path."],
                "aggregation_focus": "Return one bounded candidate.",
                "validator_focus": "Prefer repair when validator reviewers disagree.",
                "pass_threshold": 0.9,
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.worker-1":
            payload = {
                "candidate_fragment": "One focused candidate fragment.",
                "change_summary": "One focused candidate path.",
                "target_files": ["src/dan/worker/organisms/coding_execution.py"],
                "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py"],
                "risks": ["The candidate may still need repair."],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.aggregation.lead":
            payload = {
                "candidate_id": "candidate-tie",
                "change_summary": "Merged the one focused worker result.",
                "target_files": ["src/dan/worker/organisms/coding_execution.py"],
                "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py"],
                "risks": ["The candidate still needs repair."],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.validator.reviewer-a":
            return CompletionResponse(text="pass", raw={"worker_id": worker_id})
        if worker_id == "coding-build.validator.reviewer-b":
            return CompletionResponse(text="repair", raw={"worker_id": worker_id})

        if worker_id == "coding-build.validator.lead":
            payload = {
                "passed": False,
                "overall_score": 0.61,
                "dimension_scores": {
                    "boundedness": 0.9,
                    "validation_specificity": 0.55,
                    "delivery_explicitness": 0.61,
                },
                "repair_brief": "Keep the patch bounded but make the concrete validation step explicit.",
                "missing_requirements": ["explicit validation step"],
                "comparison_note": "The reviewer tie should resolve conservatively to repair.",
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        raise AssertionError(f"Unexpected worker_id: {worker_id}")


class _NoMutationEvidenceProvider:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")

        if worker_id == "coding-build.orchestrator":
            payload = {
                "public_response": "Inspect one narrow fix path and aggregate it.",
                "worker_count": 1,
                "worker_briefs": ["Inspect one narrow fix path."],
                "aggregation_focus": "Return one bounded candidate with explicit files.",
                "validator_focus": "Validate the candidate.",
                "pass_threshold": 0.9,
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.worker-1":
            payload = {
                "candidate_fragment": "Claim one focused file fix.",
                "change_summary": "Claimed focused file fix.",
                "target_files": ["/workspace/absharks/shark-anatomy.html"],
                "test_plan": ["open /workspace/absharks/shark-anatomy.html"],
                "risks": ["The claim may not match the actual workspace."],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.aggregation.lead":
            payload = {
                "candidate_id": "candidate-no-write",
                "change_summary": "Claimed to fix the target file without applying a mutation.",
                "target_files": ["/workspace/absharks/shark-anatomy.html"],
                "test_plan": ["open /workspace/absharks/shark-anatomy.html"],
                "risks": ["The candidate may not reflect a real workspace change."],
            }
            return CompletionResponse(
                text=json.dumps(payload, sort_keys=True),
                raw={
                    "worker_id": worker_id,
                    "executed_tools": [
                        {
                            "tool_id": "file_read",
                            "ok": True,
                            "arguments": {"path": "/workspace/absharks/shark-anatomy.html"},
                            "result": {
                                "path": "/workspace/absharks/shark-anatomy.html",
                                "line_count": 281,
                                "size": 15146,
                            },
                        }
                    ],
                },
            )

        raise AssertionError(f"Unexpected worker_id: {worker_id}")


class _EmptyModifiedCandidateProvider:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")

        if worker_id == "coding-build.orchestrator":
            payload = {
                "public_response": "Inspect one narrow fix path and aggregate it.",
                "worker_count": 2,
                "worker_briefs": [
                    "Inspect one narrow fix path.",
                    "Cross-check the same narrow fix path.",
                ],
                "aggregation_focus": "Return one bounded candidate with explicit files.",
                "validator_focus": "Validate the candidate.",
                "pass_threshold": 0.9,
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id in {"coding-build.worker-1", "coding-build.worker-2"}:
            payload = {
                "candidate_fragment": "Prepare the narrow runtime patch for aggregation.",
                "change_summary": "Prepared one narrow runtime patch for aggregation.",
                "target_files": ["src/dan/worker/organisms/coding_execution.py"],
                "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py"],
                "risks": ["Aggregator may still omit target files."],
            }
            return CompletionResponse(
                text=json.dumps(payload, sort_keys=True),
                raw={
                    "worker_id": worker_id,
                    "executed_tools": [
                        {
                            "tool_id": "file_write",
                            "ok": True,
                            "arguments": {
                                "path": "tmp_read.py",
                                "content": "print('inspection only')\n",
                            },
                            "result": {"path": "tmp_read.py"},
                        }
                    ],
                },
            )

        if worker_id == "coding-build.aggregation.lead":
            payload = {
                "candidate_id": "candidate-empty-modified",
                "change_summary": "The worker did not leave a materialized workspace patch.",
                "target_files": [],
                "test_plan": [],
                "risks": ["No target files were changed."],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        raise AssertionError(f"Unexpected worker_id: {worker_id}")


def _owner_mutation_tool_record(
    *,
    worker_id: str,
    path: str,
    owner_path: str,
) -> dict[str, object]:
    capsule_id = f"ctx:{worker_id}:{owner_path}"
    return {
        "tool_id": "file_edit",
        "ok": True,
        "arguments": {"path": path},
        "result": {"path": path},
        "context_capsules": [
            {
                "capsule_id": capsule_id,
                "kind": "implementation_delta",
                "source_task_id": "website-refresh",
                "source_worker_id": worker_id,
                "summary": f"Changed {owner_path}",
                "artifact_state": "useful_for_downstream",
                "unlocks": ["implementation_delta", f"file_changed:{path}"],
                "confidence": 0.9,
            }
        ],
        "readiness_signal": {
            "readiness_id": f"ready:{worker_id}:{owner_path}",
            "source_task_id": "website-refresh",
            "source_worker_id": worker_id,
            "capsule_ids": [capsule_id],
            "ready_for_downstream": True,
            "predicate": "tool_context_available",
            "summary": f"{owner_path} is ready for downstream aggregation.",
            "blockers": [],
            "downstream_task_ids": [],
        },
    }


class _WorkerMutationAggregationTimeoutProvider:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")

        if worker_id == "coding-build.orchestrator":
            payload = {
                "public_response": "Patch two owned files in parallel and aggregate the result.",
                "worker_count": 2,
                "worker_briefs": [
                    "EXCLUSIVE WRITE OWNER: index.html. Patch the page markup only.",
                    "EXCLUSIVE WRITE OWNER: styles.css. Patch the stylesheet only.",
                ],
                "aggregation_focus": "Merge the two owned-file worker outputs into one candidate.",
                "validator_focus": "Validate the merged candidate.",
                "pass_threshold": 0.9,
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.worker-1":
            payload = {
                "candidate_fragment": {"index.html": "<main>Updated markup</main>\n"},
                "change_summary": "Materialized index.html before provider completion timeout.",
                "target_files": ["index.html"],
                "test_plan": ["Runtime read back index.html from disk."],
                "risks": ["The final worker JSON was synthesized from tool evidence."],
                "synthesized_from_tool_evidence": True,
            }
            return CompletionResponse(
                text=json.dumps(payload, sort_keys=True),
                raw={
                    "worker_id": worker_id,
                    "executed_tools": [
                        _owner_mutation_tool_record(
                            worker_id=worker_id,
                            path="/workspace/site/index.html",
                            owner_path="index.html",
                        )
                    ],
                },
            )

        if worker_id == "coding-build.worker-2":
            payload = {
                "candidate_fragment": {"styles.css": "body { background: #08090c; }\n"},
                "change_summary": "Updated styles.css dark theme and transitions.",
                "target_files": ["styles.css"],
                "test_plan": ["open index.html and inspect styles"],
                "risks": ["Visual validation is still manual."],
            }
            return CompletionResponse(
                text=json.dumps(payload, sort_keys=True),
                raw={
                    "worker_id": worker_id,
                    "executed_tools": [
                        _owner_mutation_tool_record(
                            worker_id=worker_id,
                            path="/workspace/site/styles.css",
                            owner_path="styles.css",
                        )
                    ],
                },
            )

        if worker_id == "coding-build.aggregation.lead":
            payload = {
                "candidate_id": "Provider completion timed out before generation finished.",
                "change_summary": "Provider completion timed out before generation finished.",
                "target_files": [],
                "test_plan": [],
                "risks": [],
                "workspace_effect": "modified",
            }
            return CompletionResponse(
                text=json.dumps(payload, sort_keys=True),
                raw={
                    "worker_id": worker_id,
                    "executed_tools": [
                        {
                            "tool_id": "file_read",
                            "ok": True,
                            "arguments": {"path": "/workspace/site/index.html"},
                            "result": {"path": "/workspace/site/index.html"},
                        }
                    ],
                },
            )

        if worker_id == "coding-build.validator.reviewer-a":
            return CompletionResponse(text="pass", raw={"worker_id": worker_id})
        if worker_id == "coding-build.validator.reviewer-b":
            return CompletionResponse(text="pass", raw={"worker_id": worker_id})
        if worker_id == "coding-build.validator.lead":
            payload = {
                "passed": True,
                "overall_score": 0.94,
                "dimension_scores": {"boundedness": 0.95, "validation_specificity": 0.93},
                "repair_brief": "",
                "missing_requirements": [],
                "comparison_note": "The worker-backed merged candidate is acceptable.",
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        raise AssertionError(f"Unexpected worker_id: {worker_id}")


class _MissingExclusiveOwnerProvider:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")

        if worker_id == "coding-build.orchestrator":
            payload = {
                "public_response": "Patch the three website files in parallel.",
                "worker_count": 3,
                "worker_briefs": [
                    "EXCLUSIVE WRITE OWNER: index.html. Patch the page markup only.",
                    "EXCLUSIVE WRITE OWNER: styles.css. Patch the stylesheet only.",
                    "EXCLUSIVE WRITE OWNER: app.js. Patch the interaction layer only.",
                ],
                "aggregation_focus": "Merge the three owned-file worker outputs into one candidate.",
                "validator_focus": "Validate the merged website candidate.",
                "pass_threshold": 0.9,
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.worker-1":
            payload = {
                "candidate_fragment": "Updated the page markup.",
                "change_summary": "Updated index.html hero markup.",
                "target_files": ["/workspace/site/index.html"],
                "test_plan": ["open index.html"],
                "risks": [],
            }
            return CompletionResponse(
                text=json.dumps(payload, sort_keys=True),
                raw={
                    "worker_id": worker_id,
                    "executed_tools": [
                        {
                            "tool_id": "file_edit",
                            "ok": True,
                            "arguments": {"path": "/workspace/site/index.html"},
                            "result": {"path": "/workspace/site/index.html"},
                        }
                    ],
                },
            )

        if worker_id == "coding-build.worker-2":
            payload = {
                "candidate_id": "Provider completion timed out before generation finished.",
                "change_summary": "Provider completion timed out before generation finished.",
                "target_files": [],
                "test_plan": [],
                "risks": [],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.worker-3":
            payload = {
                "candidate_fragment": "Updated the interaction layer.",
                "change_summary": "Updated app.js scroll behavior.",
                "target_files": ["/workspace/site/app.js"],
                "test_plan": ["open index.html and test scrolling"],
                "risks": [],
            }
            return CompletionResponse(
                text=json.dumps(payload, sort_keys=True),
                raw={
                    "worker_id": worker_id,
                    "executed_tools": [
                        {
                            "tool_id": "file_edit",
                            "ok": True,
                            "arguments": {"path": "/workspace/site/app.js"},
                            "result": {"path": "/workspace/site/app.js"},
                        }
                    ],
                },
            )

        if worker_id == "coding-build.aggregation.lead":
            raise AssertionError("missing owner material should fail before aggregation")

        raise AssertionError(f"Unexpected worker_id: {worker_id}")


class _VerificationOnlyProvider:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")

        if worker_id == "coding-build.orchestrator":
            payload = {
                "public_response": "Inspect the current website state and report whether any change is actually needed.",
                "worker_count": 1,
                "worker_briefs": ["Inspect the current website state without mutating the workspace."],
                "aggregation_focus": "Return one bounded verification result with explicit files and validation.",
                "validator_focus": "Validate whether the verification result is explicit and bounded.",
                "pass_threshold": 0.9,
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.worker-1":
            payload = {
                "candidate_fragment": "Inspect the current shark page and confirm whether it already matches the shared styles.",
                "change_summary": "No code changes required.",
                "target_files": ["/workspace/absharks/shark-anatomy.html"],
                "test_plan": ["open /workspace/absharks/shark-anatomy.html"],
                "risks": ["Visual verification is still needed in a browser."],
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.aggregation.lead":
            payload = {
                "candidate_id": "candidate-verified",
                "change_summary": "Verification only - no code changes required. The page already matches the shared styling.",
                "target_files": ["/workspace/absharks/shark-anatomy.html"],
                "test_plan": ["open /workspace/absharks/shark-anatomy.html"],
                "risks": ["Visual verification is still needed in a browser."],
            }
            return CompletionResponse(
                text=json.dumps(payload, sort_keys=True),
                raw={
                    "worker_id": worker_id,
                    "executed_tools": [
                        {
                            "tool_id": "list_directory",
                            "ok": True,
                            "arguments": {"path": "/workspace/absharks"},
                            "result": {
                                "entries": 7,
                            },
                        },
                        {
                            "tool_id": "file_read",
                            "ok": True,
                            "arguments": {"path": "/workspace/absharks/shark-anatomy.html"},
                            "result": {
                                "path": "/workspace/absharks/shark-anatomy.html",
                                "line_count": 205,
                                "size": 10569,
                            },
                        },
                    ],
                },
            )

        if worker_id == "coding-build.validator.reviewer-a":
            return CompletionResponse(text="pass", raw={"worker_id": worker_id})
        if worker_id == "coding-build.validator.reviewer-b":
            return CompletionResponse(text="pass", raw={"worker_id": worker_id})
        if worker_id == "coding-build.validator.lead":
            payload = {
                "passed": True,
                "overall_score": 0.96,
                "dimension_scores": {
                    "boundedness": 0.97,
                    "validation_specificity": 0.94,
                    "delivery_explicitness": 0.96,
                },
                "repair_brief": "",
                "missing_requirements": [],
                "comparison_note": "The verification-only candidate stayed bounded and made the no-change result explicit.",
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        raise AssertionError(f"Unexpected worker_id: {worker_id}")


class _DirectWorkerValidationProvider:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")

        if worker_id == "coding-build.orchestrator":
            payload = {
                "public_response": "Use one bounded owner to patch the failing seam directly, then validate it.",
                "worker_count": 1,
                "worker_briefs": ["Patch the one failing seam directly and keep validation explicit."],
                "aggregation_focus": "Only aggregate if more than one worker path is needed.",
                "validator_focus": "Validate the direct bounded candidate against the acceptance bar.",
                "pass_threshold": 0.9,
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "coding-build.worker-1":
            payload = {
                "candidate_fragment": "Patch the concrete failing seam directly in the runtime file.",
                "change_summary": "Patched the one failing seam directly.",
                "target_files": ["src/dan/worker/organisms/coding_execution.py"],
                "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_coding_organism.py"],
                "risks": ["Residual risk is low after validator review."],
            }
            return CompletionResponse(
                text=json.dumps(payload, sort_keys=True),
                raw={
                    "worker_id": worker_id,
                    "executed_tools": [
                        {
                            "tool_id": "file_edit",
                            "ok": True,
                            "arguments": {
                                "path": "src/dan/worker/organisms/coding_execution.py",
                                "start_line": 1,
                                "content": "patched",
                            },
                            "result": {"path": "src/dan/worker/organisms/coding_execution.py"},
                        }
                    ],
                },
            )

        if worker_id == "coding-build.aggregation.lead":
            raise AssertionError("direct single-worker path should bypass aggregation")

        if worker_id in {"coding-build.validator.reviewer-a", "coding-build.validator.reviewer-b"}:
            return CompletionResponse(text="pass", raw={"worker_id": worker_id})

        if worker_id == "coding-build.validator.lead":
            payload = {
                "passed": True,
                "overall_score": 0.95,
                "dimension_scores": {
                    "boundedness": 0.95,
                    "validation_specificity": 0.95,
                    "delivery_explicitness": 0.94,
                },
                "repair_brief": "",
                "missing_requirements": [],
                "comparison_note": "The direct single-worker candidate is sufficient and bounded.",
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        raise AssertionError(f"Unexpected worker_id: {worker_id}")


@pytest.mark.asyncio
async def test_coding_execution_defaults_to_one_worker_when_orchestrator_omits_worker_count(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_MissingWorkerCountProvider())
    organism = coding_execution_organism(model="stub-model")
    events: list[dict[str, object]] = []

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
        event_callback=events.append,
    )

    assert execution.result is not None
    assert execution.result.status == "completed"
    assert execution.worker_pool_executions[0].result.metadata["successful_member_ids"] == ["worker-1"]
    orchestration_event = next(
        event
        for event in events
        if event["event"] == "stage.completed" and event["stage"] == "orchestration"
    )
    assert orchestration_event["worker_count"] == 1
    assert orchestration_event["worker_briefs"] == [
        "Inspect the concrete failing seam and produce one bounded fix."
    ]


@pytest.mark.asyncio
async def test_coding_execution_promotes_single_write_capable_worker_directly(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_DirectWorkerValidationProvider())
    organism = coding_execution_organism(model="stub-model")

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
    )

    assert execution.result is not None
    assert execution.result.status == "completed"
    assert execution.result.final_output["candidate_id"] == "candidate-1-worker-1"
    assert execution.result.final_output["validation_report"]["passed"] is True
    assert execution.result.metadata["selected_candidate_source"] == "worker"
    assert execution.aggregation_executions == []
    assert [record.stage for record in execution.result.observability.stage_records] == [
        "orchestration",
        "workers",
        "aggregation",
        "validation",
    ]
    aggregation_record = next(
        record
        for record in execution.result.observability.stage_records
        if record.stage == "aggregation"
    )
    assert aggregation_record.summary == "Promoted worker-1 as the direct bounded candidate."


@pytest.mark.asyncio
async def test_coding_execution_resolves_validator_ties_to_repair(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_ValidatorTieProvider())
    organism = coding_execution_organism(model="stub-model").model_copy(update={"max_repair_rounds": 0})

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
    )

    assert execution.result is not None
    assert execution.result.status == "failed"
    assert execution.result.error == "The best candidate still failed validation."
    assert execution.validation_executions[0].result.status == "completed"
    assert execution.validation_executions[0].result.outputs["comparison_note"] == (
        "The reviewer tie should resolve conservatively to repair."
    )
    assert execution.validation_executions[0].tissue_execution is not None
    assert execution.validation_executions[0].tissue_execution.result.quorum is not None
    assert execution.validation_executions[0].tissue_execution.result.quorum.resolved_by == "tie_break_priority"
    assert execution.validation_executions[0].tissue_execution.result.quorum.decision == "repair"


@pytest.mark.asyncio
async def test_coding_execution_rejects_claimed_file_changes_without_mutation_evidence(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_NoMutationEvidenceProvider())
    organism = coding_execution_organism(model="stub-model")

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
    )

    assert execution.result is not None
    assert execution.result.status == "failed"
    assert execution.result.error == (
        "Aggregation failed: Candidate claimed concrete file changes but no mutation-capable tool call succeeded."
    )
    assert execution.validation_executions == []
    assert execution.aggregation_executions[0].result.metadata["mutation_evidence_required"] is True
    assert execution.aggregation_executions[0].result.metadata["claimed_target_files"] == [
        "/workspace/absharks/shark-anatomy.html"
    ]


@pytest.mark.asyncio
async def test_coding_execution_rejects_modified_candidate_without_target_files(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_EmptyModifiedCandidateProvider())
    organism = coding_execution_organism(model="stub-model")

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
    )

    assert execution.result is not None
    assert execution.result.status == "failed"
    assert execution.result.error == (
        "Aggregation failed: Candidate claimed a modified workspace but did not name target files."
    )
    assert execution.validation_executions == []
    assert execution.aggregation_executions[0].result.metadata["mutation_evidence_required"] is True
    assert execution.aggregation_executions[0].result.metadata["target_files_required"] is True
    assert execution.aggregation_executions[0].result.metadata["claimed_target_files"] == []


@pytest.mark.asyncio
async def test_coding_execution_augments_aggregation_timeout_from_worker_mutations(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_WorkerMutationAggregationTimeoutProvider())
    organism = coding_execution_organism(model="stub-model")
    events: list[dict[str, object]] = []

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
        event_callback=events.append,
    )

    assert execution.result is not None
    assert execution.result.status == "completed"
    assert execution.result.error is None
    assert execution.result.final_output["candidate_id"] == "candidate-1-worker-merge"
    assert execution.result.final_output["target_files"] == ["index.html", "styles.css"]
    assert execution.result.final_output["workspace_effect"] == "modified"
    assert execution.result.final_output["candidate_fragment"] == {
        "index.html": "<main>Updated markup</main>\n",
        "styles.css": "body { background: #08090c; }\n",
    }
    assert "Updated styles.css dark theme and transitions." in execution.result.final_output["change_summary"]
    assert execution.aggregation_executions == []
    assert execution.result.metadata["selected_candidate_source"] == "worker_merge"
    readiness_history = execution.result.metadata["readiness_ledger_history"]
    assert readiness_history[0]["all_owner_paths_ready"] is True
    assert readiness_history[0]["ready_owner_paths"] == ["index.html", "styles.css"]
    assert {lane["readiness_source"] for lane in readiness_history[0]["lanes"]} == {
        "capsule_scheduler"
    }
    readiness_event = next(
        event for event in events if event["event"] == "scheduler.readiness.evaluated"
    )
    assert readiness_event["all_owner_paths_ready"] is True
    assert readiness_event["ready_owner_paths"] == ["index.html", "styles.css"]
    assert execution.validation_executions[0].result.status == "completed"


@pytest.mark.asyncio
async def test_coding_execution_short_timeout_deterministically_merges_parallel_owner_workers(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_WorkerMutationAggregationTimeoutProvider())
    organism = coding_execution_organism(model="stub-model")
    task = _task(tmp_path)
    task = task.model_copy(
        update={
            "session_context": {
                **dict(task.session_context),
                "completion_timeout_seconds": 45.0,
                "short_completion_timeout": True,
            }
        }
    )

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=task,
        trace_log=CrossCellTraceLog(),
    )

    assert execution.result is not None
    assert execution.result.status == "completed"
    assert execution.aggregation_executions == []
    assert execution.result.final_output["candidate_id"] == "candidate-1-worker-merge"
    assert execution.result.final_output["target_files"] == ["index.html", "styles.css"]
    assert execution.result.metadata["selected_candidate_source"] == "worker_merge"


@pytest.mark.asyncio
async def test_coding_execution_fails_fast_when_parallel_owner_lane_has_no_material(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_MissingExclusiveOwnerProvider())
    organism = coding_execution_organism(model="stub-model").model_copy(
        update={"max_repair_rounds": 0}
    )
    events: list[dict[str, object]] = []

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
        event_callback=events.append,
    )

    assert execution.result is not None
    assert execution.result.status == "failed"
    assert execution.result.error == (
        "Aggregation failed: Required parallel owner lanes produced no material mutation evidence: styles.css"
    )
    assert execution.aggregation_executions == []
    assert execution.validation_executions == []
    aggregation_event = next(
        event
        for event in events
        if event["event"] == "stage.completed" and event["stage"] == "aggregation"
    )
    assert aggregation_event["missing_owner_paths"] == ["styles.css"]


@pytest.mark.asyncio
async def test_coding_execution_allows_verification_only_candidates_without_mutation_evidence(tmp_path) -> None:
    executor = WorkerCoreExecutor(completion_provider=_VerificationOnlyProvider())
    organism = coding_execution_organism(model="stub-model")

    execution = await execute_coding_organism(
        executor=executor,
        organism=organism,
        task=_task(tmp_path),
        trace_log=CrossCellTraceLog(),
    )

    assert execution.result is not None
    assert execution.result.status == "completed"
    assert execution.result.error is None
    assert execution.result.final_output["candidate_id"] == "candidate-verified"
    assert execution.result.final_output["workspace_effect"] == "verified"
    assert execution.result.final_output["target_files"] == [
        "/workspace/absharks/shark-anatomy.html"
    ]
    assert execution.validation_executions[0].result.status == "completed"
    assert execution.aggregation_executions[0].result.metadata.get("mutation_evidence_required") is None
