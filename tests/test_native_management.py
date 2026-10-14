import asyncio
import json
import sys

import pytest

from diane.native_workers.management import normalize_contract, record_review, worker_summary
from diane.native_workers.service import NativeTeam, current_team
from diane.tools.native_worker import native_worker


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["codex", "claude"])
async def test_contract_review_resume_and_compact_handoff(monkeypatch, tmp_path, backend):
    launched = []

    def launch(*args):
        launched.append(args)
        row = {"type": "result", "session_id": "test-session", "result": "x" * 5000}
        return [sys.executable, "-c", f"print({json.dumps(row)!r})"], {}

    monkeypatch.setattr("diane.native_workers.service.launch", launch)
    team = NativeTeam("parent", str(tmp_path), {backend: {"enabled": True}}, tmp_path / "workers")
    token = current_team.set(team)
    try:
        contract = {"scope": "Parser only", "acceptance": ["Malformed input is rejected"]}
        worker = await native_worker("start", backend=backend, prompt="Fix parser", isolate=False, contract=contract)
        identity = worker["worker_id"]
        assert worker["attempt"] == 1 and worker["lead_review"] is None
        assert "Malformed input is rejected" in launched[-1][2]
        await asyncio.gather(*team.tasks.values())
        compact = (await native_worker("status"))["workers"][0]
        assert compact["status"] == "completed" and compact["review_decision"] == "unreviewed"
        assert len(compact["response"]) == 2000 and compact["response_truncated"]
        detail = await native_worker("status", worker_id=identity)
        assert len(detail["response"]) == 5000 and detail["task_contract"] == contract
        review = await native_worker("review", worker_id=identity, attempt=1, decision="revise", evidence=["tests/parser.py: malformed case still fails"])
        assert review["status"] == "completed"  # Process success and acceptance remain separate.
        assert review["lead_review"]["decision"] == "revise"
        saved = json.loads((team.base / f"{identity}.json").read_text())
        assert saved["lead_review"] == review["lead_review"]
        resumed = await native_worker("resume", worker_id=identity, prompt="Fix only the malformed case")
        assert resumed["attempt"] == 2 and resumed["lead_review"] is None
        assert resumed["task_contract"] == contract
        assert launched[-1][-1] == "test-session"
        assert "Malformed input is rejected" in launched[-1][2]
        await asyncio.gather(*team.tasks.values())
        with pytest.raises(ValueError, match="current worker attempt"):
            await native_worker("review", worker_id=identity, attempt=1, decision="accepted", evidence=["old evidence"])
        assert team.records[identity]["lead_review"] is None
        accepted = await native_worker("review", worker_id=identity, attempt=2, decision="accepted", evidence=["tests/parser.py passes"])
        assert accepted["lead_review"]["attempt"] == 2
        events = [json.loads(line) for line in (team.base / f"{identity}.jsonl").read_text().splitlines()]
        assert [e["review"]["decision"] for e in events if e.get("type") == "worker.reviewed"] == ["revise", "accepted"]
        assert len(team.records) == 1
    finally:
        current_team.reset(token)
        await team.close()


@pytest.mark.parametrize("contract", [{"permission": "full"}, {"acceptance": [""]}, {"scope": 123}, {"acceptance": ["x" * 2001]}])
def test_contract_cannot_smuggle_permissions_or_invalid_criteria(contract):
    with pytest.raises(ValueError):
        normalize_contract(contract)


@pytest.mark.parametrize("status", ["running", "needs_input", "failed", "stopped", "interrupted"])
def test_unfinished_or_failed_work_cannot_be_accepted(status):
    row = {"attempt": 2, "status": status}
    with pytest.raises(ValueError):
        record_review(row, 2, "accepted", ["claimed success"])
    assert "lead_review" not in row


def test_reviews_require_evidence_and_do_not_claim_native_subagent_control():
    row = {"attempt": 1, "status": "completed"}
    with pytest.raises(ValueError, match="evidence"):
        record_review(row, 1, "accepted", [])
    with pytest.raises(ValueError, match="Observed"):
        record_review({**row, "can_stop": False}, 1, "accepted", ["check"])


def test_summary_omits_large_private_fields_and_does_not_mutate_record():
    row = {"profile": {"secret": "hidden"}, "history": ["x" * 100000], "source_files": {"a": "hash"},
           "response": "x" * 100000, "prompt": "y" * 100000, "error": "z" * 100000,
           "task_contract": {"scope": "full task", "acceptance": ["criterion"]}}
    summary = worker_summary(row)
    assert not {"profile", "history", "source_files", "task_contract"} & summary.keys()
    assert len(json.dumps(summary)) < 4500
    assert len(row["response"]) == 100000
