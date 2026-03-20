"""Tests for dan.loader.flow_parser."""
import pytest
from dan.loader.flow_parser import parse_flow_line, parse_flow_lines, FlowParseError
from dan.loader.models import (
    ChainStatement,
    EachStatement,
    IfStatement,
    LoopStatement,
    ParallelStatement,
)


class TestChainParsing:
    def test_simple_chain(self):
        stmt = parse_flow_line("a → b → c")
        assert isinstance(stmt, ChainStatement)
        assert stmt.agents == ["a", "b", "c"]

    def test_ascii_arrow(self):
        stmt = parse_flow_line("a -> b -> c")
        assert isinstance(stmt, ChainStatement)
        assert stmt.agents == ["a", "b", "c"]

    def test_port_specific(self):
        stmt = parse_flow_line("a.output → b.input")
        assert isinstance(stmt, ChainStatement)
        assert stmt.agents == ["a", "b"]
        assert stmt.port_pairs[0] == ("output", "input")

    def test_two_agents(self):
        stmt = parse_flow_line("writer → reviewer")
        assert isinstance(stmt, ChainStatement)
        assert stmt.agents == ["writer", "reviewer"]

    def test_mixed_ports(self):
        stmt = parse_flow_line("a.out → b → c.in")
        assert isinstance(stmt, ChainStatement)
        assert stmt.agents == ["a", "b", "c"]
        assert stmt.port_pairs[0][0] == "out"


class TestEachParsing:
    def test_basic_each(self):
        stmt = parse_flow_line("writer | each(processor, parallel: 4)")
        assert isinstance(stmt, EachStatement)
        assert stmt.source_agent == "writer"
        assert stmt.body_agent == "processor"
        assert stmt.parallel == 4

    def test_default_parallel(self):
        stmt = parse_flow_line("writer | each(processor)")
        assert isinstance(stmt, EachStatement)
        assert stmt.parallel == 1

    def test_each_with_source_port(self):
        stmt = parse_flow_line("outline_planner.sections | each(section_writer, parallel: 4)")
        assert isinstance(stmt, EachStatement)
        assert stmt.source_agent == "outline_planner"
        assert stmt.source_port == "sections"
        assert stmt.body_agent == "section_writer"
        assert stmt.parallel == 4


class TestLoopParsing:
    def test_basic_loop(self):
        stmt = parse_flow_line('reviewer | loop(reviser, until: "verdict == \'accept\'", max: 5)')
        assert isinstance(stmt, LoopStatement)
        assert stmt.source_agent == "reviewer"
        assert stmt.body_agent == "reviser"
        assert "verdict" in stmt.condition
        assert stmt.max_iterations == 5

    def test_default_max(self):
        stmt = parse_flow_line('reviewer | loop(reviser, until: "done")')
        assert isinstance(stmt, LoopStatement)
        assert stmt.max_iterations == 10

    def test_missing_until(self):
        with pytest.raises(FlowParseError):
            parse_flow_line("a | loop(b)")


class TestIfParsing:
    def test_basic_if(self):
        stmt = parse_flow_line('router | if("route == \'detailed\'", then: writer, else: processor)')
        assert isinstance(stmt, IfStatement)
        assert stmt.source_agent == "router"
        assert "route" in stmt.condition
        assert stmt.then_agent == "writer"
        assert stmt.else_agent == "processor"

    def test_missing_else(self):
        with pytest.raises(FlowParseError):
            parse_flow_line('a | if("x > 1", then: b)')

    def test_missing_then(self):
        with pytest.raises(FlowParseError):
            parse_flow_line('a | if("x > 1", else: b)')


class TestParallelParsing:
    def test_basic_parallel(self):
        stmt = parse_flow_line("source | parallel(team_a, team_b)")
        assert isinstance(stmt, ParallelStatement)
        assert stmt.source_agent == "source"
        assert stmt.branch_agents == ["team_a", "team_b"]
        assert stmt.merge == "append"
        assert stmt.parallel == 1

    def test_parallel_with_options(self):
        stmt = parse_flow_line("source | parallel(team_a, team_b, merge: last_write_wins, parallel: 2)")
        assert isinstance(stmt, ParallelStatement)
        assert stmt.branch_agents == ["team_a", "team_b"]
        assert stmt.merge == "last_write_wins"
        assert stmt.parallel == 2

    def test_parallel_single_branch(self):
        stmt = parse_flow_line("input | parallel(solo)")
        assert isinstance(stmt, ParallelStatement)
        assert stmt.branch_agents == ["solo"]

    def test_parallel_requires_branch(self):
        with pytest.raises(FlowParseError):
            parse_flow_line("source | parallel()")


class TestParseFlowLines:
    def test_multiple_lines(self):
        lines = [
            "a → b",
            "b → c",
            "",
            "# comment line",
            "d | each(e, parallel: 2)",
        ]
        stmts = parse_flow_lines(lines)
        assert len(stmts) == 3
        assert isinstance(stmts[0], ChainStatement)
        assert isinstance(stmts[2], EachStatement)

    def test_empty_line(self):
        with pytest.raises(FlowParseError):
            parse_flow_line("")

    def test_unknown_operator(self):
        with pytest.raises(FlowParseError):
            parse_flow_line("a | unknown(b)")


class TestEdgeCases:
    def test_hyphenated_names(self):
        stmt = parse_flow_line("idea-gen → outline-planner")
        assert isinstance(stmt, ChainStatement)
        assert stmt.agents == ["idea-gen", "outline-planner"]

    def test_underscore_names(self):
        stmt = parse_flow_line("idea_gen → outline_planner")
        assert isinstance(stmt, ChainStatement)
