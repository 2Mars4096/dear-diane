"""Tests for Plan 19-6: Runtime Authoring."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from dan.meta.authoring import (
    RuntimeAuthor,
    SkillSpec,
    TestResult,
    ToolSpec,
    ToolTestCase,
    ValidationResult,
    _parse_skill_frontmatter,
)
from dan.meta.discovery import DiscoveryResult, ToolInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOOL_SPEC_JSON = json.dumps({
    "tool_id": "content_hash",
    "name": "Content Hash",
    "description": "Hash text content",
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "text to hash"}},
        "required": ["text"],
    },
    "code": (
        "import hashlib\n\n"
        "async def content_hash(text: str = '', **_kwargs) -> dict:\n"
        "    h = hashlib.sha256(text.encode())\n"
        "    return {'hash': h.hexdigest()}\n"
    ),
    "dependencies": [],
    "test_cases": [
        {"inputs": {"text": "hello"}},
    ],
    "category": "custom",
    "returns": "dict with hash",
})

_SKILL_SPEC_JSON = json.dumps({
    "skill_id": "code_reviewer",
    "name": "Code Reviewer",
    "description": "Reviews code for quality",
    "content": "## Review Guidelines\n- Check for bugs\n- Verify style",
    "target_nodes": ["llm_operator"],
    "hook_points": ["pre_prompt"],
    "tags": ["review", "quality"],
})


def _make_fake_llm(response: str):
    """Return an async callable that always returns *response*."""
    async def fake_llm(system_prompt: str, user_prompt: str, model: Any = None, temperature: float = 0.3) -> str:
        return response
    return fake_llm


def _make_counting_llm(responses: list[str]):
    """Return an async callable that cycles through responses and tracks call count."""
    state = {"idx": 0, "call_count": 0}

    async def counting_llm(system_prompt: str, user_prompt: str, model: Any = None, temperature: float = 0.3) -> str:
        state["call_count"] += 1
        resp = responses[min(state["idx"], len(responses) - 1)]
        state["idx"] += 1
        return resp

    counting_llm.state = state  # type: ignore[attr-defined]
    return counting_llm


@dataclass
class FakeSandboxResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


class FakeSandboxRunner:
    """Configurable sandbox that returns preset results per call."""

    def __init__(
        self,
        exit_code: int = 0,
        stderr: str = "",
        structured: dict[str, Any] | None = None,
    ) -> None:
        self._exit_code = exit_code
        self._stderr = stderr
        self._structured = structured if structured is not None else {"success": True, "output": {}}
        self.run_count = 0

    async def run(self, code: str, config: Any, inputs: dict | None = None) -> tuple[FakeSandboxResult, dict | None]:
        self.run_count += 1
        return FakeSandboxResult(exit_code=self._exit_code, stderr=self._stderr), self._structured


class FakeToolRegistry:
    """Minimal registry tracking registered tool ids."""

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def register(self, tool_id: str, fn: Any) -> None:
        self._tools[tool_id] = fn

    def registered_ids(self) -> list[str]:
        return list(self._tools)

    def has(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def get_metadata(self, tool_id: str) -> dict | None:
        return None


# ---------------------------------------------------------------------------
# Tests: ToolSpec
# ---------------------------------------------------------------------------


class TestToolSpec:
    def test_basic_construction(self):
        spec = ToolSpec(
            tool_id="my_tool",
            name="My Tool",
            description="Does stuff",
            parameters={"type": "object"},
            code="async def my_tool(**kw): return {}",
        )
        assert spec.tool_id == "my_tool"
        assert spec.name == "My Tool"
        assert spec.code.startswith("async")

    def test_serialization(self):
        spec = ToolSpec(
            tool_id="t", name="T", description="d",
            parameters={}, code="async def t(): pass",
        )
        data = spec.model_dump()
        assert data["tool_id"] == "t"
        assert data["category"] == "custom"

    def test_defaults(self):
        spec = ToolSpec(
            tool_id="x", name="X", description="d",
            parameters={}, code="pass",
        )
        assert spec.category == "custom"
        assert spec.dependencies == []
        assert spec.test_cases == []
        assert spec.returns == ""


# ---------------------------------------------------------------------------
# Tests: SkillSpec
# ---------------------------------------------------------------------------


class TestSkillSpec:
    def test_basic_construction(self):
        spec = SkillSpec(
            skill_id="reviewer",
            name="Code Reviewer",
            description="Reviews code",
            content="Instructions here",
        )
        assert spec.skill_id == "reviewer"
        assert spec.content == "Instructions here"

    def test_serialization(self):
        spec = SkillSpec(
            skill_id="s", name="S", description="d", content="text",
            hook_points=["pre_prompt"], tags=["quality"],
        )
        data = spec.model_dump()
        assert data["hook_points"] == ["pre_prompt"]
        assert data["tags"] == ["quality"]

    def test_defaults(self):
        spec = SkillSpec(
            skill_id="s", name="S", description="d", content="c",
        )
        assert spec.target_nodes == []
        assert spec.hook_points == []
        assert spec.tags == []


# ---------------------------------------------------------------------------
# Tests: RuntimeAuthor - Tool Pipeline
# ---------------------------------------------------------------------------


class TestRuntimeAuthorToolPipeline:
    @pytest.mark.asyncio
    async def test_generate_tool_returns_spec(self):
        author = RuntimeAuthor(llm_call=_make_fake_llm(_TOOL_SPEC_JSON))
        spec = await author.generate_tool("hash some text")
        assert isinstance(spec, ToolSpec)
        assert spec.tool_id == "content_hash"

    @pytest.mark.asyncio
    async def test_generate_tool_with_context(self):
        calls: list[str] = []

        async def tracking_llm(system_prompt: str, user_prompt: str, model: Any = None, temp: float = 0.3) -> str:
            calls.append(user_prompt)
            return _TOOL_SPEC_JSON

        context = DiscoveryResult(tools=[ToolInfo(tool_id="existing_tool")])
        author = RuntimeAuthor(llm_call=tracking_llm)
        await author.generate_tool("hash text", context=context)
        assert "existing_tool" in calls[0]

    @pytest.mark.asyncio
    async def test_test_tool_passes_exit_0(self):
        sandbox = FakeSandboxRunner(exit_code=0, structured={"success": True, "output": {}})
        author = RuntimeAuthor(sandbox=sandbox)
        spec = ToolSpec(
            tool_id="t", name="T", description="d", parameters={}, code="pass",
            test_cases=[ToolTestCase(inputs={"x": 1})],
        )
        result = await author.test_tool(spec)
        assert isinstance(result, TestResult)
        assert result.passed is True
        assert result.total == 1

    @pytest.mark.asyncio
    async def test_test_tool_fails_exit_1(self):
        sandbox = FakeSandboxRunner(exit_code=1, stderr="SyntaxError")
        author = RuntimeAuthor(sandbox=sandbox)
        spec = ToolSpec(
            tool_id="t", name="T", description="d", parameters={}, code="pass",
            test_cases=[ToolTestCase(inputs={"x": 1})],
        )
        result = await author.test_tool(spec)
        assert result.passed is False
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_test_tool_expected_error(self):
        sandbox = FakeSandboxRunner(exit_code=1, stderr="expected failure")
        author = RuntimeAuthor(sandbox=sandbox)
        spec = ToolSpec(
            tool_id="t", name="T", description="d", parameters={}, code="pass",
            test_cases=[ToolTestCase(inputs={}, expected_error=True)],
        )
        result = await author.test_tool(spec)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_test_tool_expected_error_but_succeeded(self):
        sandbox = FakeSandboxRunner(exit_code=0, structured={"success": True, "output": {}})
        author = RuntimeAuthor(sandbox=sandbox)
        spec = ToolSpec(
            tool_id="t", name="T", description="d", parameters={}, code="pass",
            test_cases=[ToolTestCase(inputs={}, expected_error=True)],
        )
        result = await author.test_tool(spec)
        assert result.passed is False
        assert any("expected error" in f for f in result.failures)

    @pytest.mark.asyncio
    async def test_test_tool_no_test_cases(self):
        author = RuntimeAuthor()
        spec = ToolSpec(
            tool_id="t", name="T", description="d", parameters={}, code="pass",
        )
        result = await author.test_tool(spec)
        assert result.passed is True
        assert result.total == 0

    def test_register_tool(self):
        registry = FakeToolRegistry()
        sandbox = FakeSandboxRunner()
        author = RuntimeAuthor(tool_registry=registry, sandbox=sandbox)
        spec = ToolSpec(
            tool_id="my_tool", name="My Tool", description="d",
            parameters={}, code="async def my_tool(**kw): return {}",
        )
        author.register_tool(spec)
        assert "my_tool" in registry.registered_ids()

    def test_register_tool_no_registry_raises(self):
        author = RuntimeAuthor()
        spec = ToolSpec(
            tool_id="t", name="T", description="d", parameters={}, code="pass",
        )
        with pytest.raises(RuntimeError, match="No ToolRegistry"):
            author.register_tool(spec)

    def test_persist_tool_writes_file(self, tmp_path: Path):
        author = RuntimeAuthor()
        spec = ToolSpec(
            tool_id="content_hash",
            name="Content Hash",
            description="Hash text",
            parameters={"type": "object"},
            code="async def content_hash(**kw): return {}",
            test_cases=[ToolTestCase(inputs={"text": "hi"}, expected_output={"hash": "abc"})],
            category="custom",
            returns="dict",
        )
        filepath = author.persist_tool(spec, tmp_path)
        assert filepath.exists()
        assert filepath.suffix == ".py"
        content = filepath.read_text(encoding="utf-8")
        assert "TOOL_METADATA" in content
        assert "content_hash" in content

    def test_persist_tool_metadata_keys(self, tmp_path: Path):
        author = RuntimeAuthor()
        spec = ToolSpec(
            tool_id="my_tool",
            name="My Tool",
            description="test",
            parameters={"type": "object"},
            code="async def my_tool(**kw): return {}",
            category="custom",
            returns="dict",
        )
        filepath = author.persist_tool(spec, tmp_path)
        content = filepath.read_text(encoding="utf-8")
        assert "tool_id" in content
        assert "description" in content
        assert "parameters" in content
        assert "category" in content

    @pytest.mark.asyncio
    async def test_create_tool_full_pipeline(self):
        sandbox = FakeSandboxRunner(exit_code=0, structured={"success": True, "output": {}})
        registry = FakeToolRegistry()
        author = RuntimeAuthor(
            llm_call=_make_fake_llm(_TOOL_SPEC_JSON),
            sandbox=sandbox,
            tool_registry=registry,
        )
        spec = await author.create_tool("hash content")
        assert isinstance(spec, ToolSpec)
        assert "content_hash" in registry.registered_ids()

    @pytest.mark.asyncio
    async def test_create_tool_retries_on_failure(self):
        fail_sandbox_result = (FakeSandboxResult(exit_code=1, stderr="err"), None)
        pass_sandbox_result = (FakeSandboxResult(exit_code=0), {"success": True, "output": {}})

        call_num = {"n": 0}

        class RetrySandbox:
            async def run(self, code: str, config: Any, inputs: dict | None = None):
                call_num["n"] += 1
                if call_num["n"] <= 1:
                    return fail_sandbox_result
                return pass_sandbox_result

        llm = _make_counting_llm([_TOOL_SPEC_JSON] * 5)
        registry = FakeToolRegistry()
        author = RuntimeAuthor(
            llm_call=llm,
            sandbox=RetrySandbox(),
            tool_registry=registry,
            max_retries=3,
        )
        spec = await author.create_tool("hash content")
        assert isinstance(spec, ToolSpec)
        assert llm.state["call_count"] >= 2

    @pytest.mark.asyncio
    async def test_create_tool_all_retries_fail(self):
        sandbox = FakeSandboxRunner(exit_code=1, stderr="always fails")
        sandbox._structured = None  # no structured output
        registry = FakeToolRegistry()
        author = RuntimeAuthor(
            llm_call=_make_fake_llm(_TOOL_SPEC_JSON),
            sandbox=sandbox,
            tool_registry=registry,
            max_retries=1,
        )
        with pytest.raises(RuntimeError, match="Failed to create tool"):
            await author.create_tool("hash content")

    @pytest.mark.asyncio
    async def test_create_tool_persists_when_dir_given(self, tmp_path: Path):
        sandbox = FakeSandboxRunner(exit_code=0, structured={"success": True, "output": {}})
        registry = FakeToolRegistry()
        author = RuntimeAuthor(
            llm_call=_make_fake_llm(_TOOL_SPEC_JSON),
            sandbox=sandbox,
            tool_registry=registry,
        )
        spec = await author.create_tool("hash", target_dir=tmp_path)
        assert (tmp_path / f"{spec.tool_id}.py").exists()


# ---------------------------------------------------------------------------
# Tests: RuntimeAuthor - Skill Pipeline
# ---------------------------------------------------------------------------


class TestRuntimeAuthorSkillPipeline:
    @pytest.mark.asyncio
    async def test_generate_skill_returns_spec(self):
        author = RuntimeAuthor(llm_call=_make_fake_llm(_SKILL_SPEC_JSON))
        spec = await author.generate_skill("review code quality")
        assert isinstance(spec, SkillSpec)
        assert spec.skill_id == "code_reviewer"

    def test_validate_skill_passes_valid(self):
        author = RuntimeAuthor()
        spec = SkillSpec(
            skill_id="s", name="S", description="d",
            content="Instructions", hook_points=["pre_prompt"],
        )
        result = author.validate_skill(spec)
        assert isinstance(result, ValidationResult)
        assert result.valid is True

    def test_validate_skill_fails_empty_content(self):
        author = RuntimeAuthor()
        spec = SkillSpec(
            skill_id="s", name="S", description="d", content="",
        )
        result = author.validate_skill(spec)
        assert result.valid is False
        assert any("non-empty" in e for e in result.errors)

    def test_validate_skill_fails_invalid_hook(self):
        author = RuntimeAuthor()
        spec = SkillSpec(
            skill_id="s", name="S", description="d",
            content="Valid content", hook_points=["invalid_hook"],
        )
        result = author.validate_skill(spec)
        assert result.valid is False
        assert any("Invalid hook_point" in e for e in result.errors)

    def test_validate_skill_fails_missing_id(self):
        author = RuntimeAuthor()
        spec = SkillSpec(
            skill_id="", name="S", description="d", content="Valid",
        )
        result = author.validate_skill(spec)
        assert result.valid is False
        assert any("skill_id" in e for e in result.errors)

    def test_persist_skill_writes_md(self, tmp_path: Path):
        author = RuntimeAuthor()
        spec = SkillSpec(
            skill_id="reviewer",
            name="Code Reviewer",
            description="Reviews code",
            content="Check for bugs",
            hook_points=["pre_prompt"],
            tags=["quality"],
            target_nodes=["llm_operator"],
        )
        filepath = author.persist_skill(spec, tmp_path)
        assert filepath.exists()
        assert filepath.suffix == ".md"
        content = filepath.read_text(encoding="utf-8")
        assert "---" in content
        assert "Code Reviewer" in content
        assert "Check for bugs" in content
        assert "pre_prompt" in content

    def test_activate_skill(self):
        from dan.server.skill_library import SKILL_LIBRARY

        author = RuntimeAuthor()
        spec = SkillSpec(
            skill_id="test_skill_19_6",
            name="Test Skill",
            description="For testing",
            content="Test instructions",
            tags=["test"],
        )
        author.activate_skill(spec)
        assert "test_skill_19_6" in SKILL_LIBRARY
        entry = SKILL_LIBRARY["test_skill_19_6"]
        assert entry["name"] == "Test Skill"
        assert entry["text"] == "Test instructions"
        del SKILL_LIBRARY["test_skill_19_6"]

    @pytest.mark.asyncio
    async def test_create_skill_full_pipeline(self, tmp_path: Path):
        from dan.server.skill_library import SKILL_LIBRARY

        author = RuntimeAuthor(llm_call=_make_fake_llm(_SKILL_SPEC_JSON))
        spec = await author.create_skill("review code", target_dir=tmp_path)
        assert isinstance(spec, SkillSpec)
        assert spec.skill_id in SKILL_LIBRARY
        assert (tmp_path / f"{spec.skill_id}.md").exists()
        del SKILL_LIBRARY[spec.skill_id]

    @pytest.mark.asyncio
    async def test_create_skill_invalid_raises(self):
        invalid_json = json.dumps({
            "skill_id": "",
            "name": "Bad",
            "description": "d",
            "content": "",
        })
        author = RuntimeAuthor(llm_call=_make_fake_llm(invalid_json))
        with pytest.raises(ValueError, match="validation"):
            await author.create_skill("bad skill")


# ---------------------------------------------------------------------------
# Tests: Custom Discovery
# ---------------------------------------------------------------------------


class TestCustomDiscovery:
    def test_discover_custom_tools_finds_tools(self, tmp_path: Path):
        tool_code = (
            'TOOL_METADATA = {\n'
            '    "tool_id": "greet",\n'
            '    "description": "Say hello",\n'
            '    "parameters": {},\n'
            '    "examples": [],\n'
            '    "category": "custom",\n'
            '    "returns": "str",\n'
            '}\n\n'
            'async def greet(**kw):\n'
            '    return "hello"\n'
        )
        (tmp_path / "greet.py").write_text(tool_code, encoding="utf-8")
        results = RuntimeAuthor.discover_custom_tools(tmp_path)
        assert len(results) == 1
        tool_id, fn, meta = results[0]
        assert tool_id == "greet"
        assert callable(fn)
        assert meta["description"] == "Say hello"

    def test_discover_custom_tools_ignores_no_metadata(self, tmp_path: Path):
        (tmp_path / "bare.py").write_text("x = 1\n", encoding="utf-8")
        results = RuntimeAuthor.discover_custom_tools(tmp_path)
        assert len(results) == 0

    def test_discover_custom_tools_ignores_underscored(self, tmp_path: Path):
        (tmp_path / "_private.py").write_text("TOOL_METADATA = {}\n", encoding="utf-8")
        results = RuntimeAuthor.discover_custom_tools(tmp_path)
        assert len(results) == 0

    def test_discover_custom_tools_missing_dir(self, tmp_path: Path):
        results = RuntimeAuthor.discover_custom_tools(tmp_path / "nonexistent")
        assert results == []

    def test_register_custom_tools(self, tmp_path: Path):
        tool_code = (
            'TOOL_METADATA = {\n'
            '    "tool_id": "adder",\n'
            '    "description": "Add numbers",\n'
            '    "parameters": {},\n'
            '    "examples": [],\n'
            '    "category": "custom",\n'
            '    "returns": "int",\n'
            '}\n\n'
            'async def adder(**kw):\n'
            '    return 42\n'
        )
        (tmp_path / "adder.py").write_text(tool_code, encoding="utf-8")
        registry = FakeToolRegistry()
        registered = RuntimeAuthor.register_custom_tools(tmp_path, registry)
        assert "adder" in registered
        assert registry.has("adder")

    def test_register_custom_tools_skips_already_registered(self, tmp_path: Path):
        tool_code = (
            'TOOL_METADATA = {\n'
            '    "tool_id": "dup",\n'
            '    "description": "Dup",\n'
            '    "parameters": {},\n'
            '    "examples": [],\n'
            '    "category": "custom",\n'
            '    "returns": "str",\n'
            '}\n\n'
            'async def dup(**kw):\n'
            '    return ""\n'
        )
        (tmp_path / "dup.py").write_text(tool_code, encoding="utf-8")
        registry = FakeToolRegistry()
        registry.register("dup", lambda: None)
        registered = RuntimeAuthor.register_custom_tools(tmp_path, registry)
        assert registered == []

    def test_discover_custom_skills_finds_md(self, tmp_path: Path):
        skill_md = (
            "---\n"
            "name: My Skill\n"
            "description: Does stuff\n"
            "tags: [quality, review]\n"
            "hook_points: [pre_prompt]\n"
            "---\n\n"
            "## Instructions\nDo the thing.\n"
        )
        (tmp_path / "my_skill.md").write_text(skill_md, encoding="utf-8")
        results = RuntimeAuthor.discover_custom_skills(tmp_path)
        assert len(results) == 1
        assert results[0]["name"] == "My Skill"
        assert results[0]["description"] == "Does stuff"

    def test_discover_custom_skills_parses_frontmatter(self, tmp_path: Path):
        skill_md = (
            "---\n"
            "name: Tester\n"
            "description: Test things\n"
            "tags: [test, ci]\n"
            "inject_as: system\n"
            "hook_points: [pre_prompt, post_output]\n"
            "target_nodes: [llm_operator]\n"
            "---\n\n"
            "Body text here.\n"
        )
        (tmp_path / "tester.md").write_text(skill_md, encoding="utf-8")
        results = RuntimeAuthor.discover_custom_skills(tmp_path)
        desc = results[0]
        assert desc["tags"] == ["test", "ci"]
        assert desc["hook_points"] == ["pre_prompt", "post_output"]
        assert desc["target_nodes"] == ["llm_operator"]
        assert desc["text"] == "Body text here."

    def test_discover_custom_skills_no_frontmatter(self, tmp_path: Path):
        (tmp_path / "plain.md").write_text("Just text, no frontmatter.\n", encoding="utf-8")
        results = RuntimeAuthor.discover_custom_skills(tmp_path)
        assert len(results) == 0

    def test_discover_custom_skills_missing_dir(self, tmp_path: Path):
        results = RuntimeAuthor.discover_custom_skills(tmp_path / "nonexistent")
        assert results == []


class TestParseSkillFrontmatter:
    def test_valid_frontmatter(self):
        text = "---\nname: Skill\ndescription: A skill\ntags: [a, b]\n---\n\nBody"
        result = _parse_skill_frontmatter(text, "fallback")
        assert result is not None
        assert result["name"] == "Skill"
        assert result["tags"] == ["a", "b"]
        assert result["text"] == "Body"

    def test_no_frontmatter(self):
        result = _parse_skill_frontmatter("No yaml here", "fallback")
        assert result is None

    def test_fallback_id(self):
        text = "---\ndescription: Minimal\n---\n\nBody"
        result = _parse_skill_frontmatter(text, "my_fallback")
        assert result is not None
        assert result["skill_id"] == "my_fallback"
        assert result["name"] == "my_fallback"
