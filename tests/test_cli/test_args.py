"""Unit tests for CLI argument parsing and config resolution."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from dan.cli import resolve_config
from dan.cli.run import build_parser, parse_inputs


class TestBuildParser:
    def test_minimal_source(self):
        parser = build_parser()
        args = parser.parse_args(["workflow.json"])
        assert args.source == "workflow.json"
        assert args.inputs == []
        assert args.input_json is None
        assert args.headless is False
        assert args.quiet is False
        assert args.verbose is False
        assert args.output_format == "text"
        assert args.background is False
        assert args.human_timeout == 300

    def test_all_flags(self):
        parser = build_parser()
        args = parser.parse_args([
            "my_workflow.py",
            "--api-key", "sk-123",
            "--model", "gpt-4o",
            "--base-url", "http://localhost:8080/v1",
            "--workspace", "/tmp/ws",
            "--input", "topic=AI",
            "--input", "depth=3",
            "--input-json", '{"format": "pdf"}',
            "--headless",
            "--human-timeout", "60",
            "--auto-approve",
            "--quiet",
            "--output-format", "json",
            "--output", "/tmp/out.json",
            "--artifacts-dir", "/tmp/art",
            "--goal",
        ])
        assert args.source == "my_workflow.py"
        assert args.api_key == "sk-123"
        assert args.model == "gpt-4o"
        assert args.base_url == "http://localhost:8080/v1"
        assert args.workspace == "/tmp/ws"
        assert args.inputs == ["topic=AI", "depth=3"]
        assert args.input_json == '{"format": "pdf"}'
        assert args.headless is True
        assert args.human_timeout == 60
        assert args.auto_approve is True
        assert args.quiet is True
        assert args.output_format == "json"
        assert args.output == "/tmp/out.json"
        assert args.artifacts_dir == "/tmp/art"
        assert args.goal is True

    def test_background_flag(self):
        parser = build_parser()
        args = parser.parse_args(["wf.json", "--background"])
        assert args.background is True

    def test_bg_shorthand(self):
        parser = build_parser()
        args = parser.parse_args(["wf.json", "--bg"])
        assert args.background is True

    def test_verbose_flag(self):
        parser = build_parser()
        args = parser.parse_args(["wf.json", "-v"])
        assert args.verbose is True

    def test_interactive_flag(self):
        parser = build_parser()
        args = parser.parse_args(["wf.json", "--interactive"])
        assert args.interactive is True

    def test_short_input(self):
        parser = build_parser()
        args = parser.parse_args(["wf.json", "-i", "x=1", "-i", "y=2"])
        assert args.inputs == ["x=1", "y=2"]


class TestParseInputs:
    def test_key_value_pairs(self):
        result = parse_inputs(["topic=AI safety", "depth=3"])
        assert result == {"topic": "AI safety", "depth": "3"}

    def test_json_input(self):
        result = parse_inputs([], input_json='{"key": "value", "n": 5}')
        assert result == {"key": "value", "n": 5}

    def test_merge_kv_and_json(self):
        result = parse_inputs(["extra=yes"], input_json='{"base": "data"}')
        assert result == {"base": "data", "extra": "yes"}

    def test_kv_overrides_json(self):
        result = parse_inputs(["key=override"], input_json='{"key": "original"}')
        assert result["key"] == "override"

    def test_invalid_kv_exits(self):
        with pytest.raises(SystemExit):
            parse_inputs(["no-equals-sign"])

    def test_invalid_json_exits(self):
        with pytest.raises(SystemExit):
            parse_inputs([], input_json="not json")

    def test_empty(self):
        result = parse_inputs([])
        assert result == {}


class TestResolveConfig:
    def test_explicit_args_take_priority(self):
        with patch.dict(os.environ, {"DAN_LLM_API_KEY": "env-key", "DAN_MODEL": "env-model"}):
            cfg = resolve_config(api_key="cli-key", model="cli-model")
            assert cfg["api_key"] == "cli-key"
            assert cfg["model"] == "cli-model"

    def test_env_fallback(self):
        with patch.dict(os.environ, {
            "DAN_LLM_API_KEY": "dan-key",
            "DAN_LLM_BASE_URL": "http://dan-url/v1",
            "DAN_MODEL": "dan-model",
        }, clear=False):
            cfg = resolve_config()
            assert cfg["api_key"] == "dan-key"
            assert cfg["base_url"] == "http://dan-url/v1"
            assert cfg["model"] == "dan-model"

    def test_openai_key_fallback(self):
        env = {"OPENAI_API_KEY": "openai-key"}
        with patch.dict(os.environ, env, clear=True):
            cfg = resolve_config()
            assert cfg["api_key"] == "openai-key"

    def test_dan_key_over_openai(self):
        env = {"DAN_LLM_API_KEY": "dan-key", "OPENAI_API_KEY": "openai-key"}
        with patch.dict(os.environ, env, clear=True):
            cfg = resolve_config()
            assert cfg["api_key"] == "dan-key"

    def test_workspace_default_is_cwd(self):
        cfg = resolve_config()
        assert cfg["workspace"]  # non-empty
