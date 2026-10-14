import json
import pytest
from diane.native_workers.context_window import codex_context_limit, claude_context_env


def test_codex_uses_each_models_own_ceiling(tmp_path):
    (tmp_path / 'models_cache.json').write_text(json.dumps({'models': [
        {'slug': 'large', 'max_context_window': 872000, 'context_window': 272000},
        {'slug': 'small', 'max_context_window': 272000},
        {'slug': 'default-only', 'context_window': 128000},
        {'slug': 'invalid', 'max_context_window': '1000000'},
    ]}))
    assert codex_context_limit(str(tmp_path), 'large') == 872000
    assert codex_context_limit(str(tmp_path), 'small') == 272000
    assert codex_context_limit(str(tmp_path), 'default-only') == 128000
    assert codex_context_limit(str(tmp_path), 'invalid') is None
    assert codex_context_limit(str(tmp_path), 'unknown') is None
    (tmp_path / 'models_cache.json').write_text('invalid')
    assert codex_context_limit(str(tmp_path), 'large') is None


@pytest.mark.parametrize('model', ['claude-opus-5', 'claude-sonnet-5', 'claude-fable-5-1'])
def test_claude_native_million_models(model):
    assert claude_context_env(model)['CLAUDE_CODE_AUTO_COMPACT_WINDOW'] == '1000000'


@pytest.mark.parametrize('model', ['claude-haiku-4-5-20251001', '', 'opus', 'gateway/custom', 'claude-sonnet-4-6'])
def test_no_unverified_context_or_paid_variant_switch(model):
    assert claude_context_env(model) == {}
