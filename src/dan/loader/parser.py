"""Markdown agent/workflow file parser.

Extracts structured ``AgentSpec`` and ``WorkflowSpec`` models from markdown
files with YAML frontmatter.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from dan.loader.models import (
    AgentSpec,
    ContextSpec,
    HyperedgeSpec,
    PortSpec,
    SourceLocation,
    WorkflowSpec,
)


class ParseError(Exception):
    """Raised on unrecoverable parse errors."""


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

_PORT_ENTRY_RE = re.compile(
    r"(\w+)"
    r"(?:\s*\(\s*"
    r"(?:schema:\s*(.+?)|(.+?))"
    r"\s*\))?"
)

_SECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)

_AGENT_LINK_RE = re.compile(r"-\s*\[([^\]]+)\]\(([^)]+)\)")

_BLOCKQUOTE_PORT_RE = re.compile(
    r"^>\s*(accepts|returns)\s*:\s*(.+)$", re.IGNORECASE
)

_CONTEXT_LINE_RE = re.compile(
    r"-\s+(\w+)"
    r"(?::\s*(.*?))?"
    r"(?:\s*\(mode:\s*(read|write|append)\))?"
    r"\s*$",
)

_FENCED_CODE_RE = re.compile(r"```(\w+)?\s*\n(.*?)```", re.DOTALL)

_AGENT_FM_DIRECT = frozenset({
    "model", "temperature", "max_tokens", "system_prompt",
    "output_schema", "retry_policy", "tool_id", "tool_config",
    "route_descriptions", "timeout_seconds", "default_action", "language",
})

# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str, int]:
    """Returns ``(frontmatter_dict, remaining_body, body_start_line)``."""
    if not text.startswith("---"):
        return {}, text, 1

    lines = text.split("\n")
    end: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break

    if end is None:
        return {}, text, 1

    yaml_text = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :])
    body_start_line = end + 2  # 1-indexed, after the closing ---

    try:
        fm = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ParseError(f"Malformed YAML frontmatter: {exc}") from exc

    if not isinstance(fm, dict):
        fm = {}

    return fm, body, body_start_line


# ---------------------------------------------------------------------------
# Port parsing
# ---------------------------------------------------------------------------


def _parse_port_list(
    text: str, source: SourceLocation | None = None,
) -> list[PortSpec]:
    """Parse ``topic (string), outline (schema: path)`` into PortSpec list."""
    ports: list[PortSpec] = []
    for entry in text.split(","):
        entry = entry.strip()
        if not entry:
            continue
        m = _PORT_ENTRY_RE.match(entry)
        if not m:
            continue
        schema_path = m.group(2)
        type_ann = m.group(3) or "string"
        if schema_path:
            type_ann = "string"
        ports.append(
            PortSpec(
                name=m.group(1),
                type_annotation=type_ann.strip(),
                schema_path=schema_path.strip() if schema_path else None,
                source=source,
            )
        )
    return ports


def _extract_blockquote_ports(
    body: str, file_path: Path, body_start_line: int,
) -> tuple[list[PortSpec], list[PortSpec], str]:
    """Extract ``> Accepts:`` / ``> Returns:`` blockquotes.

    Returns ``(input_ports, output_ports, remaining_body)``.
    """
    inputs: list[PortSpec] = []
    outputs: list[PortSpec] = []
    remaining: list[str] = []
    for i, line in enumerate(body.splitlines()):
        m = _BLOCKQUOTE_PORT_RE.match(line)
        if m:
            src = SourceLocation(file=file_path, line=body_start_line + i)
            ports = _parse_port_list(m.group(2), source=src)
            if m.group(1).lower() == "accepts":
                inputs.extend(ports)
            else:
                outputs.extend(ports)
        else:
            remaining.append(line)
    return inputs, outputs, "\n".join(remaining)


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------


def _extract_sections(body: str) -> dict[str, tuple[str, int]]:
    """Map ``## Heading`` names (lowered) to ``(content, line_offset_in_body)``."""
    headings = list(_SECTION_RE.finditer(body))
    sections: dict[str, tuple[str, int]] = {}
    for i, m in enumerate(headings):
        name = m.group(1).strip().lower()
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        line_offset = body[: m.start()].count("\n")
        sections[name] = (body[start:end].strip(), line_offset)
    return sections


