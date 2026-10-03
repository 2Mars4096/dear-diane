"""Host-local API credentials, never included in model profiles or responses."""
from __future__ import annotations
import json
import threading
from pathlib import Path
from diane._atomic_file import atomic_write_text
from diane.server.paths import resolve_graphs_dir

PROVIDERS = {"openrouter", "openai", "deepseek", "moonshot"}
_LOCK = threading.RLock()

def _path() -> Path:
    return Path(resolve_graphs_dir()) / "model_credentials" / "keys.json"

def saved_keys() -> dict[str, str]:
    with _LOCK:
        path = _path()
        return json.loads(path.read_text()) if path.exists() else {}

def save_key(provider: str, key: str) -> None:
    if provider not in PROVIDERS:
        raise ValueError("Unknown API provider")
    key = key.strip()
    if len(key) > 4096 or any(char.isspace() for char in key):
        raise ValueError("Enter a valid API key without whitespace")
    with _LOCK:
        values = saved_keys()
        if key:
            values[provider] = key
        else:
            values.pop(provider, None)
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write_text(path, json.dumps(values), mode=0o600)
