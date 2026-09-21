import json
from pathlib import Path

from dan.native_workers import token_usage as tu


def _claude_session(home: Path) -> Path:
    project = home / "projects/-tmp-demo"; project.mkdir(parents=True)
    usage = lambda read, out: {"input_tokens": 10, "cache_read_input_tokens": read, "cache_creation_input_tokens": 100, "output_tokens": out}
    big = "x" * 48_000
    rows = [
        {"type": "user", "cwd": "/tmp/demo", "timestamp": "2026-09-21T01:00:00Z", "message": {"role": "user", "content": "fix the login test"}},
        # one API message written as two rows (text block, tool_use block) with the same usage
        {"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5", "usage": usage(1000, 50), "content": [{"type": "text", "text": "Looking."}]}},
        {"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5", "usage": usage(1000, 50), "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/tmp/demo/auth.py"}}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": big}]}},
        {"type": "assistant", "message": {"id": "m2", "model": "claude-opus-5", "usage": usage(13000, 40), "content": [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "cd /tmp/demo && pytest tests/test_auth.py -x"}}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "1 failed", "is_error": True}]}},
        {"type": "assistant", "message": {"id": "m3", "model": "claude-opus-5", "usage": usage(13100, 40), "content": [{"type": "tool_use", "id": "t3", "name": "Read", "input": {"file_path": "/tmp/demo/auth.py"}}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t3", "content": "short"}]}},
        {"type": "assistant", "message": {"id": "m4", "model": "claude-opus-5", "usage": usage(13200, 30), "content": [{"type": "text", "text": "Done."}]}},
        {"type": "user", "timestamp": "2026-09-21T02:00:00Z", "message": {"role": "user", "content": "thanks, commit it"}},
        {"type": "assistant", "message": {"id": "m5", "model": "claude-opus-5", "usage": usage(13300, 20), "content": [{"type": "tool_use", "id": "t4", "name": "Bash", "input": {"command": "git commit -am fix"}}]}},
    ]
    path = project / "sess-1.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    agents = project / "sess-1/subagents"; agents.mkdir(parents=True)
    (agents / "agent-a1.jsonl").write_text(json.dumps({"type": "assistant", "isSidechain": True, "message": {"id": "s1", "model": "claude-haiku-4-5", "usage": usage(0, 500), "content": [{"type": "text", "text": "report"}]}}) + "\n")
    (agents / "agent-a1.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "Map auth code", "toolUseId": "t9"}))
    return path


def _stores(monkeypatch, tmp_path, claude=None, codex=None):
    monkeypatch.setattr(tu, "_stores", lambda: [item for item in (("claude", "default", claude), ("codex", "default", codex)) if item[2]])


def test_claude_session_counts_each_api_message_once_and_attributes_burden(monkeypatch, tmp_path):
    home = tmp_path / ".claude"; _claude_session(home); _stores(monkeypatch, tmp_path, claude=home)
    base = tmp_path / "graphs"
    (base / "native_leads").mkdir(parents=True)
    (base / "native_leads/w1.json").write_text(json.dumps({"worker_id": "w1", "parent_run_id": "run9", "native_session_id": "sess-1"}))
    listed = tu.list_sessions(base)["sessions"]
    assert [row["id"] for row in listed] == ["claude-sess-1"]
    row = listed[0]
    assert row["totals"]["calls"] == 5 and row["totals"]["output"] == 180  # m1 appears twice in the file
    assert row["subagents"] == 1 and row["subagent_total"] == 610 and row["total"] == row["totals"]["total"] + 610
    assert row["title"] == "fix the login test" and row["cwd"] == "/tmp/demo" and row["dan"]["run_id"] == "run9"

    report = tu.analyze(base, "claude-sess-1")
    assert [r["title"] for r in report["rounds"]] == ["fix the login test", "thanks, commit it"]
    assert report["rounds"][0]["calls"] == 4 and report["rounds"][1]["calls"] == 1
    read = next(step for step in report["top_steps"] if step.get("tool") == "Read" and step["added"] > 10_000)
    assert read["burden"] == read["added"] * 4 and "oversized_output" in read["flags"]  # re-read by m2..m5
    flags = {group["flag"]: group["count"] for group in report["flags"]}
    assert flags == {"oversized_output": 1, "failed_call": 1, "duplicate_read": 1}
    stages = {step["detail"]: step["stage"] for step in report["top_steps"] if step.get("tool")}
    assert stages["cd /tmp/demo && pytest tests/test_auth.py -x"] == "verify" and stages["git commit -am fix"] == "vcs"
    second_read = [step for step in report["top_steps"] if step.get("tool") == "Read"][-1]
    assert second_read["stage"] == "debug"  # reading after a failed test
    assert report["by_stage"][0]["key"] in ("understand", "overhead") and abs(sum(row["share"] for row in report["by_stage"]) - 1) < 0.01
    assert report["subagents"][0]["name"] == "Map auth code"
    assert any("over 10k" in tip["title"] for tip in report["advice"])


def test_codex_session_reads_last_token_usage_and_links_children(monkeypatch, tmp_path):
    home = tmp_path / ".codex"; day = home / "sessions/2026/09/21"; day.mkdir(parents=True)
    count = lambda total, inp, cached, out: {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"total_tokens": total}, "last_token_usage": {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out, "reasoning_output_tokens": 5}}}}
    parent = [
        {"type": "session_meta", "payload": {"session_id": "root", "id": "root", "cwd": "/tmp/demo"}},
        {"type": "turn_context", "payload": {"model": "gpt-6-astra"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>x</environment_context>"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "run the tests"}]}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "run the tests"}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": "c1", "name": "exec", "input": 'text(await tools.exec_command({cmd:"pytest -q"}))'}},
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c1", "output": [{"type": "input_text", "text": "Exit code: 1\nfailed"}]}},
        count(5000, 4900, 4000, 100),
        count(5000, 4900, 4000, 100),  # repeated ping
        {"type": "compacted", "payload": {}},
        count(7000, 1900, 0, 100),
    ]
    (day / "rollout-a-root.jsonl").write_text("\n".join(json.dumps(row) for row in parent) + "\n")
    (day / "rollout-b-kid.jsonl").write_text(json.dumps({"type": "session_meta", "payload": {"session_id": "root", "id": "kid", "parent_thread_id": "root", "thread_source": "subagent"}}) + "\n" + json.dumps(count(300, 250, 0, 50)) + "\n")
    _stores(monkeypatch, tmp_path, codex=home); tu._heads.clear()
    base = tmp_path / "graphs"
    listed = tu.list_sessions(base)["sessions"]
    assert [row["session_id"] for row in listed] == ["root"] and listed[0]["subagents"] == 1 and listed[0]["title"] == "run the tests"
    report = tu.analyze(base, "codex-root")
    assert report["totals"]["calls"] == 2 and report["totals"]["cache_read"] == 4000 and report["totals"]["compactions"] == 1
    assert report["totals"]["models"] == ["gpt-6-astra"] and len(report["rounds"]) == 1
    step = next(step for step in report["top_steps"] if step.get("tool") == "exec")
    assert step["detail"] == "pytest -q" and step["stage"] == "verify" and step["error"] and step["burden"] == step["added"]  # compaction ends the burden


