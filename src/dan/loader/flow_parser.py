"""Flow notation parser for the markdown agent format.

Parses ``## Flow`` lines into typed :class:`FlowStatement` objects.
"""

from __future__ import annotations

import re
from pathlib import Path

from dan.loader.models import (
    ChainStatement,
    EachStatement,
    FlowStatement,
    IfStatement,
    LoopStatement,
    SourceLocation,
)

__all__ = ["FlowParseError", "parse_flow_line", "parse_flow_lines"]

AGENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
ARROW_SPLIT_RE = re.compile(r"\s*(?:→|->)\s*")
PIPE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*\|\s*(\w+)\((.+)\)\s*$", re.DOTALL)


class FlowParseError(Exception):
    def __init__(self, message: str, line: str = "", position: int = 0) -> None:
        self.line_text = line
        self.position = position
        super().__init__(message)


def _validate_agent_name(name: str, line: str) -> None:
    if not AGENT_NAME_RE.match(name):
        raise FlowParseError(f"Invalid agent name: {name!r}", line=line)


def _parse_segment(segment: str, line: str) -> tuple[str, str | None]:
    """Return ``(agent_name, port | None)`` from a chain segment like ``agent.port``."""
    segment = segment.strip()
    if "." in segment:
        parts = segment.split(".", 1)
        name, port = parts[0].strip(), parts[1].strip()
        _validate_agent_name(name, line)
        if not port:
            raise FlowParseError(f"Empty port name on agent {name!r}", line=line)
        return name, port
    _validate_agent_name(segment, line)
    return segment, None


def _extract_quoted_string(text: str, line: str) -> tuple[str, str]:
    """Extract the first quoted string from *text*.

    Returns ``(extracted_value, remainder_after_closing_quote)``.
    """
    text = text.lstrip()
    if not text or text[0] not in ('"', "'"):
        raise FlowParseError("Expected a quoted string", line=line)
    quote = text[0]
    idx = 1
    chars: list[str] = []
    while idx < len(text):
        ch = text[idx]
        if ch == "\\" and idx + 1 < len(text):
            chars.append(text[idx + 1])
            idx += 2
            continue
        if ch == quote:
            return "".join(chars), text[idx + 1 :]
        chars.append(ch)
        idx += 1
    raise FlowParseError(f"Unterminated string starting with {quote}", line=line)


def _parse_kwargs(raw: str, line: str) -> tuple[str | None, dict[str, str]]:
    """Parse the inside of ``op(body, key: val, ...)``.

    Returns ``(positional_arg_or_None, {key: value, ...})``.
    The first positional argument (before any ``key:``) is the body agent.
    Quoted-string values are extracted properly; unquoted values are stripped.
    """
    raw = raw.strip()
    positional: str | None = None
    kwargs: dict[str, str] = {}

    pos = 0

    def _skip_ws() -> None:
        nonlocal pos
        while pos < len(raw) and raw[pos] in " \t":
            pos += 1

    def _read_token() -> str:
        nonlocal pos
        start = pos
        while pos < len(raw) and raw[pos] not in ",:)":
            pos += 1
        return raw[start:pos].strip()

    first = True
    while pos < len(raw):
        _skip_ws()
        if pos >= len(raw):
            break

        ahead = raw[pos:]
        kw_match = re.match(r"([A-Za-z_]\w*)\s*:\s*", ahead)
        if kw_match:
            key = kw_match.group(1)
            pos += kw_match.end()
            _skip_ws()
            if pos < len(raw) and raw[pos] in ('"', "'"):
                val, rest = _extract_quoted_string(raw[pos:], line)
                pos = len(raw) - len(rest)
            else:
                val = _read_token()
            kwargs[key] = val
        elif first:
            if raw[pos] in ('"', "'"):
                val, rest = _extract_quoted_string(raw[pos:], line)
                positional = val
                pos = len(raw) - len(rest)
            else:
                positional = _read_token()
            first = False
        else:
            raise FlowParseError(f"Unexpected token in arguments: {raw[pos:]!r}", line=line)

        _skip_ws()
        if pos < len(raw) and raw[pos] == ",":
            pos += 1

    return positional, kwargs


