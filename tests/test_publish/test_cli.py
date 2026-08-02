"""Tests for dan-publish CLI argument parsing."""

from __future__ import annotations

import pytest

from dan.cli.publish import build_parser


class TestBuildParser:
    def test_defaults(self):
        p = build_parser()
        args = p.parse_args(["workflow.json"])
        assert args.source == "workflow.json"
        assert args.server_type == "mcp"
        assert args.port == 8001
        assert args.host == "0.0.0.0"
        assert args.name is None
        assert args.api_key is None
        assert args.human_timeout == 300.0
        assert args.generate_config is False
        assert args.docs is False
        assert args.openapi is False

    def test_type_http(self):
        p = build_parser()
        args = p.parse_args(["wf.json", "--type", "http"])
        assert args.server_type == "http"

    def test_type_both(self):
        p = build_parser()
        args = p.parse_args(["wf.json", "--type", "both"])
        assert args.server_type == "both"

    def test_port(self):
        p = build_parser()
        args = p.parse_args(["wf.json", "--port", "9000"])
        assert args.port == 9000

    def test_name_override(self):
        p = build_parser()
        args = p.parse_args(["wf.json", "--name", "my-api"])
        assert args.name == "my-api"

    def test_api_key(self):
        p = build_parser()
        args = p.parse_args(["wf.json", "--api-key", "secret"])
        assert args.api_key == "secret"

    def test_dir_flag(self):
        p = build_parser()
        args = p.parse_args(["--dir", "./graphs/"])
        assert args.directory == "./graphs/"
        assert args.source is None

    def test_generate_config_flag(self):
        p = build_parser()
        args = p.parse_args(["wf.json", "--generate-config"])
        assert args.generate_config is True

    def test_docs_flag(self):
        p = build_parser()
        args = p.parse_args(["wf.json", "--docs"])
        assert args.docs is True

    def test_openapi_flag(self):
        p = build_parser()
        args = p.parse_args(["wf.json", "--openapi"])
        assert args.openapi is True

    def test_human_timeout(self):
        p = build_parser()
        args = p.parse_args(["wf.json", "--human-timeout", "60"])
        assert args.human_timeout == 60.0

    def test_llm_config(self):
        p = build_parser()
        args = p.parse_args([
            "wf.json",
            "--llm-api-key", "key123",
            "--model", "gpt-4",
            "--base-url", "http://localhost:1234/v1",
        ])
        assert args.llm_api_key == "key123"
        assert args.model == "gpt-4"
        assert args.base_url == "http://localhost:1234/v1"

    def test_host(self):
        p = build_parser()
        args = p.parse_args(["wf.json", "--host", "127.0.0.1"])
        assert args.host == "127.0.0.1"