def _extract_prompt_body(body: str, excluded: set[str]) -> str:
    """Return body text outside any ``## Section`` whose name is in *excluded*."""
    headings = list(_SECTION_RE.finditer(body))
    if not headings:
        return body.strip()

    exclude_ranges: list[tuple[int, int]] = []
    for i, m in enumerate(headings):
        if m.group(1).strip().lower() in excluded:
            end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
            exclude_ranges.append((m.start(), end))

    parts: list[str] = []
    pos = 0
    for rng_start, rng_end in exclude_ranges:
        if pos < rng_start:
            parts.append(body[pos:rng_start])
        pos = rng_end
    if pos < len(body):
        parts.append(body[pos:])

    return "".join(parts).strip()


# ---------------------------------------------------------------------------
# Flow line helpers
# ---------------------------------------------------------------------------


def _extract_raw_flow_lines(text: str) -> list[str]:
    r"""Parse raw flow lines, joining ``\`` continuations and skipping comments."""
    lines: list[str] = []
    buf = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            buf += stripped[:-1].rstrip() + " "
            continue
        if buf:
            lines.append(buf + stripped)
            buf = ""
        else:
            lines.append(stripped)
    if buf:
        lines.append(buf.rstrip())
    return lines


# ---------------------------------------------------------------------------
# Context section
# ---------------------------------------------------------------------------


def _parse_context_section(
    text: str, file_path: Path, line_offset: int,
) -> list[ContextSpec]:
    """Parse ``## Context`` list items into ContextSpec objects."""
    result: list[ContextSpec] = []
    for i, line in enumerate(text.splitlines()):
        m = _CONTEXT_LINE_RE.match(line.strip())
        if not m:
            continue
        result.append(
            ContextSpec(
                key=m.group(1),
                description=(m.group(2) or "").strip(),
                mode=m.group(3) or "read",
                source=SourceLocation(file=file_path, line=line_offset + i),
            )
        )
    return result


# ---------------------------------------------------------------------------
# Public API — agent files
# ---------------------------------------------------------------------------


def parse_agent_file(path: Path) -> AgentSpec:
    """Parse a single agent ``.md`` file into an AgentSpec."""
    text = path.read_text(encoding="utf-8")
    fm, body, body_start = _split_frontmatter(text)

    spec = AgentSpec(
        name=path.stem,
        file_path=path,
        source=SourceLocation(file=path, line=1),
        raw_frontmatter=dict(fm),
    )

    if "type" in fm:
        spec.agent_type = fm["type"]
    for key in _AGENT_FM_DIRECT:
        if key in fm:
            setattr(spec, key, fm[key])

    inputs, outputs, body = _extract_blockquote_ports(body, path, body_start)
    spec.input_ports = inputs
    spec.output_ports = outputs

    sections = _extract_sections(body)

    if "system" in sections:
        spec.system_prompt = sections["system"][0]

    excluded: set[str] = {"system"}

    if spec.agent_type == "composite":
        excluded |= {"agents", "flow"}
        if "agents" in sections:
            for m in _AGENT_LINK_RE.finditer(sections["agents"][0]):
                spec.internal_agents[m.group(1)] = m.group(2)
        if "flow" in sections:
            spec.internal_flow_lines = _extract_raw_flow_lines(
                sections["flow"][0]
            )

    if spec.agent_type == "code":
        code_match = _FENCED_CODE_RE.search(body)
        if code_match:
            spec.prompt_body = code_match.group(2).strip()
            if code_match.group(1):
                spec.language = code_match.group(1)
        else:
            spec.prompt_body = _extract_prompt_body(body, excluded)
    else:
        spec.prompt_body = _extract_prompt_body(body, excluded)

    return spec


