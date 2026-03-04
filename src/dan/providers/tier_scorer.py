"""Tier scorer — assigns a TaskTier to each LLM-using node.

Combines three orthogonal scoring dimensions (difficulty, impact,
recoverability) into a single tier score that maps to one of four
provider-agnostic model tiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dan.providers.model_policy import CascadePolicy, TaskTier, TierWeights
from dan.utils.tokens import estimate_tokens

if TYPE_CHECKING:
    from dan.models.graph import Graph

_LLM_NODE_TYPES: frozenset[str] = frozenset(
    {"llm_operator", "orchestrator", "router", "agent_team", "vote", "reflection"}
)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _count_schema_properties(schema: dict[str, Any]) -> int:
    """Count top-level properties in a JSON Schema object."""
    props = schema.get("properties", {})
    return len(props)


# ---------------------------------------------------------------------------
# DifficultyScorer
# ---------------------------------------------------------------------------

_DIFFICULTY_BASE: dict[str, float] = {
    "router": 0.15,
    "llm_operator": 0.40,
    "orchestrator": 0.55,
    "reflection": 0.70,
    "vote": 0.45,
    "agent_team": 0.30,
}


class DifficultyScorer:
    """Scores how hard the reasoning task is for a given node."""

    def score(self, node: Any, tool_count: int = 0) -> float:
        node_type = getattr(node, "node_type", "")
        base = _DIFFICULTY_BASE.get(node_type, 0.40)

        prop_count = 0
        for port in getattr(node, "output_ports", []):
            schema = getattr(port, "json_schema", None) or {}
            prop_count += _count_schema_properties(schema)
        base += min(0.15, (prop_count // 3) * 0.05)

        base += min(0.15, (tool_count // 3) * 0.05)

        prompt_text = (
            getattr(node, "prompt_template", "")
            or getattr(node, "system_prompt", "")
            or ""
        )
        if prompt_text:
            tokens = estimate_tokens(prompt_text)
            if tokens > 5000:
                base += 0.20
            elif tokens > 2000:
                base += 0.10

        return _clamp(base)


# ---------------------------------------------------------------------------
# ImpactScorer
# ---------------------------------------------------------------------------


class ImpactScorer:
    """Scores how much damage a wrong answer causes, based on graph topology.

    Impact scores are precomputed once per graph so per-call lookup is O(1).
    """

    def __init__(self, graph: Graph) -> None:
        self._scores: dict[str, float] = self._precompute(graph)

    def score(self, node_id: str) -> float:
        return self._scores.get(node_id, 0.40)

    @staticmethod
    def _precompute(graph: Graph) -> dict[str, float]:
        node_types: dict[str, str] = {}
        for n in graph.nodes:
            node_types[n.id] = getattr(n, "node_type", "")

        outgoing_data: dict[str, list[str]] = {}
        for e in graph.edges:
            if getattr(e, "edge_type", "") == "data":
                outgoing_data.setdefault(e.source_node_id, []).append(e.target_node_id)

        sub_graph_node_ids: set[str] = set()
        for sg in graph.sub_graphs.values():
            for n in sg.nodes:
                sub_graph_node_ids.add(n.id)

        scores: dict[str, float] = {}
        for n in graph.nodes:
            nid = n.id
            ntype = node_types.get(nid, "")
            if ntype not in _LLM_NODE_TYPES:
                continue

            targets = outgoing_data.get(nid, [])
            llm_targets = [t for t in targets if node_types.get(t, "") in _LLM_NODE_TYPES]

            feeds_human = any(
                node_types.get(t, "") in ("human", "human_in_the_loop")
                for t in targets
            )

            if feeds_human:
                base = 0.85
            elif not llm_targets:
                base = 0.80
            else:
                base = 0.20

            fan_out = len(targets)
            base += min(0.30, fan_out * 0.05)

            if nid in sub_graph_node_ids:
                base -= 0.15

            scores[nid] = _clamp(base)

        return scores


# ---------------------------------------------------------------------------
# RecoverabilityScorer
# ---------------------------------------------------------------------------


class RecoverabilityScorer:
    """Scores how well errors from this node can be caught and corrected."""

    def score(self, node: Any, graph: Graph | None = None) -> float:
        s = 0.0

        retry = getattr(node, "retry_policy", None)
        if retry is not None and getattr(retry, "max_retries", 0) > 0:
            s += 0.20

        has_output_schema = False
        for port in getattr(node, "output_ports", []):
            schema = getattr(port, "json_schema", None)
            if schema:
                has_output_schema = True
                break
        if has_output_schema:
            s += 0.25

        node_id = getattr(node, "id", "")
        if graph is not None:
            for sg_key in graph.sub_graphs:
                if "__body" in sg_key:
                    sg = graph.sub_graphs[sg_key]
                    if any(n.id == node_id for n in sg.nodes):
                        s += 0.25
                        break

            s += self._downstream_validator_bonus(node_id, graph)

        policy = getattr(node, "model_policy", None)
        if isinstance(policy, CascadePolicy):
            s += 0.10

        return _clamp(s)

    @staticmethod
    def _downstream_validator_bonus(node_id: str, graph: Graph) -> float:
        """Walk up to 2 hops from *node_id* looking for a validator node."""
        node_types: dict[str, str] = {n.id: getattr(n, "node_type", "") for n in graph.nodes}
        outgoing: dict[str, list[str]] = {}
        for e in graph.edges:
            outgoing.setdefault(e.source_node_id, []).append(e.target_node_id)

        hop1 = outgoing.get(node_id, [])
        for tid in hop1:
            if node_types.get(tid) == "validator":
                return 0.20
        for tid in hop1:
            for tid2 in outgoing.get(tid, []):
                if node_types.get(tid2) == "validator":
                    return 0.20
        return 0.0


# ---------------------------------------------------------------------------
# TierResult + TierScorer
# ---------------------------------------------------------------------------


@dataclass
class TierResult:
    """Output of tier scoring — tier assignment plus dimension breakdown."""

    tier: TaskTier
    tier_score: float
    difficulty: float
    impact: float
    recoverability: float


class TierScorer:
    """Combines difficulty, impact, and recoverability into a TaskTier.

    Construct with a ``Graph`` to enable topology-aware scoring (impact
    and recoverability).  Without a graph, those dimensions use defaults.
    """

    def __init__(
        self,
        graph: Graph | None = None,
        weights: TierWeights | None = None,
    ) -> None:
        self._weights = weights or TierWeights()
        self._difficulty = DifficultyScorer()
        self._impact = ImpactScorer(graph) if graph is not None else None
        self._recoverability = RecoverabilityScorer()
        self._graph = graph

    def score_node(self, node: Any, tool_count: int = 0) -> TierResult:
        explicit = getattr(node, "task_tier", None)
        if explicit is not None:
            tier = TaskTier(explicit) if not isinstance(explicit, TaskTier) else explicit
            threshold = {
                TaskTier.micro: 0.12,
                TaskTier.routine: 0.37,
                TaskTier.reasoning: 0.62,
                TaskTier.critical: 0.87,
            }
            return TierResult(
                tier=tier,
                tier_score=threshold[tier],
                difficulty=0.0,
                impact=0.0,
                recoverability=0.0,
            )

        d = self._difficulty.score(node, tool_count=tool_count)

        node_id = getattr(node, "id", "")
        i = self._impact.score(node_id) if self._impact is not None else 0.40

        r = self._recoverability.score(node, graph=self._graph)

        w = self._weights
        tier_score = w.difficulty * d + w.impact * i + w.recoverability * (1.0 - r)

        tier_score = _clamp(tier_score)
        tier = TaskTier.from_score(tier_score)

        return TierResult(
            tier=tier,
            tier_score=tier_score,
            difficulty=d,
            impact=i,
            recoverability=r,
        )