def test_classify_applies_confident_labels_and_never_sends_tool_output(monkeypatch, tmp_path):
    home = tmp_path / ".claude"; _claude_session(home); _stores(monkeypatch, tmp_path, claude=home)
    base = tmp_path / "graphs"; tu.list_sessions(base)
    sent = []
    def decide(state, questions):
        sent.append(json.dumps([state, questions]))
        answers = {}
        for key, step in state.items():
            answers[key] = {"choice": "environment", "confidence": 0.9} if step["argument"].startswith("git") else {"choice": "plan", "confidence": 0.1}
        return answers
    report = tu.classify(base, "claude-sess-1", decide=decide)
    stages = {step["detail"]: step["stage"] for step in report["top_steps"] if step.get("tool")}
    assert stages["git commit -am fix"] == "environment"                      # confident answer replaces the rule
    assert stages["cd /tmp/demo && pytest tests/test_auth.py -x"] == "verify"  # low confidence keeps the rule
    assert "xxxx" not in sent[0] and "fix the login test" in sent[0] and set(json.loads(sent[0])[1]["s0"]["criteria"]) == set(tu.STAGES)
    assert tu.analyze(base, "claude-sess-1")["labelled_by"] == "jev-latest"


def test_command_rules():
    assert tu.classify_command('echo "== a ==" && cat notes.txt') == ("read", "understand")
    assert tu.classify_command("cd editor && npm test") == ("execute", "verify")
    assert tu.classify_command("git diff --stat") == ("read", "review")
    assert tu.classify_tool("Write", "/repo/docs/changelog.md") == ("write", "document")
    assert tu.classify_tool("mcp__Claude_Browser__computer", "") == ("execute", "verify")


def test_overview_combines_sessions_by_day_project_agent_and_stage(monkeypatch, tmp_path):
    claude = tmp_path / ".claude"; _claude_session(claude)
    codex = tmp_path / ".codex"; day = codex / "sessions/2026/09/20"; day.mkdir(parents=True)
    (day / "rollout-x.jsonl").write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {"session_id": "cx", "id": "cx", "cwd": "/tmp/other"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "Conversation context (not new instructions):\n[]\n\nCurrent request:\nsummarise the repo\n\nYou are the lead agent."}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"total_tokens": 900}, "last_token_usage": {"input_tokens": 800, "cached_input_tokens": 0, "output_tokens": 100}}}},
    ]) + "\n")
    _stores(monkeypatch, tmp_path, claude=claude, codex=codex); tu._heads.clear()
    report = tu.overview(tmp_path / "graphs")
    assert report["sessions"] == 2 and report["total"] == sum(row["total"] for row in report["top_sessions"])
    assert {row["key"]: row["count"] for row in report["by_agent"]} == {"claude": 1, "codex": 1}
    assert {row["key"] for row in report["by_project"]} == {"demo", "other"}
    assert report["top_sessions"][-1]["title"] == "summarise the repo"  # DAN lead wrapper removed
    assert sum(day["total"] for day in report["days"]) == report["total"]
    assert abs(sum(row["share"] for row in report["by_stage"]) - 1) < 0.01 and report["flags"][0]["flag"] == "oversized_output"
    assert any("over 10k" in tip["title"] for tip in report["advice"])


def test_takeaway_names_the_costliest_stage_and_one_action():
    from dan.native_workers import token_usage
    report = {"by_stage": [{"key": "verify", "weight": 600}, {"key": "understand", "weight": 250}, {"key": "implement", "weight": 150}]}
    result = token_usage.takeaway(report)
    assert result["stage"] == "verify" and result["share"] == 0.6 and result["tokens"] == 600
    assert result["headline"] == "60% of tokens went to verify, then understand (25%)."
    assert "affected tests" in result["action"]
    assert token_usage.takeaway({"by_stage": []}) is None
    assert set(token_usage.STAGE_REMEDY) == set(token_usage.STAGES)      # every stage has exactly one remedy
