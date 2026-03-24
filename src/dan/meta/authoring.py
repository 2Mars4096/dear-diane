"""Runtime Authoring — dynamic tool and skill creation for the meta-orchestrator.

Implements Plan 19-6: when the planner needs a capability not covered by
built-in tools or existing skills, ``RuntimeAuthor`` generates it via LLM,
tests it in the sandbox, registers it in ``ToolRegistry``, and persists it
to disk for auto-discovery on restart.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, MutableMapping

from pydantic import BaseModel, Field

from dan.meta.discovery import DiscoveryResult

if TYPE_CHECKING:
    from dan.executors.tool import ToolRegistry
    from dan.sandbox.runner import SandboxRunner

logger = logging.getLogger(__name__)

__all__ = [
    "RuntimeAuthor",
    "SkillSpec",
    "ToolTestResult",
    "ToolSpec",
    "ToolTestCase",
    "ValidationResult",
]


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class ToolTestCase(BaseModel):
    """A test case for a generated tool."""

    inputs: dict[str, Any]
    expected_output: Any = None
    expected_error: bool = False


class ToolSpec(BaseModel):
    """Specification for a dynamically generated tool."""

    tool_id: str
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    code: str  # Python source — imports + async function definition
    dependencies: list[str] = Field(default_factory=list)
    test_cases: list[ToolTestCase] = Field(default_factory=list)
    category: str = "custom"
    returns: str = ""


class ToolTestResult(BaseModel):
    """Result of testing a generated tool."""

    passed: bool
    total: int = 0
    failures: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SkillSpec(BaseModel):
    """Specification for a dynamically generated skill."""

    skill_id: str
    name: str
    description: str
    content: str  # markdown body
    target_nodes: list[str] = Field(default_factory=list)
    hook_points: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    """Result of validating a skill spec."""

    valid: bool
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_HOOK_POINTS = frozenset({"pre_prompt", "post_output", "validation", "tool_call"})

_REQUIRED_METADATA_KEYS = frozenset(
    {"tool_id", "description", "parameters", "examples", "category", "returns"}
)

_TOOL_GEN_SYSTEM_PROMPT = """\
You are a tool author for the DAN (Deep Agent Network) system.

Given a natural language description of what the tool should do, produce a JSON
object matching this schema:

{
  "tool_id": "snake_case_name",
  "name": "Human Readable Name",
  "description": "What this tool does",
  "parameters": {
    "type": "object",
    "properties": {
      "param_name": {"type": "string", "description": "..."}
    },
    "required": ["param_name"]
  },
  "code": "<see rules below>",
  "dependencies": [],
  "test_cases": [
    {"inputs": {"param_name": "value"}, "expected_output": {"key": "value"}},
    {"inputs": {"bad_param": "x"}, "expected_error": true}
  ],
  "category": "custom",
  "returns": "Description of the return value"
}

## Example tool code (the `code` field)

import hashlib

async def content_hash(text: str = "", algorithm: str = "sha256", **_kwargs) -> dict:
    h = hashlib.new(algorithm)
    h.update(text.encode("utf-8"))
    return {"hash": h.hexdigest(), "algorithm": algorithm, "length": len(text)}

## Rules

- The `code` field must define ONE async function whose name matches `tool_id`.
- The function must accept **kwargs (or explicit params + **_kwargs) and return a dict.
- Place any needed imports at the TOP of the code block, before the function.
- Do NOT include `from __future__ import annotations` — the host module provides it.
- Do NOT use external packages unless listed in `dependencies`.
- Provide at least 2 test cases: one success, one error/edge case.
- The function must be self-contained — no references to modules beyond stdlib.
- Output ONLY a single valid JSON object. No markdown fences, no explanation."""

_SKILL_GEN_SYSTEM_PROMPT = """\
You are a skill author for the DAN (Deep Agent Network) system.

Skills are prompt-prefix text blocks injected into LLM nodes to add domain
expertise. Given a natural language description, produce a JSON object:

{
  "skill_id": "snake_case_name",
  "name": "Human Readable Skill Name",
  "description": "What this skill does",
  "content": "The markdown body — instructions, guidelines, constraints...",
  "target_nodes": ["llm_operator"],
  "hook_points": ["pre_prompt"],
  "tags": ["tag1", "tag2"]
}

