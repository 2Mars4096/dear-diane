"""Bounded OpenRouter audio transport; personal services still own all actions.

Microphone bytes are transient. Persist intent and a conservative reservation before
each paid call, never automatically retry uncertain requests. Audio prices are
catalog-checked estimates, not an upstream-enforced invoice ceiling.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import time
import wave
from decimal import Decimal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from . import budgets
from .store import Conflict, canonical, now

STT_MODEL = 'qwen/qwen3-asr-1.7b'
MAX_AUDIO_BYTES = 960044  # 30 seconds, mono PCM16 at 16 kHz, plus WAV header.
MAX_SPEECH_CHARS = 2000
_PRICE_CHECKS: dict[str, tuple[float, Decimal]] = {}
PROFILES = {
    'warm': {'name': 'Warm', 'description': 'Soft and conversational', 'gender': 'female', 'model': 'qwen/qwen-audio-3.0-tts-flash', 'voice': 'longanfengyue', 'rate': 0.96},
    'bright': {'name': 'Bright', 'description': 'Lively and expressive', 'gender': 'female', 'model': 'qwen/qwen-audio-3.0-tts-flash', 'voice': 'longanlingxi', 'rate': 1.04},
    'steady': {'name': 'Steady', 'description': 'Measured and restrained', 'gender': 'male', 'model': 'qwen/qwen-audio-3.0-tts-flash', 'voice': 'loongjohn', 'rate': 0.88},
    'composed': {'name': 'Composed', 'description': 'Calm British English', 'gender': 'male', 'model': 'hexgrad/kokoro-82m', 'voice': 'bm_george', 'rate': 1.0},
}


def config():
    from dan.cli import resolve_config
    value = resolve_config()
    if os.environ.get('DAN_PERSONAL_VOICE', '').lower() not in {'1', 'true', 'yes'}:
        raise Conflict('Voice is not enabled on this Mac.')
    if value['base_url'].rstrip('/') != 'https://openrouter.ai/api/v1' or not value['api_key']:
        raise Conflict('Voice needs an OpenRouter connection on this Mac.')
    return value


def capabilities():
    try:
        config(); enabled = True
    except Conflict:
        enabled = False
    return {'enabled': enabled, 'local_captions': os.environ.get('DAN_PERSONAL_LOCAL_VOICE') == '1', 'profiles': [{'id': key, **{field: value[field] for field in ('name', 'description', 'gender')}} for key, value in PROFILES.items()]}


class SpeechInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(min_length=8, max_length=100)
    turn_id: str = Field(min_length=1, max_length=100)
    profile: str = Field(max_length=20)


def validate_audio(data):
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError('Speak in segments of up to 30 seconds.')
    try:
        with wave.open(io.BytesIO(data), 'rb') as audio:
            frames = audio.getnframes()
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, 'NONE'):
                raise ValueError('Voice requires mono 16 kHz PCM audio.')
            if not 1600 <= frames <= 480000 or len(audio.readframes(frames)) != frames * 2:
                raise ValueError('Voice recording is empty, incomplete or too long.')
    except (wave.Error, EOFError):
        raise ValueError('Voice recording could not be read.') from None


class Voice:
    def __init__(self, store):
        self.store = store

    def reserve(self, owner, identity, payload, cost):
        digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
        with self.store.connection() as db:
            previous = db.execute('SELECT hash,state,body FROM voice_operations WHERE owner=? AND id=?', (owner, identity)).fetchone()
            if previous:
                if previous['hash'] != digest:
                    raise Conflict('This voice operation was already used for different input.')
                saved = json.loads(previous['body'])
                if previous['state'] == 'completed' and 'text' in saved:
                    return saved['text']
                raise Conflict('This audio request was already attempted. Start a new voice turn.')
            policy = budgets.policy(db, owner)
            used = budgets.daily_reserved(db, owner, now()[:10])
            if policy['paused'] or used is None or cost > Decimal(policy['task_limit']) or used + cost > Decimal(policy['daily_limit']):
                raise Conflict('Voice is paused by your AI spending limits.')
            saved = {'created_at': now(), 'billing': [], 'budget': {'reserved_usd': str(cost), 'basis': 'audio_catalog_estimate'}, 'kind': payload['kind']}
            db.execute("INSERT INTO voice_operations VALUES (?,?,?,'attempted',?)", (owner, identity, digest, canonical(saved)))
        return None

    def finish(self, owner, identity, text=None):
        with self.store.connection() as db:
            row = db.execute('SELECT body FROM voice_operations WHERE owner=? AND id=?', (owner, identity)).fetchone()
            saved = json.loads(row[0])
            if text is not None:
                saved['text'] = text
            db.execute("UPDATE voice_operations SET state='completed',body=? WHERE owner=? AND id=?", (canonical(saved), owner, identity))

    def authorize(self, owner, identity):
        with self.store.connection() as db:
            row = db.execute('SELECT body FROM voice_operations WHERE owner=? AND id=?', (owner, identity)).fetchone()
            saved = json.loads(row[0]); day = now()[:10]
            days = saved.get('budget_days', [saved['created_at'][:10]])
            cost = Decimal(saved['budget']['reserved_usd'])
            policy = budgets.policy(db, owner); used = budgets.daily_reserved(db, owner, day)
            if policy['paused'] or used is None or cost > Decimal(policy['task_limit']) or used + (cost if day not in days else 0) > Decimal(policy['daily_limit']):
                raise Conflict('Voice is paused by your AI spending limits.')
            if day not in days:
                saved['budget_days'] = [*days, day]
                db.execute('UPDATE voice_operations SET body=? WHERE owner=? AND id=?', (canonical(saved), owner, identity))

    async def checked_call(self, owner, identity, path, model, payload, ceiling):
        settings = config()
        from .billing import Receipts
        receipts = Receipts(self.store, 'voice_operations', owner, identity)
        attempt = None
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # Audio endpoints do not document chat's enforced max_price controls.
                # Refuse unsupported/expensive catalog entries before sending audio.
                cached = _PRICE_CHECKS.get(model)
                if not cached or time.monotonic() - cached[0] >= 60 or cached[1] > ceiling:
                    catalog = await client.get(settings['base_url'] + '/models/' + model + '/endpoints', timeout=10)
                    catalog.raise_for_status()
                    endpoints = catalog.json()['data']['endpoints']
                    if not endpoints or any(not Decimal(str(e['pricing']['prompt'])).is_finite() or not 0 <= Decimal(str(e['pricing']['prompt'])) <= ceiling or Decimal(str(e['pricing']['completion'])) != 0 for e in endpoints):
                        raise Conflict('Audio pricing changed. Voice is paused until its configuration is reviewed.')
                    _PRICE_CHECKS[model] = (time.monotonic(), max(Decimal(str(e['pricing']['prompt'])) for e in endpoints))
                self.authorize(owner, identity)
                with self.store.connection() as db:
                    saved = json.loads(db.execute('SELECT body FROM voice_operations WHERE owner=? AND id=?', (owner, identity)).fetchone()[0])
                attempt = receipts.begin(saved['budget']['reserved_usd'])
                async with client.stream('POST', settings['base_url'] + path, headers={'Authorization': 'Bearer ' + settings['api_key']}, json={'model': model, **payload}) as response:
                    receipts.record(attempt, {'generation_id': response.headers.get('x-generation-id')})
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 4 * 1024 * 1024:
                            raise ValueError('The audio response was too large.')
                    if path == '/audio/transcriptions':
                        try:
                            receipts.record(attempt, {'cost_usd': json.loads(data).get('usage', {}).get('cost')})
                        except (ValueError, TypeError, AttributeError):
                            pass
                    return bytes(data)
        except httpx.TimeoutException:
            raise ValueError('The voice service timed out. You can keep typing.') from None
        except (httpx.HTTPError, KeyError, ArithmeticError):
            raise ValueError('The voice service is unavailable. You can keep typing.') from None
        finally:
            receipts.change(lambda body: body.update(billing_closed=True))

    async def transcribe(self, owner, identity, data):
        config(); validate_audio(data)
        previous = self.reserve(owner, identity, {'kind': 'transcription', 'sha256': hashlib.sha256(data).hexdigest()}, Decimal('0.005'))
        if previous is not None:
            return {'text': previous}
        raw = await self.checked_call(owner, identity, '/audio/transcriptions', STT_MODEL, {'input_audio': {'format': 'wav', 'data': base64.b64encode(data).decode()}}, Decimal('0.0001'))
        try:
            text = json.loads(raw)['text'].strip()
            if not isinstance(text, str) or len(text) > 16000:
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ValueError('The voice transcript could not be read.') from None
        self.finish(owner, identity, text)
        return {'text': text}

    async def speak(self, owner, body):
        config()
        if body.profile not in PROFILES:
            raise ValueError('Choose one of the available voices.')
        with self.store.connection() as db:
            row = db.execute("SELECT body FROM conversation_jobs WHERE owner=? AND id=? AND state='completed'", (owner, body.turn_id)).fetchone()
        if not row:
            raise ValueError('That reply is not ready to speak.')
        turn = json.loads(row[0])
        spoken = turn.get('reply', '')
        for source in turn.get('references', []):
            spoken = spoken.replace('[' + source['id'] + ']', '')
        text = spoken[:MAX_SPEECH_CHARS].strip()
        if not text.strip():
            raise ValueError('There is no reply to speak.')
        preset = PROFILES[body.profile]
        self.reserve(owner, body.operation_id, {'kind': 'speech', 'turn': body.turn_id, 'profile': body.profile, 'text': text}, Decimal('0.05'))
        data = await self.checked_call(owner, body.operation_id, '/audio/speech', preset['model'], {'input': text, 'voice': preset['voice'], 'response_format': 'mp3'}, Decimal('0.000025'))
        if not data or not (data.startswith(b'ID3') or data[0] == 255):
            raise ValueError('The voice service returned unreadable audio.')
        self.finish(owner, body.operation_id)
        return {'audio': base64.b64encode(data).decode(), 'rate': preset['rate'], 'format': 'mp3', 'truncated': len(spoken) > MAX_SPEECH_CHARS}
