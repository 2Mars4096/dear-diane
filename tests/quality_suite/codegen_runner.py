"""Codegen path evaluator for golden intent fixtures.

Runs each golden intent through:
  fixture → WorkflowIntent → IntentCompiler → exec() → validate
  → topology check + structure constraints → round-trip

No LLM access required — uses IntentCompiler deterministically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from dan.meta.intent_compiler import IntentCompiler
from dan.meta.intent_schema import (
    ReviewRequirement,
    StageIntent,
    StageType,
    WorkflowIntent,
)
from dan.meta.planner import validate_codegen_output
from dan.models.graph import Graph

from tests.quality_suite.graph_equivalence import round_trip_check


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class IntentResult:
    intent_file: str
    family: str
    variant: str
    path: str  # "codegen" or "intent"
    passed: bool
    error_type: str | None = None
    error_message: str | None = None
    latency_ms: float = 0.0
    topology_check: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fixture → WorkflowIntent conversion
# ---------------------------------------------------------------------------


def fixture_to_workflow_intent(fixture: dict) -> WorkflowIntent:
    """Convert a golden intent fixture dict into a ``WorkflowIntent``.

    Uses the fixture's ``expected.topology`` flags to determine which stage
    types to include so that the compiled graph matches the expected structure.
    """
    intent_text = fixture["intent"]
    topology = fixture.get("expected", {}).get("topology", {})

    stages: list[StageIntent] = []

    stages.append(StageIntent(
        name="prepare",
        stage_type=StageType.transform,
        description=f"Prepare input for: {intent_text[:60]}",
    ))

    if topology.get("has_rag"):
        stages.append(StageIntent(
            name="retrieve_context",
            stage_type=StageType.rag_retrieval,
            description="Retrieve relevant context from knowledge base",
            config={"collection": "default"},
        ))

    if topology.get("has_tools"):
        stages.append(StageIntent(
            name="tool_step",
            stage_type=StageType.tool_call,
            description="Execute external tool",
            config={"tool_id": "web_search"},
        ))

    if topology.get("has_fan_out"):
        stages.append(StageIntent(
            name="parallel_process",
            stage_type=StageType.fan_out,
            description="Process items in parallel",
            parallelism=3,
        ))

    if topology.get("has_loops"):
        stages.append(StageIntent(
            name="review_cycle",
            stage_type=StageType.review_loop,
            description="Iterative review and revision",
            review=ReviewRequirement(
                reviewer_prompt="Review the output for quality and correctness",
                condition="quality_score >= 8",
                max_iterations=3,
            ),
        ))

    if topology.get("has_human_in_loop"):
        stages.append(StageIntent(
            name="human_review",
            stage_type=StageType.human_approval,
            description="Human approval gate",
        ))

    stages.append(StageIntent(
        name="synthesize",
        stage_type=StageType.transform,
        description="Synthesize and format final output",
    ))

    return WorkflowIntent(
        goal=intent_text,
        stages=stages,
        global_inputs=["input"],
        global_outputs=["output"],
    )


# ---------------------------------------------------------------------------
# Topology checker
# ---------------------------------------------------------------------------

_LOOP_NODE_TYPES = frozenset({"while_loop", "gate"})


def check_topology(graph: Graph, expected_topology: dict) -> dict:
    """Compare expected topology flags against the actual graph structure."""
    node_types: set[str] = set()
    for n in graph.nodes:
        node_types.add(n.node_type)
    for sg in graph.sub_graphs.values():
        for n in sg.nodes:
            node_types.add(n.node_type)

    actual = {
        "has_loops": bool(node_types & _LOOP_NODE_TYPES),
        "has_fan_out": "for_each" in node_types,
        "has_tools": "tool_operator" in node_types,
        "has_rag": "rag_operator" in node_types,
        "has_human_in_loop": "human_in_the_loop" in node_types,
    }

    expected = {
        "has_loops": expected_topology.get("has_loops", False),
        "has_fan_out": expected_topology.get("has_fan_out", False),
        "has_tools": expected_topology.get("has_tools", False),
        "has_rag": expected_topology.get("has_rag", False),
        "has_human_in_loop": expected_topology.get("has_human_in_loop", False),
    }

    return {"expected": expected, "actual": actual, "match": actual == expected}


def _iter_graphs(graph: Graph):
    """Yield the graph and all nested sub-graphs exactly once."""
    stack = [graph]
    visited: set[int] = set()
    while stack:
        current = stack.pop()
        marker = id(current)
        if marker in visited:
            continue
        visited.add(marker)
        yield current
        for sub_graph in current.sub_graphs.values():
            stack.append(sub_graph)


def _collect_all_node_types(graph: Graph) -> set[str]:
    node_types: set[str] = set()
    for g in _iter_graphs(graph):
        for node in g.nodes:
            node_types.add(node.node_type)
    return node_types


def _count_all_nodes(graph: Graph) -> int:
    return sum(len(g.nodes) for g in _iter_graphs(graph))


def check_structure_constraints(graph: Graph, expected: dict) -> dict:
    """Enforce fixture-level min_nodes and required node_types."""
    min_nodes = int(expected.get("min_nodes", 0) or 0)
    required_types = sorted(set(expected.get("node_types", [])))

    actual_node_count = _count_all_nodes(graph)
    actual_node_types = sorted(_collect_all_node_types(graph))
    actual_type_set = set(actual_node_types)

    missing_types = sorted(
        node_type for node_type in required_types
        if node_type not in actual_type_set
    )
    node_count_ok = actual_node_count >= min_nodes
    node_types_ok = len(missing_types) == 0

    return {
        "expected": {
            "min_nodes": min_nodes,
            "node_types": required_types,
        },
        "actual": {
            "node_count": actual_node_count,
            "node_types": actual_node_types,
        },
        "missing_node_types": missing_types,
        "node_count_ok": node_count_ok,
        "node_types_ok": node_types_ok,
        "match": node_count_ok and node_types_ok,
    }


def classify_mismatch_error_type(
    *,
    topology_match: bool,
    structure_match: bool,
    roundtrip_equivalent: bool,
) -> str | None:
    """Return a stable failure type for non-validation mismatches."""
    mismatch_types: list[str] = []
    if not topology_match:
        mismatch_types.append("topology_mismatch")
    if not structure_match:
        mismatch_types.append("structure_mismatch")
    if not roundtrip_equivalent:
        mismatch_types.append("round_trip_mismatch")
    if not mismatch_types:
        return None
    if len(mismatch_types) == 1:
        return mismatch_types[0]
    return "multiple_mismatch"


# ---------------------------------------------------------------------------
# Mock builder code generation
# ---------------------------------------------------------------------------


def _generate_mock_builder_code(intent: dict) -> str:
    """Produce valid builder code from a golden intent fixture.

    Parses the fixture into a ``WorkflowIntent`` and compiles it
    deterministically via ``IntentCompiler``.
    """
    workflow_intent = fixture_to_workflow_intent(intent)
    compiler = IntentCompiler()
    return compiler.compile(workflow_intent)


# ---------------------------------------------------------------------------
# Evaluation entry point
# ---------------------------------------------------------------------------


def run_codegen_evaluation(
    intents: list[dict],
    mock_responses: dict[str, str] | None = None,
) -> list[IntentResult]:
    """Run each intent through the codegen path with mock builder code.

    For each intent:
    1. Use ``mock_responses[family_variant]`` or generate builder code via
       ``IntentCompiler``
    2. ``exec()`` the code to get a ``Graph``
    3. Run ``validate_codegen_output()``
    4. Check topology expectations (has_loops, has_fan_out, etc.)
       and fixture constraints (min_nodes, node_types)
    5. Run ``round_trip_check()``
    6. Record ``IntentResult``
    """
    results: list[IntentResult] = []

    for fixture in intents:
        file_name = fixture.get("_source_file", "unknown")
        family = fixture.get("family", "unknown")
        variant = fixture.get("variant", "unknown")
        key = f"{family}_{variant}"

        t0 = time.perf_counter()

        try:
            # Step 1: obtain builder code
            if mock_responses and key in mock_responses:
                code = mock_responses[key]
            else:
                code = _generate_mock_builder_code(fixture)

            # Step 2: exec to get Graph
            ns: dict = {}
            exec(code, ns)  # noqa: S102
            graph: Graph | None = ns.get("graph")
            if graph is None:
                raise ValueError(
                    "Builder code did not produce a 'graph' variable"
                )

            # Step 3: validate
            graph_dict = graph.model_dump(mode="json")
            validation = validate_codegen_output(graph_dict)
            if not validation.success:
                error_msgs = "; ".join(
                    e.message for e in (validation.errors or [])[:3]
                )
                results.append(IntentResult(
                    intent_file=file_name,
                    family=family,
                    variant=variant,
                    path="codegen",
                    passed=False,
                    error_type="validation",
                    error_message=error_msgs[:200],
                    latency_ms=(time.perf_counter() - t0) * 1000,
                ))
                continue

            # Step 4: topology check
            expected = fixture.get("expected", {})
            expected_topo = expected.get("topology", {})
            topo = check_topology(graph, expected_topo)
            constraints = check_structure_constraints(graph, expected)
            topology_check = {
                **topo,
                "topology_match": topo["match"],
                "structure_constraints": constraints,
                "match": topo["match"] and constraints["match"],
            }

            # Step 5: round-trip check
            rt = round_trip_check(code)

            passed = topology_check["match"] and rt.equivalent
            error_parts: list[str] = []
            if not topology_check["topology_match"]:
                error_parts.append(
                    f"topology mismatch: expected={topo['expected']}, "
                    f"actual={topo['actual']}"
                )
            if not constraints["match"]:
                error_parts.append(
                    "structure mismatch: "
                    f"min_nodes expected>={constraints['expected']['min_nodes']} "
                    f"actual={constraints['actual']['node_count']}; "
                    f"missing node types={constraints['missing_node_types']}"
                )
            if not rt.equivalent:
                error_parts.append(
                    f"round-trip mismatch: {rt.mismatches[:3]}"
                )
            mismatch_error_type = classify_mismatch_error_type(
                topology_match=topology_check["topology_match"],
                structure_match=constraints["match"],
                roundtrip_equivalent=rt.equivalent,
            )

            results.append(IntentResult(
                intent_file=file_name,
                family=family,
                variant=variant,
                path="codegen",
                passed=passed,
                error_type=mismatch_error_type,
                error_message="; ".join(error_parts) if error_parts else None,
                latency_ms=(time.perf_counter() - t0) * 1000,
                topology_check=topology_check,
            ))

        except Exception as e:
            results.append(IntentResult(
                intent_file=file_name,
                family=family,
                variant=variant,
                path="codegen",
                passed=False,
                error_type=type(e).__name__,
                error_message=str(e)[:200],
                latency_ms=(time.perf_counter() - t0) * 1000,
            ))

    return results
