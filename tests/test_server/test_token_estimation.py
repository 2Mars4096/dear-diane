from __future__ import annotations

from dan.server.chat import tokens as chat_tokens
from dan.server import mention_resolver
from dan.utils import tokens as shared_tokens


class _BrokenEncoding:
    def encode(self, text: str) -> list[int]:
        raise RuntimeError("offline tokenizer unavailable")


class _BrokenTikToken:
    def encoding_for_model(self, model: str) -> _BrokenEncoding:
        raise KeyError(model)

    def get_encoding(self, name: str) -> _BrokenEncoding:
        raise RuntimeError(f"cannot load {name}")


def test_shared_estimate_tokens_falls_back_when_tiktoken_breaks(monkeypatch):
    monkeypatch.setattr(shared_tokens, "_tiktoken_available", True)
    monkeypatch.setattr(shared_tokens, "tiktoken", _BrokenTikToken())

    assert shared_tokens.estimate_tokens("abcdefgh") == 2


def test_chat_estimate_tokens_falls_back_when_tiktoken_breaks(monkeypatch):
    monkeypatch.setattr(chat_tokens, "_tiktoken_available", True)
    monkeypatch.setattr(chat_tokens, "tiktoken", _BrokenTikToken())

    assert chat_tokens.estimate_tokens("abcdefgh") == 2


def test_mention_resolver_estimate_tokens_falls_back_when_tiktoken_breaks(monkeypatch):
    monkeypatch.setattr(mention_resolver, "_tiktoken_available", True)
    monkeypatch.setattr(mention_resolver, "tiktoken", _BrokenTikToken())

    assert mention_resolver._estimate_tokens("abcdefgh") == 2
