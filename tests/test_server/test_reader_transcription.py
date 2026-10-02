import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from dan.server import reader_transcription as t

IMAGE = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()


@pytest.mark.asyncio
async def test_vision_payload_and_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(t, 'resolve_graphs_dir', lambda: str(tmp_path))
    monkeypatch.setattr(t, 'provider_key', lambda _: 'test-only')
    provider = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(text='{"text":"想象中的美国。 α≤β ²\\u200b"}')), close=AsyncMock())
    monkeypatch.setattr(t, 'OpenAIProvider', lambda _: provider)
    result = await t.transcribe(IMAGE, 'garbled text')
    assert result == {'text': '想象中的美国。 α≤β ²', 'model': t.MODELS[0]}
    payload = provider.complete.call_args.kwargs
    assert payload['messages'][1]['content'][0]['image_url']['url'] == IMAGE
    assert 'garbled text' in payload['messages'][1]['content'][1]['text']
    assert 'deepseek' not in payload['model']
    assert await t.transcribe(IMAGE, 'garbled text') == result
    assert provider.complete.call_count == 1
    provider.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_and_failure_do_not_save_bad_quotes(tmp_path, monkeypatch):
    monkeypatch.setattr(t, 'resolve_graphs_dir', lambda: str(tmp_path))
    monkeypatch.setattr(t, 'provider_key', lambda _: 'test-only')
    provider = SimpleNamespace(complete=AsyncMock(side_effect=[ValueError('bad response'), SimpleNamespace(text='{"text":"真实原文"}')]), close=AsyncMock())
    monkeypatch.setattr(t, 'OpenAIProvider', lambda _: provider)
    assert (await t.transcribe(IMAGE, 'hint'))['model'] == t.MODELS[1]
    provider.complete = AsyncMock(return_value=SimpleNamespace(text='{"text":""}'))
    with pytest.raises(RuntimeError, match='Could not transcribe'):
        await t.transcribe(IMAGE, 'different hint')
    assert len(list((tmp_path / 'reader_transcriptions').glob('*.json'))) == 1


@pytest.mark.asyncio
async def test_rejects_external_images():
    with pytest.raises(ValueError, match='PNG'):
        await t.transcribe('https://example.com/picture.png', '')


def test_unicode_cleanup_preserves_printed_symbols():
    assert t.clean_transcription('\ufeff  α≤β ² ﬁ e\u0301 中文，。\u200b ') == 'α≤β ² ﬁ é 中文，。'
