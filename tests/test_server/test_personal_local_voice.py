import asyncio
import io
import json
from types import SimpleNamespace
import wave

from dan.personal import local_voice


def wav():
    out = io.BytesIO()
    with wave.open(out, 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
        audio.writeframes(b'\0\0' * 16000)
    return out.getvalue()


def test_local_captions_emit_cumulative_bilingual_tokens(monkeypatch):
    def generate(audio, **options):
        assert 'language' not in options
        for text in ['Hello', '，', '你好']:
            yield SimpleNamespace(text=text)
    monkeypatch.setattr(local_voice, '_load', lambda: SimpleNamespace(stream_transcribe=generate))
    async def read():
        return [json.loads(line) async for line in local_voice.stream(wav())]
    results = asyncio.run(read())
    assert [r['text'] for r in results] == ['Hello', 'Hello，', 'Hello，你好', 'Hello，你好']
    assert results[-1]['done'] is True
    assert not local_voice._busy.locked()


def test_local_failure_is_safe_and_releases_worker(monkeypatch):
    def fail(): raise RuntimeError('private diagnostic')
    monkeypatch.setattr(local_voice, '_load', fail)
    async def read(): return [json.loads(line) async for line in local_voice.stream(wav())]
    assert asyncio.run(read()) == [{'error': 'Local captions unavailable'}]
    assert not local_voice._busy.locked()


def test_busy_local_worker_does_not_queue_more_audio():
    assert local_voice._busy.acquire(blocking=False)
    try:
        async def read(): return [json.loads(line) async for line in local_voice.stream(wav())]
        assert asyncio.run(read()) == [{'error': 'Local captions are busy'}]
    finally:
        local_voice._busy.release()
