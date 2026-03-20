from __future__ import annotations

from dan.builder import workflow
from dan.builder.decompiler import decompile


def test_builder_supports_agent_team_runtime_primitive() -> None:
    wf = workflow("agent_team_parity")
    with wf.team(
        "review_team",
        moderator_prompt="Coordinate the specialists",
        turn_strategy="free_form",
        shared_context_keys=["conversation_history"],
    ) as team:
        with team.agent("researcher") as sub:
            sub.llm("research", prompt="Research the topic")
        with team.agent("writer") as sub:
            sub.llm("write", prompt="Draft the answer")

    graph = wf.build()
    node = graph.node_by_id("review_team")

    assert node is not None
    assert node.node_type == "agent_team"
    assert set(node.agents.keys()) == {"researcher", "writer"}
    assert set(node.agents.values()) == {"review_team_researcher", "review_team_writer"}
    assert set(graph.sub_graphs.keys()) >= {"review_team_researcher", "review_team_writer"}


def test_agent_team_decompiles_and_round_trips() -> None:
    wf = workflow("agent_team_roundtrip")
    with wf.team(
        "review_team",
        moderator_prompt="Coordinate the specialists",
        turn_strategy="moderator",
        max_turns=6,
    ) as team:
        with team.agent("researcher") as sub:
            sub.llm("research", prompt="Research the topic")
        with team.agent("writer") as sub:
            sub.llm("write", prompt="Draft the answer")

    graph = wf.build()
    code = decompile(graph)

    assert "with wf.team('review_team'" in code
    assert ".agent('researcher')" in code
    assert ".agent('writer')" in code

    ns: dict[str, object] = {}
    exec(code, ns)
    rebuilt = ns["graph"]

    rebuilt_node = rebuilt.node_by_id("review_team")
    assert rebuilt_node is not None
    assert rebuilt_node.node_type == "agent_team"
    assert rebuilt_node.turn_strategy == "moderator"
    assert rebuilt_node.max_turns == 6
    assert set(rebuilt_node.agents.keys()) == {"researcher", "writer"}
