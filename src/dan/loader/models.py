"""Intermediate parsed representations for the markdown agent format.

These models are the output of the parser (8-1) and input to the
compiler (8-2).  They are independent of the ``dan_graph_v1`` runtime
models — the compiler maps them into ``Graph`` / ``Node`` / ``Edge``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Source location — every parsed element tracks where it came from
# ---------------------------------------------------------------------------


@dataclass
class SourceLocation:
    """Points back to the originating markdown file + line."""

    file: Path
    line: int = 0
    column: int = 0


# ---------------------------------------------------------------------------
# Port specifications (parsed from blockquotes)
# ---------------------------------------------------------------------------


@dataclass
class PortSpec:
    """A single port parsed from ``> Accepts`` or ``> Returns`` blockquotes."""

    name: str
    type_annotation: str = "string"
    schema_path: str | None = None  # e.g. "schemas/outline.json"
    source: SourceLocation | None = None


# ---------------------------------------------------------------------------
# Agent specification (parsed from one .md file)
# ---------------------------------------------------------------------------


@dataclass
class AgentSpec:
    """Everything extracted from a single agent ``.md`` file."""

    # Identity
    name: str = ""
    file_path: Path | None = None

    # Frontmatter fields
    agent_type: Literal["llm", "tool", "code", "human", "router", "composite", "reflection", "goal_loop", "vote", "agent_team"] = "llm"
    model: str = ""
    temperature: float = 0.7
    max_tokens: int | None = None
    system_prompt: str = ""
    output_schema: dict[str, Any] | None = None
    retry_policy: dict[str, Any] | None = None

    # Type-specific frontmatter
    tool_id: str = ""
    tool_config: dict[str, Any] = field(default_factory=dict)
    route_descriptions: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    default_action: str | None = None
    language: str = "python"  # for code agents

    # Ports
    input_ports: list[PortSpec] = field(default_factory=list)
    output_ports: list[PortSpec] = field(default_factory=list)

    # Body
    prompt_body: str = ""

    # Nested internal agent references (populated for node kinds that own named subgraphs)
    internal_agents: dict[str, str] = field(default_factory=dict)  # name -> path
    internal_flow_lines: list[str] = field(default_factory=list)

    # Source tracking
    source: SourceLocation | None = None

    # Raw frontmatter for pass-through of unknown fields
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Flow statements (parsed from ## Flow section)
# ---------------------------------------------------------------------------


@dataclass
class ChainStatement:
    """``agent_a → agent_b → agent_c`` — sequential default-port wiring."""

    agents: list[str]
    port_pairs: list[tuple[str | None, str | None]] = field(default_factory=list)
    source: SourceLocation | None = None


@dataclass
class EachStatement:
    """``agent_a | each(agent_b, parallel: N)`` or ``agent_a.port | each(...)`` — fan-out over items."""

    source_agent: str
    body_agent: str
    parallel: int = 1
    source_port: str | None = None  # when set, wire this output port to items (e.g. sections)
    source: SourceLocation | None = None


@dataclass
class LoopStatement:
    """``agent_a | loop(agent_b, until: "cond", max: N, state: "{...}", defaults: "{...}")``."""

    source_agent: str
    body_agent: str
    condition: str = ""
    max_iterations: int = 10
    state_schema: dict | None = None
    state_defaults: dict | None = None
    source: SourceLocation | None = None


@dataclass
class IfStatement:
    """``agent_a | if("cond", then: agent_b, else: agent_c)`` — gate-style branch."""

    source_agent: str
    condition: str = ""
    then_agent: str = ""
    else_agent: str = ""
    source: SourceLocation | None = None


@dataclass
class ParallelStatement:
    """``agent_a | parallel(team_a, team_b, merge: append, parallel: 2)`` — parallel branches."""

    source_agent: str
    branch_agents: list[str]
    merge: str = "append"
    parallel: int = 1
    source: SourceLocation | None = None


FlowStatement = ChainStatement | EachStatement | LoopStatement | IfStatement | ParallelStatement


# ---------------------------------------------------------------------------
# Hyperedge specification (parsed from .md file or inline definition)
# ---------------------------------------------------------------------------


@dataclass
class HyperedgeSpec:
    """Intermediate representation of a hyperedge, prior to compilation."""

    name: str = ""
    hyperedge_type: str = "skill"  # skill, guardrail, style, override
    hook: str = "pre_prompt"
    content: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    # Attachment selectors
    attach_to: list[str] = field(default_factory=list)
    attach_to_type: list[str] = field(default_factory=list)
    attach_to_tags: list[str] = field(default_factory=list)
    attach_to_subgraph: list[str] = field(default_factory=list)
    attach_globally: bool = False
    propagate: bool = True
    priority: int | None = None
    source_file: str | None = None  # path to the .md file (None for inline)


# ---------------------------------------------------------------------------
# Context declaration (parsed from ## Context section)
# ---------------------------------------------------------------------------


@dataclass
class ContextSpec:
    """A shared context key declared in ``## Context``."""

    key: str
    description: str = ""
    mode: str = "read"  # read | write | append
    source: SourceLocation | None = None


# ---------------------------------------------------------------------------
# Workflow specification (parsed from workflow .md file)
# ---------------------------------------------------------------------------


@dataclass
class WorkflowSpec:
    """Everything extracted from a workflow ``.md`` file."""

    name: str = ""
    description: str = ""
    format_version: int = 1
    tags: list[str] = field(default_factory=list)

    agents: dict[str, str] = field(default_factory=dict)  # name -> file path
    flow_statements: list[FlowStatement] = field(default_factory=list)
    context_declarations: list[ContextSpec] = field(default_factory=list)
    hyperedges: list[HyperedgeSpec] = field(default_factory=list)

    parse_warnings: list[tuple[str, SourceLocation | None]] = field(default_factory=list)

    file_path: Path | None = None
    source: SourceLocation | None = None
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)
