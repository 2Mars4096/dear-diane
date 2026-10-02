import json
import os
import tomllib

import pytest

from dan.native_workers import catalog
from dan.native_workers.models import (
    OPENROUTER_URL, ReasoningProvider, dan_model_policy, openrouter_key, source_catalog,
)


@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-router-secret")
    monkeypatch.delenv("DAN_OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path / "graphs"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "account"))
    monkeypatch.setattr(catalog, "binary", lambda runtime: f"/bin/{runtime}")
    monkeypatch.setattr(catalog, "accounts", lambda: {runtime: {"default": {"env": {}}} for runtime in catalog.RUNTIMES})
    return {"provider": "openrouter", "model": "deepseek/deepseek-v4.1-flash", "effort": "medium"}


@pytest.mark.parametrize("session", ["", "prior-session"])
def test_codex_source_is_per_process_and_survives_resume(configured, tmp_path, session):
    original = dict(os.environ)
    args, env = catalog.launch("codex", configured, "work", str(tmp_path), session)
    config = tomllib.loads("\n".join(args[i + 1] for i, part in enumerate(args[:-1]) if part == "-c"))
    assert config["model_provider"] == "dan_openrouter"
    provider = config["model_providers"]["dan_openrouter"]
    assert provider["base_url"] == OPENROUTER_URL and provider["wire_api"] == "responses"
    assert env[provider["env_key"]] == "test-router-secret"
    assert config["model_reasoning_effort"] == "medium"
    assert config["model_supports_reasoning_summaries"] is True
    assert "test-router-secret" not in json.dumps(args)
    assert os.environ == original
    assert not (tmp_path / "config.toml").exists()


def test_claude_gateway_does_not_change_native_login(configured, monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "native-key")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "native-oauth")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setattr("dan.native_workers.skills.build_pool", lambda *args: None)
    original = dict(os.environ)
    args, env = catalog.launch("claude", {**configured, "effort": ""}, "work", str(tmp_path))
    assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "test-router-secret" and env["ANTHROPIC_API_KEY"] == ""
    assert "model_accounts/claude_openrouter" in env["CLAUDE_CONFIG_DIR"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env and "CLAUDE_CODE_USE_BEDROCK" not in env
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == configured["model"]
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == configured["model"]
    assert "test-router-secret" not in json.dumps(args)
    _, native = catalog.launch("claude", {}, "work", str(tmp_path))
    assert native["ANTHROPIC_API_KEY"] == "native-key"
    assert os.environ == original


@pytest.mark.parametrize("runtime,patch,match", [
    ("cursor", {}, "not supported"), ("antigravity", {}, "not supported"),
    ("codex", {"fast": True}, "Fast mode"),
    ("codex", {"model": "gpt-6-astra"}, "full provider/model"),
    ("codex", {"effort": "ultra"}, "reasoning"),
    ("claude", {}, "reasoning control"),
    ("codex", {"provider": "unknown"}, "Unknown model source"),
])
def test_unsupported_combinations_fail_before_spawn(configured, runtime, patch, match):
    with pytest.raises(ValueError, match=match):
        catalog.launch(runtime, {**configured, **patch}, "work", "/tmp")


def test_source_catalog_is_secret_free_and_missing_key_does_not_fallback(configured, monkeypatch):
    assert "test-router-secret" not in json.dumps(source_catalog("codex"))
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setattr("dan.cli.resolve_config", lambda: {"api_key": "wrong-provider-key", "base_url": "https://other.invalid"})
    assert not openrouter_key()
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        dan_model_policy(configured)
    monkeypatch.setattr("dan.cli.resolve_config", lambda: {"api_key": "matching-key", "base_url": OPENROUTER_URL})
    assert openrouter_key() == "matching-key"


@pytest.mark.asyncio
async def test_dan_reasoning_is_scoped_and_forwarded_for_completion_and_stream(configured):
    calls = []
    class Provider:
        supports_tool_calls = True
        async def complete(self, **kwargs):
            calls.append(kwargs)
            return "done"
        async def stream(self, **kwargs):
            calls.append(kwargs)
            yield "chunk"
    profile = dan_model_policy(configured)
    assert profile["base_url"] == OPENROUTER_URL and profile["reasoning_effort"] == "medium"
    provider = Provider()
    wrapped = ReasoningProvider(provider, profile["reasoning_effort"])
    assert wrapped.supports_tool_calls
    extra = {"provider": {"sort": "price"}}
    assert await wrapped.complete(model=configured["model"], extra_body=extra) == "done"
    assert [chunk async for chunk in wrapped.stream(model=configured["model"])] == ["chunk"]
    await provider.complete(model=configured["model"])
    assert all(call["extra_body"]["reasoning"] == {"effort": "medium"} for call in calls[:2])
    assert "reasoning" not in extra and "extra_body" not in calls[-1]


@pytest.mark.asyncio
async def test_dan_team_forwards_the_same_model_choice(configured, monkeypatch, tmp_path):
    from dan.native_workers.service import NativeTeam
    from dan.server.chat_v2_backend import AgentBackendRunResult
    calls = []
    async def run(self, request, emit):
        calls.append(request)
        return AgentBackendRunResult(status="completed", backend="super_dan", summary="done")
    monkeypatch.setattr("dan.server.chat_v2_backend.SuperDanBackendAdapter._run", run)
    team = NativeTeam("parent", str(tmp_path), {"dan": {**configured, "enabled": True}}, tmp_path / "workers")
    worker = await team.start("dan", "work")
    await team.tasks[worker["worker_id"]]
    assert calls[0].profile_policy == {**dan_model_policy(configured), "native_workers": {}}


@pytest.mark.parametrize('source,model', [('openai', 'gpt-6-astra'), ('deepseek', 'deepseek-flash'), ('moonshot', 'kimi-k3')])
def test_direct_provider_codex_route(configured, monkeypatch, tmp_path, source, model):
    from dan.native_workers.models import API_PROVIDERS
    spec = API_PROVIDERS[source]
    monkeypatch.setenv(spec['env'], 'direct-test-secret')
    args, env = catalog.launch('codex', {'provider': source, 'model': model, 'effort': 'high'}, 'work', str(tmp_path))
    config = tomllib.loads('\n'.join(args[i + 1] for i, part in enumerate(args[:-1]) if part == '-c'))
    provider = config['model_providers'][f'dan_{source}']
    assert provider['base_url'] == spec['url']
    assert env[provider['env_key']] == 'direct-test-secret'
    assert 'direct-test-secret' not in json.dumps(args)


@pytest.mark.parametrize('source,model', [('deepseek', 'deepseek-flash'), ('moonshot', 'kimi-k3')])
def test_direct_claude_route(configured, monkeypatch, tmp_path, source, model):
    from dan.native_workers.models import API_PROVIDERS
    spec = API_PROVIDERS[source]
    monkeypatch.setenv(spec['env'], 'direct-test-secret')
    monkeypatch.setenv('ANTHROPIC_SMALL_FAST_MODEL', 'unrelated-model')
    monkeypatch.setenv('CLAUDE_CODE_EFFORT_LEVEL', 'low')
    monkeypatch.setattr('dan.native_workers.skills.build_pool', lambda *args: None)
    args, env = catalog.launch('claude', {'provider': source, 'model': model, 'effort': 'high'}, 'work', str(tmp_path))
    assert env['ANTHROPIC_BASE_URL'] == spec['anthropic_url']
    assert env['ANTHROPIC_AUTH_TOKEN'] == 'direct-test-secret'
    assert env['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == model
    assert 'ANTHROPIC_SMALL_FAST_MODEL' not in env
    assert 'CLAUDE_CODE_EFFORT_LEVEL' not in env
    assert args[args.index('--effort') + 1] == 'high'
    assert 'direct-test-secret' not in json.dumps(args)


def test_saved_credentials_private_redacted_and_removable(configured, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dan.server.routers.native_workers import router
    from dan.native_workers.models import provider_key
    from dan.native_workers.provider_credentials import _path
    monkeypatch.delenv('DAN_REMOTE_CONFIG', raising=False)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'environment-fallback')
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.put('/api/model-providers/deepseek/key', json={'api_key': 'saved-test-secret'})
        assert response.status_code == 200
        assert 'saved-test-secret' not in response.text
        assert provider_key('deepseek') == 'saved-test-secret'
        assert _path().stat().st_mode & 0o777 == 0o600
        assert 'saved-test-secret' not in client.get('/api/model-providers').text
        assert 'saved-test-secret' not in json.dumps(source_catalog('codex'))
        assert client.put('/api/model-providers/deepseek/key', json={'api_key': ''}).status_code == 200
        assert provider_key('deepseek') == 'environment-fallback'
        assert client.put('/api/model-providers/unknown/key', json={'api_key': 'x'}).status_code == 400
        assert client.put('/api/model-providers/deepseek/key', json={'api_key': 'x'}, headers={'origin': 'https://evil.example'}).status_code == 403
        monkeypatch.setenv('DAN_REMOTE_CONFIG', 'remote')
        assert client.get('/api/model-providers').status_code == 403


def test_direct_reasoning_options_and_compatibility(configured, monkeypatch):
    from dan.native_workers.models import validate_model_source
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    policy = dan_model_policy({'provider': 'deepseek', 'model': 'deepseek-flash', 'effort': 'max'})
    assert policy['base_url'] == 'https://api.deepseek.com'
    assert ReasoningProvider(None, 'max', 'deepseek').options({}) == {'reasoning_effort': 'max'}
    with pytest.raises(ValueError, match='not supported'):
        validate_model_source('claude', {'provider': 'openai', 'model': 'gpt-6-astra'})
    with pytest.raises(ValueError, match='GPT-4.1'):
        dan_model_policy({'provider': 'openai', 'model': 'gpt-6-astra'})
