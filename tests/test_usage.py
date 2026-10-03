import json
from pathlib import Path

from diane.native_workers import usage


def test_codex_usage_reads_latest_rate_limits(tmp_path):
    home = tmp_path / ".codex"
    day = home / "sessions/2026/09/21"; day.mkdir(parents=True)
    old = day / "rollout-old.jsonl"; new = day / "rollout-new.jsonl"
    old.write_text(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {"primary": {"used_percent": 90, "window_minutes": 10080, "resets_at": 5}}}}) + "\n")
    new.write_text("\n".join([
        json.dumps({"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {"primary": {"used_percent": 10, "window_minutes": 10080, "resets_at": 9}, "secondary": {"used_percent": 42, "window_minutes": 300, "resets_at": 7}, "plan_type": "pro"}}}),
        json.dumps({"type": "response_item", "payload": {"type": "message"}}),
    ]) + "\n")
    import os; os.utime(old, (1, 1)); os.utime(new, (2, 2))
    report = usage.codex_usage(home)
    assert report["plan"] == "pro"
    assert [(w["label"], w["used_percent"]) for w in report["windows"]] == [("5h", 42.0), ("week", 10.0)]
    assert usage.codex_usage(tmp_path / "none")["error"]


def test_recent_accounts_orders_by_latest_record(tmp_path):
    import os
    leads = tmp_path / "native_leads"; leads.mkdir()
    for name, backend, account, stamp in [("a", "codex", "ph", 10), ("b", "claude", "default", 30), ("c", "codex", "ph", 20), ("d", "cursor", "default", 5)]:
        path = leads / f"{name}.json"; path.write_text(json.dumps({"backend": backend, "profile": {"account": account}})); os.utime(path, (stamp, stamp))
    assert usage.recent_accounts(tmp_path, 2) == [("claude", "default"), ("codex", "ph")]


def test_usage_report_never_exposes_tokens(monkeypatch, tmp_path):
    monkeypatch.setattr(usage, "accounts", lambda: {"codex": {"default": {"label": "Codex", "env": {"CODEX_HOME": str(tmp_path)}}}, "claude": {"default": {"label": "Claude", "env": {}}}})
    monkeypatch.setattr(usage, "_claude_token", lambda env: "secret-token")
    monkeypatch.setattr(usage, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    usage._claude_cache.clear()
    report = usage.usage_report(tmp_path)
    assert "secret-token" not in json.dumps(report)
    assert [row["backend"] for row in report["accounts"]] == ["codex", "claude"]
    assert "offline" not in json.dumps(report) and report["accounts"][1]["error"].startswith("Usage unavailable")


def test_claude_limits_include_model_scoped_window():
    data = {"five_hour": {"utilization": 1.0}, "limits": [
        {"kind": "session", "percent": 17, "resets_at": "2026-09-21T13:10:00+00:00"},
        {"kind": "weekly_all", "percent": 15, "resets_at": "2026-09-28T01:00:00+00:00"},
        {"kind": "weekly_scoped", "percent": 24, "resets_at": "2026-09-28T01:00:00+00:00", "scope": {"model": {"display_name": "Fable"}}}]}
    windows = usage._claude_windows(data)
    assert [(w["label"], w["used_percent"]) for w in windows] == [("5h", 17.0), ("week", 15.0), ("fable", 24.0)]
    assert usage._claude_windows({"five_hour": {"utilization": 5}, "seven_day": {"utilization": 6}})[1]["label"] == "week"


def test_codex_usage_is_labelled_with_codexx_project_account(monkeypatch, tmp_path):
    home = tmp_path / ".codex"; day = home / "sessions/2026/09/21"; day.mkdir(parents=True)
    (day / "rollout.jsonl").write_text("\n".join([
        json.dumps({"type": "session_meta", "payload": {"cwd": "/work/project/sub"}}),
        json.dumps({"payload": {"rate_limits": {"primary": {"used_percent": 2, "window_minutes": 10080, "resets_at": 9}}}})]) + "\n")
    config = tmp_path / "codexx"; config.mkdir()
    (config / "state.json").write_text(json.dumps({"projects": {"/work/project": {"last_account": "mine"}}}))
    monkeypatch.setenv("DAN_CODEXX_CONFIG", str(config / "config.toml"))
    shared = tmp_path / "accounts/ph/.codex"; shared.mkdir(parents=True); (shared / "sessions").symlink_to(home / "sessions")
    monkeypatch.setattr(usage, "accounts", lambda: {"codex": {"app": {"label": "app", "env": {"CODEX_HOME": str(home)}}, "ph": {"label": "ph", "env": {"CODEX_HOME": str(shared)}}}})
    monkeypatch.setattr(usage, "recent_accounts", lambda base, limit=2: [("codex", "app"), ("codex", "ph")])
    rows = usage.usage_report(tmp_path)["accounts"]
    assert [(row["backend"], row["account"]) for row in rows] == [("codex", "mine")]   # one shared store, labelled by codexx
