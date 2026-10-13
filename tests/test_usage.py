import json
from pathlib import Path

from diane.native_workers import usage


import pytest


@pytest.fixture(autouse=True)
def isolated_usage(monkeypatch, tmp_path):
    from diane.native_workers import models
    monkeypatch.setattr(models, "provider_key", lambda source: "")
    monkeypatch.setattr(usage, "user_home", lambda: tmp_path)
    usage._codex_cache.clear()
    usage._claude_cache.clear()


def test_codex_usage_queries_exact_home_and_caches(monkeypatch, tmp_path):
    calls = []
    async def snapshot(home):
        calls.append(home)
        return {"rateLimits": {"primary": {"usedPercent": 10, "windowDurationMins": 10080, "resetsAt": 9}, "secondary": {"usedPercent": 42, "windowDurationMins": 300, "resetsAt": 7}, "planType": "pro"}}
    monkeypatch.setattr(usage, "_codex_snapshot", snapshot)
    report = usage.codex_usage(tmp_path)
    assert report["plan"] == "pro"
    assert [(w["label"], w["used_percent"]) for w in report["windows"]] == [("5h", 42.0), ("week", 10.0)]
    assert usage.codex_usage(tmp_path) == report
    assert calls == [tmp_path]
    (tmp_path / "auth.json").write_text("{}")
    usage.codex_usage(tmp_path)
    assert len(calls) == 2


def test_codex_failure_does_not_fall_back_to_shared_transcripts(monkeypatch, tmp_path):
    async def fail(home):
        raise ValueError("secret upstream error")
    monkeypatch.setattr(usage, "_codex_snapshot", fail)
    result = usage.codex_usage(tmp_path)
    assert result == {"windows": [], "error": "Account quota unavailable (ValueError)"}


def test_codex_multiple_buckets_preserve_names():
    result = usage._codex_windows({"rateLimitsByLimitId": {
        "codex": {"primary": {"usedPercent": 7, "windowDurationMins": 10080}},
        "other": {"limitName": "Fast", "primary": {"usedPercent": 30, "windowDurationMins": 60}}}})
    assert [w["label"] for w in result["windows"]] == ["Fast · 1h", "week"]


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
    monkeypatch.setattr(usage, "codex_usage", lambda home: {"windows": []})
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


def test_shared_sessions_do_not_merge_distinct_logins(monkeypatch, tmp_path):
    cli = tmp_path / ".codex"; cli.mkdir()
    sessions = cli / "sessions"; sessions.mkdir()
    app = tmp_path / "app"; app.mkdir(); (app / "sessions").symlink_to(sessions)
    monkeypatch.setattr(usage, "accounts", lambda: {"codex": {
        "default": {"env": {"CODEX_HOME": str(app)}},
        "app": {"env": {"CODEX_HOME": str(app)}}}})
    monkeypatch.setattr(usage, "codex_usage", lambda home: {"windows": [], "plan": "pro" if home == cli else "prolite"})
    rows = usage.usage_report(tmp_path)["accounts"]
    assert len(rows) == 2
    assert rows[0]["aliases"] == ["Default profile", "app"]
    assert rows[1]["label"] == "Native CLI"
    assert [r["plan"] for r in rows] == ["prolite", "pro"]


def test_all_accounts_and_api_providers_are_included(monkeypatch, tmp_path):
    from diane.native_workers import models
    monkeypatch.setattr(usage, "accounts", lambda: {"claude": {name: {"env": {"CLAUDE_CONFIG_DIR": str(tmp_path / name)}} for name in ["one", "two", "three"]}})
    monkeypatch.setattr(usage, "claude_usage", lambda *args: {"windows": []})
    monkeypatch.setattr(models, "provider_key", lambda source: "hidden-key" if source in ["openrouter", "openai"] else "")
    monkeypatch.setattr(usage, "openrouter_usage", lambda key: {"windows": [], "spend": {"usage_daily": 2}})
    rows = usage.usage_report(tmp_path)["accounts"]
    assert [r["backend"] for r in rows] == ["claude", "claude", "claude", "openrouter", "openai"]
    assert "hidden-key" not in json.dumps(rows)


def test_openrouter_returns_only_usage_fields(monkeypatch):
    import io
    monkeypatch.setattr(usage, "urlopen", lambda *a, **k: io.BytesIO(json.dumps({"data": {"label": "secret-label", "usage_daily": 1.2, "limit_remaining": 8, "limit": 10}}).encode()))
    result = usage.openrouter_usage("secret-key")
    assert result["spend"]["limit_remaining"] == 8
    assert "secret" not in json.dumps(result)


def test_one_broken_account_does_not_hide_other_accounts(monkeypatch, tmp_path):
    monkeypatch.setattr(usage, "accounts", lambda: {"claude": {name: {"env": {"CLAUDE_CONFIG_DIR": str(tmp_path / name)}} for name in ["bad", "good"]}})
    def read(account, env):
        if account == "bad":
            raise ValueError("secret response")
        return {"windows": [{"label": "week", "used_percent": 5}]}
    monkeypatch.setattr(usage, "claude_usage", read)
    rows = usage.usage_report(tmp_path)["accounts"]
    assert rows[0]["error"] == "Usage unavailable (ValueError)"
    assert rows[1]["windows"][0]["used_percent"] == 5
    assert "secret" not in json.dumps(rows)
