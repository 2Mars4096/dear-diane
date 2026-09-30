"""Optional Mac Qwen draft recognition; audio and partial text remain transient."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
import threading
import wave

from .voice import validate_audio

MODEL = 'mlx-community/Qwen3-ASR-1.7B-4bit'
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='qwen-captions')
_busy = threading.Lock()
_model = None


def enabled():
    return os.environ.get('DAN_PERSONAL_LOCAL_VOICE') == '1'


def _load():
    global _model
    if _model is None:
        from mlx_audio.stt import load
        _model = load(MODEL)
    return _model


def warm():
    """Load on the same worker that runs inference, before accepting requests."""
    _executor.submit(_load).result()


async def stream(data: bytes):
    validate_audio(data)
    if not _busy.acquire(blocking=False):
        yield json.dumps({'error': 'Local captions are busy'}) + '\n'
        return
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    stop = threading.Event()

    def emit(item):
        if not loop.is_closed():
            loop.call_soon_threadsafe(queue.put_nowait, item)

    def produce():
        try:
            import numpy as np
            with wave.open(io.BytesIO(data)) as audio:
                samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').astype(np.float32) / 32768.0
            text = ''
            for part in _load().stream_transcribe(samples, max_tokens=256):
                if stop.is_set():
                    break
                if part.text:
                    text += part.text
                    emit({'text': text})
            if not stop.is_set():
                emit({'text': text, 'done': True})
        except Exception:
            if not stop.is_set():
                emit({'error': 'Local captions unavailable'})
        finally:
            _busy.release()
            emit(None)

    _executor.submit(produce)
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield json.dumps({'error': 'Local captions timed out'}) + '\n'
                break
            if item is None:
                break
            yield json.dumps(item, ensure_ascii=False) + '\n'
    finally:
        stop.set()
