"""Unit tests for NodeRef, PortRef, and the f-string marker system."""

from dan.builder.refs import NodeRef, PortRef, MARKER_PATTERN, _sanitize_alias


class TestSanitizeAlias:
    def test_simple_id(self):
        assert _sanitize_alias("idea_gen") == "idea_gen"

    def test_hyphens(self):
        assert _sanitize_alias("my-node") == "my_node"

    def test_dots(self):
        assert _sanitize_alias("step.1") == "step_1"

    def test_leading_digit(self):
        assert _sanitize_alias("1_start") == "_1_start"

    def test_spaces(self):
        assert _sanitize_alias("node name") == "node_name"

    def test_empty_string(self):
        assert _sanitize_alias("") == "_ref"

    def test_complex_id(self):
        assert _sanitize_alias("my.node-3!") == "my_node_3_"


class TestPortRef:
    def test_format_emits_marker(self):
        ref = PortRef("node_a", "output")
        assert f"{ref}" == "<<dan:node_a:output>>"

    def test_repr(self):
        ref = PortRef("node_a", "output")
        assert repr(ref) == "PortRef('node_a', 'output')"

    def test_equality(self):
        a = PortRef("n1", "p1")
        b = PortRef("n1", "p1")
        c = PortRef("n1", "p2")
        assert a == b
        assert a != c

    def test_hashable(self):
        a = PortRef("n1", "p1")
        b = PortRef("n1", "p1")
        assert hash(a) == hash(b)
        assert {a, b} == {a}


class TestNodeRef:
    def test_format_emits_marker_with_default_port(self):
        ref = NodeRef("idea_gen", "llm_operator")
        marker = f"{ref}"
        assert marker == "<<dan:idea_gen:text>>"

    def test_format_code_operator_default(self):
        ref = NodeRef("proc", "code_operator")
        assert f"{ref}" == "<<dan:proc:result>>"

    def test_getitem_returns_portref(self):
        ref = NodeRef("node_a", "llm_operator")
        port = ref["sections"]
        assert isinstance(port, PortRef)
        assert port.node_id == "node_a"
        assert port.port_name == "sections"

    def test_repr(self):
        ref = NodeRef("idea_gen", "llm_operator")
        assert repr(ref) == "NodeRef('idea_gen')"

    def test_equality(self):
        a = NodeRef("n1", "llm_operator")
        b = NodeRef("n1", "llm_operator")
        c = NodeRef("n2", "llm_operator")
        assert a == b
        assert a != c

    def test_hashable(self):
        a = NodeRef("n1", "llm_operator")
        b = NodeRef("n1", "llm_operator")
        assert hash(a) == hash(b)

    def test_rshift_returns_rhs(self):
        a = NodeRef("a", "llm_operator")
        b = NodeRef("b", "llm_operator")
        result = a >> b
        assert result is b

    def test_rshift_chain(self):
        a = NodeRef("a", "llm_operator")
        b = NodeRef("b", "llm_operator")
        c = NodeRef("c", "llm_operator")
        result = a >> b >> c
        assert result is c

    def test_default_input_fallback(self):
        ref = NodeRef("n", "llm_operator")
        assert ref.default_input == "input"

    def test_default_input_override(self):
        ref = NodeRef("n", "while_loop", _default_input="draft")
        assert ref.default_input == "draft"

    def test_default_output_override(self):
        ref = NodeRef("n", "while_loop", _default_output="draft")
        assert ref.default_output == "draft"
        assert f"{ref}" == "<<dan:n:draft>>"

    def test_default_output_fallback(self):
        ref = NodeRef("n", "while_loop")
        assert ref.default_output == "result"


class TestMarkerPattern:
    def test_matches_simple(self):
        m = MARKER_PATTERN.search("<<dan:node_a:output>>")
        assert m is not None
        assert m.group(1) == "node_a"
        assert m.group(2) == "output"

    def test_matches_in_context(self):
        text = "Create outline for: <<dan:idea_gen:text>>"
        m = MARKER_PATTERN.search(text)
        assert m is not None
        assert m.group(1) == "idea_gen"
        assert m.group(2) == "text"

    def test_multiple_markers(self):
        text = "Use <<dan:a:x>> and <<dan:b:y>>"
        matches = MARKER_PATTERN.findall(text)
        assert len(matches) == 2
        assert matches[0] == ("a", "x")
        assert matches[1] == ("b", "y")

    def test_no_match_on_normal_braces(self):
        text = "format {variable} normally"
        assert MARKER_PATTERN.search(text) is None
