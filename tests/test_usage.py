import json
from pathlib import Path

from dan.native_workers import usage


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