# ---------------------------------------------------------------------------
# Public API — workflow files
# ---------------------------------------------------------------------------


def parse_workflow_file(path: Path) -> WorkflowSpec:
    """Parse a workflow ``.md`` file into a WorkflowSpec."""
    text = path.read_text(encoding="utf-8")
    fm, body, body_start = _split_frontmatter(text)

    version = fm.get("format_version", 1)
    if version != 1:
        raise ParseError(
            f"Unsupported format_version {version} in {path}; "
            f"only version 1 is supported."
        )

    spec = WorkflowSpec(
        name=fm.get("name", path.stem),
        description=fm.get("description", ""),
        format_version=version,
        tags=fm.get("tags") or [],
        file_path=path,
        source=SourceLocation(file=path, line=1),
        raw_frontmatter=dict(fm),
    )

    sections = _extract_sections(body)

    if "agents" in sections:
        for m in _AGENT_LINK_RE.finditer(sections["agents"][0]):
            spec.agents[m.group(1)] = m.group(2)

    raw_flow_lines: list[str] = []
    if "flow" in sections:
        raw_flow_lines = _extract_raw_flow_lines(sections["flow"][0])

    if raw_flow_lines:
        from dan.loader.flow_parser import FlowParseError, parse_flow_line

        flow_line_offset = body_start + sections["flow"][1]
        for i, line in enumerate(raw_flow_lines):
            src = SourceLocation(file=path, line=flow_line_offset + i)
            try:
                spec.flow_statements.append(parse_flow_line(line, source=src))
            except FlowParseError as exc:
                spec.parse_warnings.append(
                    (f"Failed to parse flow line: {line!r} ({exc})", src)
                )

    if "context" in sections:
        ctx_offset = body_start + sections["context"][1]
        spec.context_declarations = _parse_context_section(
            sections["context"][0], path, ctx_offset,
        )

    return spec


# ---------------------------------------------------------------------------
# Public API — hyperedge files
# ---------------------------------------------------------------------------

_VALID_HYPEREDGE_TYPES = frozenset({"skill", "guardrail", "style", "override"})


def load_hyperedge(path: str | Path) -> HyperedgeSpec:
    """Parse a hyperedge ``.md`` file into an HyperedgeSpec.

    Expected format: YAML frontmatter with type/name/hook/etc. fields,
    followed by a markdown body that becomes the ``content`` field.
    """
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    fm, body, _body_start = _split_frontmatter(text)

    he_type = fm.get("type", "skill")
    if he_type not in _VALID_HYPEREDGE_TYPES:
        raise ParseError(
            f"Invalid hyperedge type '{he_type}' in {file_path}; "
            f"expected one of {sorted(_VALID_HYPEREDGE_TYPES)}"
        )

    config: dict[str, Any] = {}
    if he_type == "guardrail":
        severity = fm.get("severity", "warning")
        block_on_fail = fm.get("block_on_fail", False)
        config = {"severity": severity, "block_on_fail": block_on_fail}

    def _to_list(val: Any) -> list[str]:
        if val is None:
            return []
        if isinstance(val, str):
            return [val]
        return list(val)

    return HyperedgeSpec(
        name=fm.get("name", file_path.stem),
        hyperedge_type=he_type,
        hook=fm.get("hook", "pre_prompt"),
        content=body.strip(),
        config=config,
        attach_to_type=_to_list(fm.get("attach_to_type")),
        attach_to_tags=_to_list(fm.get("attach_to_tags")),
        attach_to_subgraph=_to_list(fm.get("attach_to_subgraph")),
        attach_globally=fm.get("attach_globally", False),
        propagate=fm.get("propagate", True),
        source_file=str(file_path),
    )
