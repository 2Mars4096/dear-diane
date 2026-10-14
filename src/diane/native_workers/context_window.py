"""Use model-advertised context limits without changing global CLI settings."""
import json
from pathlib import Path


def codex_context_limit(home: str, model: str) -> int | None:
    try:
        data = json.loads((Path(home).expanduser() / 'models_cache.json').read_text())
        for row in data.get('models', []):
            if not isinstance(row, dict) or row.get('slug') != model:
                continue
            limit = row.get('max_context_window') or row.get('context_window')
            return limit if type(limit) is int and limit > 0 else None
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return None


# Full IDs already exposed by Diane; these have a native 1M window in Claude Code.
CLAUDE_NATIVE_1M = {
    'claude-fable-5-1', 'claude-fable-5', 'claude-opus-5', 'claude-sonnet-5',
    'claude-opus-4-7', 'claude-opus-4-8', 'claude-opus-5-5',
    'claude-sonnet-5-5', 'claude-haiku-5-5',
}


def claude_context_env(model: str) -> dict[str, str]:
    if model in CLAUDE_NATIVE_1M:
        return {'CLAUDE_CODE_DISABLE_1M_CONTEXT': '0', 'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '1000000'}
    return {}
