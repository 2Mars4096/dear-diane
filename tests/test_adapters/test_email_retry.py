"""Tests for email adapter malformed reply retry logic."""

from __future__ import annotations

from dan.adapters.email_adapter import EmailAdapter, EmailAdapterConfig, MAX_REPLY_RETRIES


class TestEmailValidateReply:
    def test_valid_reply_with_required_keys(self):
        reply = {"response": "hello", "name": "Alice"}
        schema = {"properties": {"name": {"type": "string"}}, "required": ["name"]}
        assert EmailAdapter._validate_reply(reply, schema) is True

    def test_empty_response_fails(self):
        reply = {"response": ""}
        schema = {"properties": {"name": {"type": "string"}}, "required": ["name"]}
        assert EmailAdapter._validate_reply(reply, schema) is False

    def test_missing_required_key_fails(self):
        reply = {"response": "hello"}
        schema = {"properties": {"name": {"type": "string"}}, "required": ["name"]}
        assert EmailAdapter._validate_reply(reply, schema) is False

    def test_no_properties_passes(self):
        reply = {"response": "hello"}
        schema = {}
        assert EmailAdapter._validate_reply(reply, schema) is True

    def test_no_required_passes_with_response(self):
        reply = {"response": "hello"}
        schema = {"properties": {"name": {"type": "string"}}}
        assert EmailAdapter._validate_reply(reply, schema) is True


class TestRetryCounterTracking:
    def test_max_retries_constant(self):
        assert MAX_REPLY_RETRIES == 3

    def test_adapter_initialises_tracking_dicts(self):
        config = EmailAdapterConfig(
            imap_host="localhost",
            smtp_host="localhost",
            target_email="test@example.com",
        )
        adapter = EmailAdapter(config)
        assert adapter._pending_schemas == {}
        assert adapter._pending_prompts == {}
        assert adapter._retry_counts == {}
