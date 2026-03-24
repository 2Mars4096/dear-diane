from __future__ import annotations

import pytest

from dan.agent_runtime import tokens as runtime_tokens
from dan.server.chat import tokens as chat_tokens
from dan.server import mention_resolver
from dan.utils import tokens as shared_tokens


class _FixedEncoding:
    def encode(self, text: str) -> list[int]:
        return [0] * (len(text) // 4)


class _SafeTikToken:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def encoding_for_model(self, model: str) -> _FixedEncoding:
        raise AssertionError("encoding_for_model should not be used")

    def get_encoding(self, name: str) -> _FixedEncoding:
        self.calls.append(name)
        return _FixedEncoding()


class _BrokenTikToken:
    def encoding_for_model(self, model: str) -> _FixedEncoding:
        raise AssertionError("encoding_for_model should not be used")

    def get_encoding(self, name: str) -> _FixedEncoding:
        raise RuntimeError(f"cannot load {name}")


@pytest.mark.parametrize(
    "module, estimate_name",
    [
        (shared_tokens, "estimate_tokens"),
        (runtime_tokens, "estimate_tokens"),
        (chat_tokens, "estimate_tokens"),
        (mention_resolver, "_estimate_tokens"),
    ],
)
def test_estimate_tokens_uses_local_encoding_maps(monkeypatch, module, estimate_name):
    fake = _SafeTikToken()
    monkeypatch.setattr(module, "_tiktoken_available", True)
    monkeypatch.setattr(module, "tiktoken", fake)
    monkeypatch.setattr(module, "MODEL_TO_ENCODING", {"known-model": "known_base"})
    monkeypatch.setattr(module, "MODEL_PREFIX_TO_ENCODING", {"known-prefix-": "prefix_base"})

    estimate = getattr(module, estimate_name)

    assert estimate("abcdefgh", model="known-model") == 2
    assert estimate("abcdefgh", model="known-prefix-2026") == 2
    assert fake.calls == ["known_base", "prefix_base"]


@pytest.mark.parametrize(
    "module, estimate_name",
    [
        (shared_tokens, "estimate_tokens"),
        (runtime_tokens, "estimate_tokens"),
        (chat_tokens, "estimate_tokens"),
        (mention_resolver, "_estimate_tokens"),
    ],
)
def test_estimate_tokens_falls_back_locally_for_unknown_model(monkeypatch, module, estimate_name):
    monkeypatch.setattr(module, "_tiktoken_available", True)
    monkeypatch.setattr(module, "tiktoken", _BrokenTikToken())
    monkeypatch.setattr(module, "MODEL_TO_ENCODING", {})
    monkeypatch.setattr(module, "MODEL_PREFIX_TO_ENCODING", {})

    estimate = getattr(module, estimate_name)

    assert estimate("abcdefgh", model="totally-unknown-model") == 2