def _parse_chain(line: str, source: SourceLocation | None) -> ChainStatement:
    segments = ARROW_SPLIT_RE.split(line)
    if len(segments) < 2:
        raise FlowParseError("Chain must have at least two agents", line=line)

    parsed = [_parse_segment(s, line) for s in segments]
    agents = [name for name, _ in parsed]
    consumed: set[int] = set()
    ports: list[tuple[str | None, str | None]] = []

    for i in range(len(parsed) - 1):
        src_port = parsed[i][1] if i not in consumed else None
        tgt_port = parsed[i + 1][1] if (i + 1) not in consumed else None
        if parsed[i][1] is not None:
            consumed.add(i)
        if parsed[i + 1][1] is not None:
            consumed.add(i + 1)
        ports.append((src_port, tgt_port))

    return ChainStatement(agents=agents, port_pairs=ports, source=source)


def _parse_each(source_agent: str, args_raw: str, line: str, source: SourceLocation | None) -> EachStatement:
    body, kwargs = _parse_kwargs(args_raw, line)
    if not body:
        raise FlowParseError("each() requires a body agent", line=line)
    _validate_agent_name(body, line)
    parallel = int(kwargs.get("parallel", "1"))
    return EachStatement(source_agent=source_agent, body_agent=body, parallel=parallel, source=source)


def _parse_loop(source_agent: str, args_raw: str, line: str, source: SourceLocation | None) -> LoopStatement:
    body, kwargs = _parse_kwargs(args_raw, line)
    if not body:
        raise FlowParseError("loop() requires a body agent", line=line)
    _validate_agent_name(body, line)
    condition = kwargs.get("until", "")
    if not condition:
        raise FlowParseError("loop() requires an 'until' condition", line=line)
    max_iter = int(kwargs.get("max", "10"))
    return LoopStatement(
        source_agent=source_agent, body_agent=body,
        condition=condition, max_iterations=max_iter, source=source,
    )


def _parse_if(source_agent: str, args_raw: str, line: str, source: SourceLocation | None) -> IfStatement:
    cond, kwargs = _parse_kwargs(args_raw, line)
    if cond is None:
        raise FlowParseError("if() requires a condition string", line=line)
    then_agent = kwargs.get("then", "")
    else_agent = kwargs.get("else", "")
    if not then_agent or not else_agent:
        raise FlowParseError("if() requires both 'then' and 'else' agents", line=line)
    _validate_agent_name(then_agent, line)
    _validate_agent_name(else_agent, line)
    return IfStatement(
        source_agent=source_agent, condition=cond,
        then_agent=then_agent, else_agent=else_agent, source=source,
    )


_PIPE_DISPATCH: dict[str, type] = {
    "each": EachStatement,
    "loop": LoopStatement,
    "if": IfStatement,
}


def parse_flow_line(line: str, source: SourceLocation | None = None) -> FlowStatement:
    """Parse a single flow line into a :class:`FlowStatement`."""
    line = line.strip()
    if not line:
        raise FlowParseError("Empty flow line", line=line)

    m = PIPE_RE.match(line)
    if m:
        source_agent, op, args_raw = m.group(1), m.group(2), m.group(3)
        _validate_agent_name(source_agent, line)

        if op == "each":
            return _parse_each(source_agent, args_raw, line, source)
        if op == "loop":
            return _parse_loop(source_agent, args_raw, line, source)
        if op == "if":
            return _parse_if(source_agent, args_raw, line, source)

        raise FlowParseError(f"Unknown pipe operator: {op!r}", line=line)

    if "→" in line or "->" in line:
        return _parse_chain(line, source)

    raise FlowParseError(f"Cannot parse flow line: {line!r}", line=line)


def parse_flow_lines(
    lines: list[str],
    source_file: Path | None = None,
    start_line: int = 0,
) -> list[FlowStatement]:
    """Parse multiple flow lines, handling comments, blanks, and continuations."""
    merged: list[tuple[str, int]] = []
    buf = ""
    buf_line = start_line

    for i, raw in enumerate(lines):
        stripped = raw.rstrip()
        if not buf:
            buf_line = start_line + i

        if stripped.endswith("\\"):
            buf += stripped[:-1].strip() + " "
            continue

        buf += stripped
        merged.append((buf.strip(), buf_line))
        buf = ""

    if buf:
        merged.append((buf.strip(), buf_line))

    results: list[FlowStatement] = []
    for text, lineno in merged:
        if not text or text.startswith("#"):
            continue
        loc = SourceLocation(file=source_file or Path("<unknown>"), line=lineno) if source_file is not None else None
        try:
            results.append(parse_flow_line(text, source=loc))
        except FlowParseError:
            raise
        except Exception as exc:
            raise FlowParseError(str(exc), line=text) from exc

    return results
