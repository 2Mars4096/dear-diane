import asyncio
import io
import json
import wave
from decimal import Decimal

import httpx
import pytest

from dan.personal.store import PersonalStore, Conflict
from dan.personal.voice import Voice, SpeechInput, validate_audio, PROFILES
from dan.personal.conversation import Conversation, ChatInput, brief_for
from dan.personal.budgets import status


def wav(seconds=1, channels=1):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(channels); audio.setsampwidth(2); audio.setframerate(16000)
        audio.writeframes(b'\0\0' * 16000 * seconds * channels)
    return output.getvalue()


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv('DAN_PERSONAL_VOICE', '1')
    monkeypatch.setattr('dan.cli.resolve_config', lambda: {'base_url': 'https://openrouter.ai/api/v1', 'api_key': 'fixture'})
    return Voice(PersonalStore(tmp_path/'personal'))


@pytest.mark.parametrize('data', [b'bad', wav(31), wav(1, 2), wav()[:-2]])
def test_reject_invalid_or_oversized_recordings(data):
    with pytest.raises(ValueError): validate_audio(data)


def test_transcription_replay_and_changed_input_never_repeat_paid_call(service, monkeypatch):
    calls = []
    async def call(*args): calls.append(args); return b'{"text":"Please remember my meeting"}'
    monkeypatch.setattr(service, 'checked_call', call)
    result = asyncio.run(service.transcribe('operator', 'audio-request-1', wav()))
    assert asyncio.run(service.transcribe('operator', 'audio-request-1', wav())) == result
    assert len(calls) == 1
    with pytest.raises(Conflict): asyncio.run(service.transcribe('operator', 'audio-request-1', wav(2)))
    assert status(service.store, 'operator')['used_reservations_usd'] == '0.005'
    with service.store.connection() as db:
        saved = db.execute('SELECT body FROM voice_operations').fetchone()[0]
    assert 'input_audio' not in saved and 'Please remember' in saved


def test_uncertain_audio_is_not_retried_or_refunded(service, monkeypatch):
    async def fail(*args): raise ValueError('timeout')
    monkeypatch.setattr(service, 'checked_call', fail)
    with pytest.raises(ValueError): asyncio.run(service.transcribe('operator', 'audio-request-1', wav()))
    with pytest.raises(Conflict): asyncio.run(service.transcribe('operator', 'audio-request-1', wav()))
    assert status(service.store, 'operator')['used_reservations_usd'] == '0.005'


def test_audio_obeys_shared_pause_limits_and_rollover(service, monkeypatch):
    service.reserve('operator', 'audio-request-1', {'kind': 'speech'}, Decimal('0.05'))
    monkeypatch.setenv('DAN_PERSONAL_DAILY_USD', '0.04')
    with pytest.raises(Conflict): service.authorize('operator', 'audio-request-1')
    monkeypatch.setenv('DAN_PERSONAL_DAILY_USD', '2')
    monkeypatch.setattr('dan.personal.voice.now', lambda: '2099-01-01T00:00:00Z')
    service.authorize('operator', 'audio-request-1')
    with service.store.connection() as db:
        saved = json.loads(db.execute('SELECT body FROM voice_operations').fetchone()[0])
    assert '2099-01-01' in saved['budget_days']


def test_speech_only_reads_owned_completed_receipt_and_uses_fixed_voice(service, monkeypatch):
    chat = Conversation(service.store)
    turn = chat.submit('operator', ChatInput(operation_id='chat-request-1', text='Hello', voice_profile='warm'), 'fixture/model')
    body = SpeechInput(operation_id='speech-request-1', turn_id=turn['id'], profile='composed')
    with pytest.raises(ValueError): asyncio.run(service.speak('operator', body))
    job = chat.claim(); chat.finish(job, {'reply': 'Hello. How can I help?'})
    with pytest.raises(ValueError): asyncio.run(service.speak('other', body))
    calls = []
    async def call(*args): calls.append(args); return b'ID3synthetic-audio'
    monkeypatch.setattr(service, 'checked_call', call)
    result = asyncio.run(service.speak('operator', body))
    assert result['format'] == 'mp3'
    assert calls[0][3] == 'hexgrad/kokoro-82m'
    assert calls[0][4]['voice'] == 'bm_george'
    assert calls[0][4]['input'] == 'Hello. How can I help?'
    with pytest.raises(Conflict): asyncio.run(service.speak('operator', body))
    assert len(calls) == 1
    assert [preset['gender'] for preset in PROFILES.values()].count('female') == 2


def test_voice_context_preserves_preferences_and_interruption(service):
    chat = Conversation(service.store)
    first = chat.submit('operator', ChatInput(operation_id='chat-request-1', text='Budget HKD 1000'), 'fixture/model')
    job = chat.claim(); chat.finish(job, {'reply': 'Here are some ideas.'})
    second = chat.submit('operator', ChatInput(operation_id='chat-request-2', text='Somewhere cozier', voice_profile='composed', interrupted_turn_id=first['id']), 'fixture/model')
    brief = brief_for(second, chat.history('operator'), [])
    assert 'Budget HKD 1000' in brief.evidence[0].content
    assert first['id'] in brief.evidence[0].content
    assert 'composed' in brief.evidence[0].content


def test_catalog_price_check_happens_before_audio_upload(service, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={'data': {'endpoints': [{'pricing': {'prompt': '1', 'completion': '0'}}]}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr('dan.personal.voice.httpx.AsyncClient', lambda **kwargs: client)
    with pytest.raises(Conflict): asyncio.run(service.transcribe('operator', 'audio-request-1', wav()))
    assert calls == ['GET']


def test_schema_six_backup_preserves_conversations(service):
    chat = Conversation(service.store)
    turn = chat.submit('operator', ChatInput(operation_id='chat-request-1', text='Hello'), 'fixture/model')
    with service.store.connection() as db:
        db.execute('DROP TABLE voice_operations'); db.execute('PRAGMA user_version=6')
    migrated = PersonalStore(service.store.directory)
    assert Conversation(migrated).history('operator')[0]['id'] == turn['id']
    backup = next(service.store.directory.glob('state.v6.*.backup.sqlite3'))
    assert backup.stat().st_mode & 0o777 == 0o600


def test_voice_api_disabled_and_oversized_upload(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dan.server.routers.personal import router
    monkeypatch.setenv('DAN_GRAPHS_DIR', str(tmp_path))
    monkeypatch.setenv('DAN_PERSONAL_ENABLED', '1')
    monkeypatch.delenv('DAN_PERSONAL_VOICE', raising=False)
    app = FastAPI(); app.include_router(router); client = TestClient(app)
    response = client.get('/api/personal/voice')
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['enabled'] is False
    assert client.post('/api/personal/voice/transcribe?operation_id=request-1', content=wav()).status_code == 409
    assert client.post('/api/personal/voice/transcribe?operation_id=request-2', content=b'0' * 960045).status_code == 413
    monkeypatch.setenv('DAN_PERSONAL_ENABLED', '0')
    assert client.get('/api/personal/voice').status_code == 404