## Rules

- `content` is the actual skill text injected into LLM system prompts.
- `hook_points` must be from: pre_prompt, post_output, validation, tool_call.
- `target_nodes` lists node types this applies to (e.g. llm_operator, code_operator).
- `tags` enable auto-attachment to nodes with matching tags.
- Make content substantive and actionable — concrete instructions, not platitudes.
- Output ONLY a single valid JSON object. No markdown fences, no explanation."""

_TEST_HARNESS = """\
import asyncio
import json
import sys
from pathlib import Path

_inputs = json.loads(Path("_inputs.json").read_text(encoding="utf-8"))

# --- tool code ---
{tool_code}
# --- end tool code ---

async def _run_test():
    fn = {fn_name}
    kwargs = _inputs.get("test_inputs", {{}})
    try:
        result = await fn(**kwargs)
        Path("_result.json").write_text(
            json.dumps({{"success": True, "output": result}}, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        Path("_result.json").write_text(
            json.dumps({{"success": False, "error": str(exc), "error_type": type(exc).__name__}}),
            encoding="utf-8",
        )

asyncio.run(_run_test())
"""


# ---------------------------------------------------------------------------
# RuntimeAuthor
# ---------------------------------------------------------------------------


class RuntimeAuthor:
    """Generates, tests, registers, and persists tools and skills at runtime."""

    def __init__(
        self,
        llm_call: Callable[..., Awaitable[str]] | None = None,
        sandbox: SandboxRunner | None = None,
        tool_registry: ToolRegistry | None = None,
        skill_library: MutableMapping[str, dict[str, Any]] | None = None,
        model: str | None = None,
        max_retries: int = 3,
    ) -> None:
        self._llm_call = llm_call
        self._sandbox = sandbox
        self._tool_registry = tool_registry
        self._skill_library = skill_library
        self._model = model
        self._max_retries = max_retries

    # -- internal helpers --------------------------------------------------

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        if self._llm_call is None:
            raise RuntimeError("No LLM callable configured for RuntimeAuthor")
        return await self._llm_call(
            system_prompt, user_prompt, self._model, 0.3,
        )

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        """Extract JSON from LLM output, handling optional markdown fences."""
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        text = match.group(1) if match else raw.strip()
        return json.loads(text)

    def _get_sandbox(self) -> SandboxRunner:
        if self._sandbox is not None:
            return self._sandbox
        from dan.sandbox.runner import SandboxRunner as _SR

        return _SR()

    # ===================================================================
    # Tool authoring
    # ===================================================================

    async def generate_tool(
        self, intent: str, context: DiscoveryResult | None = None,
    ) -> ToolSpec:
        """LLM generates a tool spec from natural language description."""
        parts = [f"Create a tool for the following purpose:\n\n{intent}"]

        if context and context.tools:
            parts.append(
                "\n\nExisting tools (avoid duplicating): "
                + ", ".join(t.tool_id for t in context.tools)
            )

        raw = await self._call_llm(_TOOL_GEN_SYSTEM_PROMPT, "".join(parts))
        try:
            data = self._parse_json(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"LLM produced invalid JSON for tool spec: {exc}"
            ) from exc

        return ToolSpec.model_validate(data)

    async def test_tool(self, spec: ToolSpec) -> ToolTestResult:
        """Run test cases through SandboxRunner."""
        if not spec.test_cases:
            return ToolTestResult(passed=True, total=0)

        from dan.sandbox import SandboxConfig

        sandbox = self._get_sandbox()
        config = SandboxConfig(language="python", timeout_seconds=30)

        harness = _TEST_HARNESS.format(
            tool_code=spec.code,
            fn_name=spec.tool_id,
        )

        failures: list[str] = []
        errors: list[str] = []

        for idx, tc in enumerate(spec.test_cases):
            try:
                result, structured = await sandbox.run(
                    harness, config, {"test_inputs": tc.inputs},
                )
            except Exception as exc:
                errors.append(f"Test {idx}: sandbox error: {exc}")
                continue

            if result.exit_code != 0:
                if tc.expected_error:
                    continue
                msg = result.stderr[:500] if result.stderr else "no stderr"
                errors.append(f"Test {idx}: exit code {result.exit_code}: {msg}")
                continue

            if structured is None:
                errors.append(f"Test {idx}: no structured output produced")
                continue

            success = structured.get("success", False)

            if tc.expected_error:
                if success:
                    failures.append(
                        f"Test {idx}: expected error but tool succeeded"
                    )
                continue

            if not success:
                failures.append(
                    f"Test {idx}: tool failed: "
                    f"{structured.get('error', 'unknown')}"
                )
                continue

            if tc.expected_output is not None:
                actual = structured.get("output")
                if actual != tc.expected_output:
                    failures.append(
                        f"Test {idx}: expected {tc.expected_output!r}, "
                        f"got {actual!r}"
                    )

        total = len(spec.test_cases)
        return ToolTestResult(
            passed=not failures and not errors,
            total=total,
            failures=failures,
            errors=errors,
        )

    def register_tool(self, spec: ToolSpec) -> None:
        """Register tool in ToolRegistry with a sandbox-wrapping callable."""
        if self._tool_registry is None:
            raise RuntimeError("No ToolRegistry configured for RuntimeAuthor")

        sandbox = self._get_sandbox()
        tool_code = spec.code
        tool_id = spec.tool_id

        async def _sandbox_wrapper(**kwargs: Any) -> Any:
            from dan.sandbox import SandboxConfig

            harness = _TEST_HARNESS.format(tool_code=tool_code, fn_name=tool_id)
            cfg = SandboxConfig(language="python", timeout_seconds=30)
            result, structured = await sandbox.run(
                harness, cfg, {"test_inputs": kwargs},
            )

            if result.exit_code != 0:
                raise RuntimeError(
                    f"Tool '{tool_id}' execution failed "
                    f"(exit {result.exit_code}): "
                    f"{(result.stderr or '')[:1000]}"
                )
            if structured is None:
                raise RuntimeError(f"Tool '{tool_id}' produced no output")
            if not structured.get("success", False):
                raise RuntimeError(
                    f"Tool '{tool_id}' error: "
                    f"{structured.get('error', 'unknown')}"
                )
            return structured.get("output")

        self._tool_registry.register(spec.tool_id, _sandbox_wrapper)
        logger.info("Registered runtime tool '%s'", spec.tool_id)

    def persist_tool(self, spec: ToolSpec, target_dir: Path) -> Path:
        """Write tool as a Python module file following ``dan.tools`` convention."""
        target_dir.mkdir(parents=True, exist_ok=True)
        filepath = target_dir / f"{spec.tool_id}.py"

        examples: list[dict[str, Any]] = []
        for tc in spec.test_cases:
            if not tc.expected_error and tc.expected_output is not None:
                examples.append({"input": tc.inputs, "output": tc.expected_output})
        if not examples and spec.test_cases:
            examples.append({"input": spec.test_cases[0].inputs, "output": "..."})

        metadata = {
            "tool_id": spec.tool_id,
            "description": spec.description,
            "parameters": spec.parameters,
            "examples": examples,
            "category": spec.category,
            "returns": spec.returns or "dict",
        }

        module_source = (
            f'"""Custom tool: {spec.name}"""\n'
            "\n"
            "from __future__ import annotations\n"
            "\n"
            "from typing import Any\n"
            "\n"
            f"TOOL_METADATA = {json.dumps(metadata, indent=4)}\n"
            "\n"
            "\n"
            f"{spec.code}\n"
        )

        filepath.write_text(module_source, encoding="utf-8")
        logger.info("Persisted tool '%s' to %s", spec.tool_id, filepath)
        return filepath

    async def create_tool(
        self,
        intent: str,
        context: DiscoveryResult | None = None,
        target_dir: Path | None = None,
    ) -> ToolSpec:
        """Full pipeline: generate -> test (with retry) -> register -> persist."""
        last_errors = ""

        for attempt in range(self._max_retries + 1):
            effective_intent = intent
            if last_errors and attempt > 0:
                effective_intent = (
                    f"{intent}\n\n"
                    f"Previous attempt failed with these errors:\n{last_errors}\n"
                    "Please fix the issues and try again."
                )

            spec = await self.generate_tool(effective_intent, context)
            test_result = await self.test_tool(spec)

            if test_result.passed:
                self.register_tool(spec)
                if target_dir is not None:
                    self.persist_tool(spec, target_dir)
                logger.info(
                    "Tool '%s' created successfully (attempt %d/%d)",
                    spec.tool_id,
                    attempt + 1,
                    self._max_retries + 1,
                )
                return spec

            last_errors = "; ".join(test_result.failures + test_result.errors)
            logger.warning(
                "Tool generation attempt %d/%d failed: %s",
                attempt + 1,
                self._max_retries + 1,
                last_errors[:500],
            )

        raise RuntimeError(
            f"Failed to create tool after {self._max_retries + 1} attempts. "
            f"Last errors: {last_errors}"
        )

    # ===================================================================
    # Skill authoring
    # ===================================================================

    async def generate_skill(
        self, intent: str, context: DiscoveryResult | None = None,
    ) -> SkillSpec:
        """LLM generates skill markdown following hyperedge format."""
        parts = [f"Create a skill for the following purpose:\n\n{intent}"]

        if context and context.skills:
            parts.append(
                "\n\nExisting skills (avoid duplicating): "
                + ", ".join(s.name for s in context.skills)
            )

        raw = await self._call_llm(_SKILL_GEN_SYSTEM_PROMPT, "".join(parts))
        try:
            data = self._parse_json(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"LLM produced invalid JSON for skill spec: {exc}"
            ) from exc

        return SkillSpec.model_validate(data)

    def validate_skill(self, spec: SkillSpec) -> ValidationResult:
        """Check frontmatter structure, hook points, target compatibility."""
        errs: list[str] = []

        if not spec.skill_id:
            errs.append("skill_id is required")
        if not spec.name:
            errs.append("name is required")
        if not spec.content or not spec.content.strip():
            errs.append("content must be non-empty")

        for hp in spec.hook_points:
            if hp not in _VALID_HOOK_POINTS:
                errs.append(
                    f"Invalid hook_point '{hp}'. "
                    f"Valid: {sorted(_VALID_HOOK_POINTS)}"
                )

        return ValidationResult(valid=not errs, errors=errs)

    def persist_skill(self, spec: SkillSpec, target_dir: Path) -> Path:
        """Write ``.md`` file to skills directory."""
        target_dir.mkdir(parents=True, exist_ok=True)
        filepath = target_dir / f"{spec.skill_id}.md"

        tags_str = ", ".join(spec.tags) if spec.tags else ""
        hook_str = (
            ", ".join(spec.hook_points) if spec.hook_points else "pre_prompt"
        )
        targets_str = (
            ", ".join(spec.target_nodes) if spec.target_nodes else ""
        )

        frontmatter = (
            "---\n"
            f"name: {spec.name}\n"
            f"description: {spec.description}\n"
            f"tags: [{tags_str}]\n"
            "inject_as: system\n"
            f"hook_points: [{hook_str}]\n"
            f"target_nodes: [{targets_str}]\n"
            "---\n"
        )

        filepath.write_text(
            f"{frontmatter}\n{spec.content}\n", encoding="utf-8",
        )
        logger.info("Persisted skill '%s' to %s", spec.skill_id, filepath)
        return filepath

    def activate_skill(self, spec: SkillSpec) -> None:
        """Add to the configured skill library for immediate discoverability."""
        if self._skill_library is None:
            raise RuntimeError("No skill library configured for RuntimeAuthor")

        self._skill_library[spec.skill_id] = {
            "name": spec.name,
            "description": spec.description,
            "tags": spec.tags,
            "inject_as": "system",
            "text": spec.content,
        }
        logger.info("Activated skill '%s' in configured skill library", spec.skill_id)

    async def create_skill(
        self,
        intent: str,
        context: DiscoveryResult | None = None,
        target_dir: Path | None = None,
    ) -> SkillSpec:
        """Full pipeline: generate -> validate -> persist -> activate."""
        spec = await self.generate_skill(intent, context)
        validation = self.validate_skill(spec)

        if not validation.valid:
            raise ValueError(
                "Generated skill failed validation: "
                + "; ".join(validation.errors)
            )

        if target_dir is not None:
            self.persist_skill(spec, target_dir)

        self.activate_skill(spec)
        logger.info("Skill '%s' created and activated", spec.skill_id)
        return spec

    # ===================================================================
    # Custom artifact discovery
    # ===================================================================

    @staticmethod
    def discover_custom_tools(
        custom_dir: Path,
    ) -> list[tuple[str, Any, dict]]:
        """Scan directory for tool modules, return ``[(tool_id, fn, metadata)]``."""
        if not custom_dir.is_dir():
            return []

        results: list[tuple[str, Any, dict]] = []

        for py_file in sorted(custom_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue

            module_name = f"dan_custom_tools_{py_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                logger.warning("Cannot create module spec for: %s", py_file)
                continue

            try:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
            except Exception:
                logger.warning(
                    "Failed to import tool module '%s'", py_file, exc_info=True,
                )
                continue

            metadata = getattr(mod, "TOOL_METADATA", None)
            if not isinstance(metadata, dict):
                logger.debug("Skipping '%s' — no TOOL_METADATA dict", py_file)
                continue

            missing = _REQUIRED_METADATA_KEYS - set(metadata)
            if missing:
                logger.warning(
                    "Skipping '%s' — TOOL_METADATA missing keys: %s",
                    py_file,
                    missing,
                )
                continue

            tool_id = metadata["tool_id"]
            fn = getattr(mod, tool_id, None)
            if fn is None or not callable(fn):
                logger.warning(
                    "Skipping '%s' — no callable named '%s'",
                    py_file,
                    tool_id,
                )
                continue

            results.append((tool_id, fn, metadata))

        return results

    @staticmethod
    def register_custom_tools(
        custom_dir: Path, registry: ToolRegistry,
    ) -> list[str]:
        """Discover and register all tools in a directory.

        Returns the list of newly registered tool_ids.
        """
        discovered = RuntimeAuthor.discover_custom_tools(custom_dir)
        registered: list[str] = []

        for tool_id, fn, _metadata in discovered:
            if not registry.has(tool_id):
                registry.register(tool_id, fn)
                registered.append(tool_id)
                logger.info("Registered custom tool '%s'", tool_id)
            else:
                logger.debug(
                    "Custom tool '%s' already registered, skipping", tool_id,
                )

        return registered

    @staticmethod
    def discover_custom_skills(
        custom_dir: Path,
    ) -> list[dict[str, Any]]:
        """Scan directory for skill ``.md`` files, return parsed descriptors."""
        if not custom_dir.is_dir():
            return []

        results: list[dict[str, Any]] = []

        for md_file in sorted(custom_dir.glob("*.md")):
            if md_file.name.startswith("_"):
                continue
            try:
                text = md_file.read_text(encoding="utf-8")
            except OSError:
                logger.warning("Failed to read skill file: %s", md_file)
                continue

            descriptor = _parse_skill_frontmatter(text, md_file.stem)
            if descriptor is not None:
                results.append(descriptor)

        return results


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _parse_skill_frontmatter(
    text: str, fallback_id: str,
) -> dict[str, Any] | None:
    """Parse YAML-like frontmatter from a skill markdown file."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not match:
        logger.debug("No frontmatter found in skill file '%s'", fallback_id)
        return None

    raw_fm = match.group(1)
    body = match.group(2).strip()

    fm: dict[str, Any] = {}
    for line in raw_fm.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        bracket_match = re.match(r"^\[(.*)\]$", value)
        if bracket_match:
            items = [
                item.strip()
                for item in bracket_match.group(1).split(",")
                if item.strip()
            ]
            fm[key] = items
        else:
            fm[key] = value

    return {
        "skill_id": fm.get("skill_id", fallback_id),
        "name": fm.get("name", fallback_id),
        "description": fm.get("description", ""),
        "tags": fm.get("tags", []),
        "inject_as": fm.get("inject_as", "system"),
        "text": body,
        "hook_points": fm.get("hook_points", ["pre_prompt"]),
        "target_nodes": fm.get("target_nodes", []),
    }
