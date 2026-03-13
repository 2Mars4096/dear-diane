"""Tests for PII tokenization module (31-10)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from dan.providers import CompletionResult, StreamChunk
from dan.server.concierge.pii_tokenizer import (
    PIICategory,
    PIISession,
    SensitiveWord,
    SensitiveWordRegistry,
    TokenizingProviderWrapper,
    _check_for_leaks,
    _normalize_placeholder,
    current_pii_session,
    detect_auto_pii,
    detokenize,
    get_current_pii_session,
    handle_pii_command,
    is_pii_enabled,
    set_current_pii_session,
    tokenize,
)


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def session() -> PIISession:
    return PIISession()


@pytest.fixture
def registry() -> SensitiveWordRegistry:
    return SensitiveWordRegistry(words=[
        SensitiveWord(value="John Smith", category="name"),
        SensitiveWord(value="123 Main St", category="address"),
        SensitiveWord(value="password123", category="password"),
    ])


@pytest.fixture
def tmp_registry_path(tmp_path: Path) -> Path:
    return tmp_path / "sensitive_words.json"


# ===================================================================
# SensitiveWordRegistry CRUD
# ===================================================================


class TestSensitiveWordRegistry:
    def test_add_word(self) -> None:
        reg = SensitiveWordRegistry()
        reg.add("Alice", "name")
        assert len(reg.words) == 1
        assert reg.words[0].value == "Alice"
        assert reg.words[0].category == "name"

    def test_add_duplicate_ignored(self) -> None:
        reg = SensitiveWordRegistry()
        reg.add("Alice", "name")
        reg.add("alice", "name")  # case-insensitive duplicate
        assert len(reg.words) == 1

    def test_add_same_value_different_category(self) -> None:
        reg = SensitiveWordRegistry()
        reg.add("secret", "password")
        reg.add("secret", "custom")
        assert len(reg.words) == 2

    def test_remove_word(self) -> None:
        reg = SensitiveWordRegistry()
        reg.add("Alice", "name")
        assert reg.remove("Alice") is True
        assert len(reg.words) == 0

    def test_remove_case_insensitive(self) -> None:
        reg = SensitiveWordRegistry()
        reg.add("Alice", "name")
        assert reg.remove("ALICE") is True
        assert len(reg.words) == 0

    def test_remove_nonexistent(self) -> None:
        reg = SensitiveWordRegistry()
        assert reg.remove("NotHere") is False

    def test_list_words(self, registry: SensitiveWordRegistry) -> None:
        words = registry.list_words()
        assert len(words) == 3
        assert words[0].value == "John Smith"

    def test_save_and_load(self, tmp_registry_path: Path) -> None:
        reg = SensitiveWordRegistry()
        reg.add("Bob", "name")
        reg.add("bob@example.com", "email")
        reg.save(tmp_registry_path)

        loaded = SensitiveWordRegistry.load(tmp_registry_path)
        assert len(loaded.words) == 2
        assert loaded.words[0].value == "Bob"
        assert loaded.words[1].category == "email"

    def test_load_missing_file(self, tmp_path: Path) -> None:
        loaded = SensitiveWordRegistry.load(tmp_path / "nonexistent.json")
        assert len(loaded.words) == 0

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        deep_path = tmp_path / "a" / "b" / "words.json"
        reg = SensitiveWordRegistry()
        reg.add("test", "custom")
        reg.save(deep_path)
        assert deep_path.exists()

    def test_file_format(self, tmp_registry_path: Path) -> None:
        reg = SensitiveWordRegistry()
        reg.add("John Smith", "name")
        reg.save(tmp_registry_path)

        data = json.loads(tmp_registry_path.read_text())
        assert "words" in data
        assert data["words"][0] == {"value": "John Smith", "category": "name"}

    def test_save_restricts_file_permissions(self, tmp_registry_path: Path) -> None:
        reg = SensitiveWordRegistry()
        reg.add("John Smith", "name")
        reg.save(tmp_registry_path)

        mode = os.stat(tmp_registry_path).st_mode & 0o777
        assert mode == 0o600


# ===================================================================
# PIISession
# ===================================================================


class TestPIISession:
    def test_get_or_create_placeholder(self, session: PIISession) -> None:
        p = session.get_or_create_placeholder("John", "name")
        assert p == "[PERSON_1]"

    def test_determinism(self, session: PIISession) -> None:
        p1 = session.get_or_create_placeholder("John", "name")
        p2 = session.get_or_create_placeholder("John", "name")
        assert p1 == p2

    def test_case_insensitive_lookup(self, session: PIISession) -> None:
        p1 = session.get_or_create_placeholder("John", "name")
        p2 = session.get_or_create_placeholder("john", "name")
        assert p1 == p2

    def test_counter_increments(self, session: PIISession) -> None:
        p1 = session.get_or_create_placeholder("Alice", "name")
        p2 = session.get_or_create_placeholder("Bob", "name")
        assert p1 == "[PERSON_1]"
        assert p2 == "[PERSON_2]"

    def test_different_categories(self, session: PIISession) -> None:
        p1 = session.get_or_create_placeholder("John", "name")
        p2 = session.get_or_create_placeholder("123 Main St", "address")
        assert p1 == "[PERSON_1]"
        assert p2 == "[ADDRESS_1]"

    def test_lookup_original(self, session: PIISession) -> None:
        session.get_or_create_placeholder("John", "name")
        assert session.lookup_original("[PERSON_1]") == "John"

    def test_lookup_original_case_insensitive(self, session: PIISession) -> None:
        session.get_or_create_placeholder("John", "name")
        assert session.lookup_original("[person_1]") == "John"
        assert session.lookup_original("PERSON_1") == "John"

    def test_lookup_missing(self, session: PIISession) -> None:
        assert session.lookup_original("[NONEXISTENT_1]") is None

    def test_clear(self, session: PIISession) -> None:
        session.get_or_create_placeholder("John", "name")
        session.clear()
        assert len(session.placeholder_to_original) == 0
        assert len(session.original_to_placeholder) == 0

    def test_cross_turn_consistency(self, session: PIISession) -> None:
        """Same word maps to same placeholder across simulated turns."""
        p_turn1 = session.get_or_create_placeholder("John Smith", "name")
        _ = session.get_or_create_placeholder("Alice", "name")
        p_turn2 = session.get_or_create_placeholder("John Smith", "name")
        assert p_turn1 == p_turn2 == "[PERSON_1]"


# ===================================================================
# Normalize placeholder
# ===================================================================


class TestNormalizePlaceholder:
    def test_full_form(self) -> None:
        assert _normalize_placeholder("[PERSON_1]") == "[person_1]"

    def test_without_brackets(self) -> None:
        assert _normalize_placeholder("PERSON_1") == "[person_1]"

    def test_mixed_case(self) -> None:
        assert _normalize_placeholder("Person_1") == "[person_1]"

    def test_with_whitespace(self) -> None:
        assert _normalize_placeholder("  [PERSON_1]  ") == "[person_1]"


# ===================================================================
# Auto-detection patterns
# ===================================================================


class TestAutoDetection:
    def test_email(self) -> None:
        hits = detect_auto_pii("Contact me at john@example.com please")
        assert len(hits) == 1
        assert hits[0] == ("john@example.com", "email")

    def test_phone_us(self) -> None:
        hits = detect_auto_pii("Call me at 555-123-4567")
        assert len(hits) == 1
        assert hits[0][1] == "phone"

    def test_phone_with_parens(self) -> None:
        hits = detect_auto_pii("Call (555) 123-4567")
        assert len(hits) == 1
        assert hits[0][1] == "phone"

    def test_ssn(self) -> None:
        hits = detect_auto_pii("SSN: 123-45-6789")
        assert len(hits) == 1
        assert hits[0] == ("123-45-6789", "id_number")

    def test_credit_card(self) -> None:
        hits = detect_auto_pii("Card: 4111111111111111")
        assert len(hits) == 1
        assert hits[0][1] == "financial"

    def test_credit_card_with_dashes(self) -> None:
        hits = detect_auto_pii("Card: 4111-1111-1111-1111")
        assert len(hits) == 1
        assert hits[0][1] == "financial"

    def test_ip_address(self) -> None:
        hits = detect_auto_pii("Server at 192.168.1.100")
        assert len(hits) == 1
        assert hits[0] == ("192.168.1.100", "id_number")

    def test_no_false_positives_on_plain_text(self) -> None:
        hits = detect_auto_pii("Hello world, this is a normal sentence.")
        assert len(hits) == 0

    def test_multiple_patterns(self) -> None:
        text = "Email: alice@test.com, Phone: 555-111-2222"
        hits = detect_auto_pii(text)
        categories = {h[1] for h in hits}
        assert "email" in categories
        assert "phone" in categories


# ===================================================================
# Tokenizer
# ===================================================================


class TestTokenize:
    def test_single_word(self, session: PIISession) -> None:
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="Alice", category="name"),
        ])
        result = tokenize("Hello Alice!", session, reg)
        assert "Alice" not in result
        assert "[PERSON_1]" in result

    def test_multi_word_phrase(self, session: PIISession, registry: SensitiveWordRegistry) -> None:
        result = tokenize("Contact John Smith at the office.", session, registry)
        assert "John Smith" not in result
        assert "[PERSON_1]" in result

    def test_word_boundary(self, session: PIISession) -> None:
        """'John' should match 'John' but NOT 'Johnson'."""
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="John", category="name"),
        ])
        result = tokenize("John and Johnson went to the park.", session, reg)
        assert result.startswith("[PERSON_1]")
        assert "Johnson" in result

    def test_possessive(self, session: PIISession) -> None:
        """'John's' → '[PERSON_1]'s'."""
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="John", category="name"),
        ])
        result = tokenize("This is John's book.", session, reg)
        assert "[PERSON_1]'s" in result
        assert "John's" not in result

    def test_case_insensitive(self, session: PIISession) -> None:
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="Alice", category="name"),
        ])
        result = tokenize("Talk to alice and ALICE.", session, reg)
        assert "alice" not in result.lower().replace("[person_1]", "")

    def test_longest_match_first(self, session: PIISession) -> None:
        """'John Smith' matched before 'John' alone."""
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="John", category="name"),
            SensitiveWord(value="John Smith", category="name"),
        ])
        result = tokenize("John Smith is here.", session, reg)
        assert "[PERSON_1]" in result
        assert "Smith" not in result

    def test_address_replacement(self, session: PIISession, registry: SensitiveWordRegistry) -> None:
        result = tokenize("I live at 123 Main St in Springfield.", session, registry)
        assert "123 Main St" not in result
        assert "[ADDRESS_1]" in result

    def test_password_replacement(self, session: PIISession, registry: SensitiveWordRegistry) -> None:
        result = tokenize("My password is password123", session, registry)
        assert "password123" not in result
        assert "[PASSWORD_1]" in result

    def test_auto_detection_in_tokenize(self, session: PIISession) -> None:
        reg = SensitiveWordRegistry()
        result = tokenize("Email me at test@example.com", session, reg)
        assert "test@example.com" not in result
        assert "[EMAIL_1]" in result

    def test_combined_registry_and_auto(self, session: PIISession) -> None:
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="Alice", category="name"),
        ])
        result = tokenize("Alice's email is alice@test.com", session, reg)
        assert "Alice" not in result.split("[")[0]
        assert "alice@test.com" not in result

    def test_deterministic_across_calls(self, session: PIISession) -> None:
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="Alice", category="name"),
        ])
        r1 = tokenize("Hello Alice", session, reg)
        r2 = tokenize("Goodbye Alice", session, reg)
        p1 = r1.replace("Hello ", "")
        p2 = r2.replace("Goodbye ", "")
        assert p1 == p2  # same placeholder both times

    def test_empty_text(self, session: PIISession, registry: SensitiveWordRegistry) -> None:
        assert tokenize("", session, registry) == ""

    def test_no_matches(self, session: PIISession) -> None:
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="ZZZ_NOT_FOUND", category="custom"),
        ])
        text = "Nothing special here."
        assert tokenize(text, session, reg) == text


# ===================================================================
# Detokenizer
# ===================================================================


class TestDetokenize:
    def test_basic_restore(self, session: PIISession) -> None:
        session.get_or_create_placeholder("John", "name")
        result = detokenize("Hello [PERSON_1], how are you?", session)
        assert result == "Hello John, how are you?"

    def test_multiple_placeholders(self, session: PIISession) -> None:
        session.get_or_create_placeholder("Alice", "name")
        session.get_or_create_placeholder("123 Oak Ave", "address")
        text = "[PERSON_1] lives at [ADDRESS_1]."
        result = detokenize(text, session)
        assert result == "Alice lives at 123 Oak Ave."

    def test_variation_without_brackets(self, session: PIISession) -> None:
        session.get_or_create_placeholder("John", "name")
        result = detokenize("Hello PERSON_1, how are you?", session)
        assert result == "Hello John, how are you?"

    def test_variation_mixed_case(self, session: PIISession) -> None:
        session.get_or_create_placeholder("John", "name")
        result = detokenize("Hello Person_1, how are you?", session)
        assert result == "Hello John, how are you?"

    def test_no_placeholders(self, session: PIISession) -> None:
        text = "Just a normal sentence."
        assert detokenize(text, session) == text

    def test_unknown_placeholder_preserved(self, session: PIISession) -> None:
        text = "Hello [UNKNOWN_99]"
        assert detokenize(text, session) == text


# ===================================================================
# Leak detection
# ===================================================================


class TestLeakDetection:
    def test_leak_logged(self, session: PIISession, caplog: pytest.LogCaptureFixture) -> None:
        session.get_or_create_placeholder("John", "name")
        with caplog.at_level(logging.WARNING):
            _check_for_leaks("The LLM mentioned John in its response", session)
        assert any("PII leak detected" in r.message for r in caplog.records)

    def test_no_leak_no_warning(self, session: PIISession, caplog: pytest.LogCaptureFixture) -> None:
        session.get_or_create_placeholder("John", "name")
        with caplog.at_level(logging.WARNING):
            _check_for_leaks("The LLM said something safe.", session)
        assert not any("PII leak detected" in r.message for r in caplog.records)


# ===================================================================
# TokenizingProviderWrapper
# ===================================================================


class _FakeProvider:
    """Minimal provider for testing the wrapper."""

    def __init__(self, response_text: str = "OK") -> None:
        self._response_text = response_text

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        return CompletionResult(text=self._response_text, model=model)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        words = self._response_text.split()
        acc = ""
        for i, word in enumerate(words):
            acc += (" " if acc else "") + word
            yield StreamChunk(
                delta=word,
                accumulated=acc,
                done=(i == len(words) - 1),
            )


class TestTokenizingProviderWrapper:
    @pytest.mark.asyncio
    async def test_complete_tokenizes_outbound(self) -> None:
        session = PIISession()
        reg = SensitiveWordRegistry(words=[SensitiveWord(value="Alice", category="name")])

        captured: list[list[dict[str, Any]]] = []
        original_provider = _FakeProvider(response_text="Sure, [PERSON_1] is great.")

        original_complete = original_provider.complete

        async def spy_complete(messages: list[dict[str, Any]], *args: Any, **kwargs: Any) -> CompletionResult:
            captured.append(messages)
            return await original_complete(messages, *args, **kwargs)

        original_provider.complete = spy_complete  # type: ignore[assignment]
        wrapper = TokenizingProviderWrapper(original_provider, session, reg)  # type: ignore[arg-type]

        result = await wrapper.complete(
            [{"role": "user", "content": "Tell me about Alice"}],
            model="test",
        )

        assert captured[0][0]["content"] == "Tell me about [PERSON_1]"
        assert result.text == "Sure, Alice is great."

    @pytest.mark.asyncio
    async def test_complete_detokenizes_response(self) -> None:
        session = PIISession()
        reg = SensitiveWordRegistry(words=[SensitiveWord(value="Bob", category="name")])
        session.get_or_create_placeholder("Bob", "name")

        provider = _FakeProvider(response_text="Hello [PERSON_1]!")
        wrapper = TokenizingProviderWrapper(provider, session, reg)  # type: ignore[arg-type]

        result = await wrapper.complete(
            [{"role": "user", "content": "Hello"}], model="test"
        )
        assert result.text == "Hello Bob!"

    @pytest.mark.asyncio
    async def test_stream_detokenizes(self) -> None:
        session = PIISession()
        reg = SensitiveWordRegistry(words=[SensitiveWord(value="Charlie", category="name")])
        session.get_or_create_placeholder("Charlie", "name")

        provider = _FakeProvider(response_text="Hi [PERSON_1]")
        wrapper = TokenizingProviderWrapper(provider, session, reg)  # type: ignore[arg-type]

        chunks: list[StreamChunk] = []
        async for chunk in wrapper.stream(
            [{"role": "user", "content": "test"}], model="test"
        ):
            chunks.append(chunk)

        assert len(chunks) > 0
        final = chunks[-1]
        assert "Charlie" in final.accumulated

    @pytest.mark.asyncio
    async def test_stream_handles_placeholder_split_across_chunks(self) -> None:
        session = PIISession()
        reg = SensitiveWordRegistry(words=[SensitiveWord(value="alice@example.com", category="email")])
        session.get_or_create_placeholder("alice@example.com", "email")

        class _SplitPlaceholderProvider:
            async def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
                raise NotImplementedError

            async def stream(
                self,
                messages: list[dict[str, Any]],
                model: str,
                temperature: float = 0.7,
                max_tokens: int | None = None,
                **kwargs: Any,
            ) -> AsyncIterator[StreamChunk]:
                yield StreamChunk(delta="Hi [EMAIL", accumulated="Hi [EMAIL")
                yield StreamChunk(
                    delta="_1]",
                    accumulated="Hi [EMAIL_1]",
                    done=True,
                )

        wrapper = TokenizingProviderWrapper(
            _SplitPlaceholderProvider(), session, reg,  # type: ignore[arg-type]
        )

        chunks: list[StreamChunk] = []
        async for chunk in wrapper.stream(
            [{"role": "user", "content": "email alice@example.com"}],
            model="test",
        ):
            chunks.append(chunk)

        assert [chunk.delta for chunk in chunks] == ["Hi ", "alice@example.com"]
        assert chunks[-1].accumulated == "Hi alice@example.com"

    @pytest.mark.asyncio
    async def test_multipart_content_tokenized(self) -> None:
        session = PIISession()
        reg = SensitiveWordRegistry(words=[SensitiveWord(value="Diana", category="name")])

        captured: list[list[dict[str, Any]]] = []
        provider = _FakeProvider(response_text="OK")
        original_complete = provider.complete

        async def spy(messages: list[dict[str, Any]], *a: Any, **kw: Any) -> CompletionResult:
            captured.append(messages)
            return await original_complete(messages, *a, **kw)

        provider.complete = spy  # type: ignore[assignment]
        wrapper = TokenizingProviderWrapper(provider, session, reg)  # type: ignore[arg-type]

        await wrapper.complete(
            [{"role": "user", "content": [
                {"type": "text", "text": "About Diana"},
                {"type": "image_url", "url": "http://example.com/img.png"},
            ]}],
            model="test",
        )

        content = captured[0][0]["content"]
        assert content[0]["text"] == "About [PERSON_1]"
        assert content[1]["type"] == "image_url"  # non-text parts unchanged

    @pytest.mark.asyncio
    async def test_tool_calls_not_rewritten(self) -> None:
        session = PIISession()
        reg = SensitiveWordRegistry(words=[SensitiveWord(value="Eve", category="name")])
        provider = _FakeProvider()

        result = CompletionResult(
            text="",
            model="test",
            tool_calls=[{"function": {"name": "search", "arguments": '{"q": "Eve"}'}}],
        )
        provider.complete = AsyncMock(return_value=result)  # type: ignore[assignment]
        wrapper = TokenizingProviderWrapper(provider, session, reg)  # type: ignore[arg-type]

        out = await wrapper.complete(
            [{"role": "user", "content": "Find Eve"}], model="test"
        )
        assert out.tool_calls is not None
        assert out.tool_calls[0]["function"]["arguments"] == '{"q": "Eve"}'


# ===================================================================
# Command handler
# ===================================================================


class TestHandlePiiCommand:
    def test_disabled_gate(self) -> None:
        reg = SensitiveWordRegistry()
        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "0"}):
            result = handle_pii_command("/pii list", reg)
        assert "disabled" in result.lower()

    def test_add(self) -> None:
        reg = SensitiveWordRegistry()
        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "1"}):
            with patch.object(SensitiveWordRegistry, "save"):
                result = handle_pii_command('/pii add "John Smith" --category name', reg)
        assert "Added" in result
        assert len(reg.words) == 1

    def test_add_invalid_category(self) -> None:
        reg = SensitiveWordRegistry()
        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "1"}):
            result = handle_pii_command('/pii add "test" --category alien', reg)
        assert "Invalid category" in result

    def test_list_empty(self) -> None:
        reg = SensitiveWordRegistry()
        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "1"}):
            result = handle_pii_command("/pii list", reg)
        assert "No sensitive words" in result

    def test_list_with_words(self) -> None:
        reg = SensitiveWordRegistry(words=[SensitiveWord(value="Alice", category="name")])
        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "1"}):
            result = handle_pii_command("/pii list", reg)
        assert "Alice" in result
        assert "name" in result

    def test_remove(self) -> None:
        reg = SensitiveWordRegistry(words=[SensitiveWord(value="Alice", category="name")])
        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "1"}):
            with patch.object(SensitiveWordRegistry, "save"):
                result = handle_pii_command('/pii remove "Alice"', reg)
        assert "Removed" in result
        assert len(reg.words) == 0

    def test_remove_not_found(self) -> None:
        reg = SensitiveWordRegistry()
        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "1"}):
            result = handle_pii_command('/pii remove "Nobody"', reg)
        assert "not found" in result

    def test_clear_session(self) -> None:
        reg = SensitiveWordRegistry()
        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "1"}):
            result = handle_pii_command("/pii clear-session", reg)
        assert "no active" in result.lower()

    def test_clear_session_with_live_mapping(self) -> None:
        from dan.server.concierge.pii_tokenizer import get_pii_session

        reg = SensitiveWordRegistry()
        session = get_pii_session("test-session")
        session.get_or_create_placeholder("Alice", "name")

        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "1"}):
            result = handle_pii_command(
                "/pii clear-session",
                reg,
                session_key="test-session",
            )
        assert "cleared" in result.lower()

    def test_unknown_subcommand(self) -> None:
        reg = SensitiveWordRegistry()
        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "1"}):
            result = handle_pii_command("/pii frobnicate", reg)
        assert "Unknown" in result

    def test_no_subcommand(self) -> None:
        reg = SensitiveWordRegistry()
        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "1"}):
            result = handle_pii_command("/pii", reg)
        assert "Usage" in result

    def test_add_default_category(self) -> None:
        reg = SensitiveWordRegistry()
        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "1"}):
            with patch.object(SensitiveWordRegistry, "save"):
                result = handle_pii_command('/pii add "secret"', reg)
        assert "Added" in result
        assert reg.words[0].category == "custom"


# ===================================================================
# is_pii_enabled
# ===================================================================


class TestIsPiiEnabled:
    def test_enabled(self) -> None:
        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "1"}):
            assert is_pii_enabled() is True

    def test_enabled_true_string(self) -> None:
        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "true"}):
            assert is_pii_enabled() is True

    def test_disabled_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert is_pii_enabled() is False

    def test_disabled_zero(self) -> None:
        with patch.dict("os.environ", {"DAN_PII_PROTECTION": "0"}):
            assert is_pii_enabled() is False


# ===================================================================
# PII Edge Cases (31-10 §6-3)
# ===================================================================


class TestPIISessionContextVar:
    """31-10 §4-2: PIISession accessible via ContextVar for provider layer."""

    def test_default_is_none(self) -> None:
        token = current_pii_session.set(None)
        try:
            assert get_current_pii_session() is None
        finally:
            current_pii_session.reset(token)

    def test_set_and_get(self) -> None:
        session = PIISession()
        session.get_or_create_placeholder("Alice", "name")
        reset_token = set_current_pii_session(session)
        try:
            retrieved = get_current_pii_session()
            assert retrieved is session
            assert retrieved.lookup_original("[PERSON_1]") == "Alice"
        finally:
            current_pii_session.reset(reset_token)

    def test_isolation_across_tasks(self) -> None:
        """ContextVar provides per-task isolation in asyncio."""
        import asyncio

        results: list[str | None] = [None, None]

        async def task_a():
            s = PIISession()
            s.get_or_create_placeholder("TaskA", "name")
            tok = set_current_pii_session(s)
            await asyncio.sleep(0.01)
            retrieved = get_current_pii_session()
            results[0] = retrieved.lookup_original("[PERSON_1]") if retrieved else None
            current_pii_session.reset(tok)

        async def task_b():
            s = PIISession()
            s.get_or_create_placeholder("TaskB", "name")
            tok = set_current_pii_session(s)
            await asyncio.sleep(0.01)
            retrieved = get_current_pii_session()
            results[1] = retrieved.lookup_original("[PERSON_1]") if retrieved else None
            current_pii_session.reset(tok)

        async def _run():
            await asyncio.gather(task_a(), task_b())
        asyncio.run(_run())
        assert results[0] == "TaskA"
        assert results[1] == "TaskB"

    @pytest.mark.asyncio
    async def test_wrapper_uses_contextvar_when_no_explicit_session(self) -> None:
        """TokenizingProviderWrapper falls back to ContextVar session."""
        session = PIISession()
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="Alice", category="name"),
        ])
        reset_token = set_current_pii_session(session)
        try:
            provider = _FakeProvider(response_text="Hello [PERSON_1]!")
            wrapper = TokenizingProviderWrapper(provider, session=None, registry=reg)  # type: ignore[arg-type]
            result = await wrapper.complete(
                [{"role": "user", "content": "Tell me about Alice"}],
                model="test",
            )
            assert "Alice" not in result.text or result.text == "Hello Alice!"
            assert "[PERSON_1]" not in result.text
        finally:
            current_pii_session.reset(reset_token)


class TestPIIInCodeBlocks:
    """PII inside fenced code blocks should be tokenized by default,
    but skipped when DAN_PII_SKIP_CODE_BLOCKS=1."""

    def test_pii_in_code_block_tokenized_by_default(self, session: PIISession) -> None:
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="Alice", category="name"),
        ])
        text = "Here is the config:\n```\nuser = Alice\n```"
        result = tokenize(text, session, reg)
        assert "Alice" not in result
        assert "[PERSON_1]" in result

    def test_pii_in_inline_code_tokenized(self, session: PIISession) -> None:
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="Bob", category="name"),
        ])
        text = "Run `grep Bob /var/log/auth.log` to check."
        result = tokenize(text, session, reg)
        assert "Bob" not in result
        assert "[PERSON_1]" in result

    def test_pii_in_code_block_skipped_when_env_set(self, session: PIISession) -> None:
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="Alice", category="name"),
        ])
        text = "Here is the config:\n```\nuser = Alice\n```\nAlice wrote it."
        with patch.dict("os.environ", {"DAN_PII_SKIP_CODE_BLOCKS": "1"}):
            result = tokenize(text, session, reg)
        assert "```\nuser = Alice\n```" in result
        assert result.endswith("[PERSON_1] wrote it.")

    def test_inline_code_preserved_when_env_set(self, session: PIISession) -> None:
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="Bob", category="name"),
        ])
        text = "Run `grep Bob /var/log` to find Bob's entries."
        with patch.dict("os.environ", {"DAN_PII_SKIP_CODE_BLOCKS": "1"}):
            result = tokenize(text, session, reg)
        assert "`grep Bob /var/log`" in result
        assert "[PERSON_1]'s entries" in result

    def test_auto_detected_email_in_code_block(self, session: PIISession) -> None:
        reg = SensitiveWordRegistry()
        text = "```python\nemail = 'admin@corp.com'\n```"
        result = tokenize(text, session, reg)
        assert "admin@corp.com" not in result
        assert "[EMAIL_1]" in result


class TestPIIInToolArguments:
    """PII in tool call arguments must NOT be tokenized —
    tool inputs should stay exact to avoid breaking tool execution."""

    def test_tool_arguments_preserved(self) -> None:
        session = PIISession()
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="Eve", category="name"),
        ])
        tool_msg: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "search",
                        "arguments": '{"query": "Eve"}',
                    },
                }
            ],
        }
        provider = _FakeProvider()
        wrapper = TokenizingProviderWrapper(provider, session=session, registry=reg)  # type: ignore[arg-type]
        tokenized_msgs = wrapper._tokenize_messages([tool_msg])
        tc = tokenized_msgs[0].get("tool_calls")
        assert tc is not None
        assert tc[0]["function"]["arguments"] == '{"query": "Eve"}'

    def test_tool_result_content_preserved(self) -> None:
        session = PIISession()
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="SecretKey123", category="password"),
        ])
        tool_result_msg: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "API key is SecretKey123",
        }
        provider = _FakeProvider()
        wrapper = TokenizingProviderWrapper(provider, session=session, registry=reg)  # type: ignore[arg-type]
        tokenized = wrapper._tokenize_messages([tool_result_msg])
        assert "[PASSWORD_1]" in tokenized[0]["content"]


class TestPIIInSystemPrompts:
    """PII in system messages should be tokenized."""

    def test_system_prompt_tokenized(self) -> None:
        session = PIISession()
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="John Smith", category="name"),
        ])
        msgs = [
            {"role": "system", "content": "You are helping John Smith with their account."},
            {"role": "user", "content": "What is my balance?"},
        ]
        provider = _FakeProvider()
        wrapper = TokenizingProviderWrapper(provider, session=session, registry=reg)  # type: ignore[arg-type]
        tokenized = wrapper._tokenize_messages(msgs)
        assert "John Smith" not in tokenized[0]["content"]
        assert "[PERSON_1]" in tokenized[0]["content"]
        assert tokenized[1]["content"] == "What is my balance?"

    def test_system_prompt_with_email(self) -> None:
        session = PIISession()
        reg = SensitiveWordRegistry()
        msgs = [
            {"role": "system", "content": "User email: admin@example.com"},
        ]
        provider = _FakeProvider()
        wrapper = TokenizingProviderWrapper(provider, session=session, registry=reg)  # type: ignore[arg-type]
        tokenized = wrapper._tokenize_messages(msgs)
        assert "admin@example.com" not in tokenized[0]["content"]
        assert "[EMAIL_1]" in tokenized[0]["content"]

    def test_multipart_system_prompt(self) -> None:
        session = PIISession()
        reg = SensitiveWordRegistry(words=[
            SensitiveWord(value="Acme Corp", category="custom"),
        ])
        msgs = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "Client: Acme Corp"},
                    {"type": "text", "text": "Handle with care."},
                ],
            },
        ]
        provider = _FakeProvider()
        wrapper = TokenizingProviderWrapper(provider, session=session, registry=reg)  # type: ignore[arg-type]
        tokenized = wrapper._tokenize_messages(msgs)
        content = tokenized[0]["content"]
        assert content[0]["text"] == "Client: [CUSTOM_1]"
        assert content[1]["text"] == "Handle with care."
