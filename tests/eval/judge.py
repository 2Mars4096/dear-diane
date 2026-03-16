"""LLM-as-judge semantic quality assessment for generated workflow graphs.

Sends prompt + graph JSON to an LLM with a scoring rubric. Advisory only —
does not affect pass/fail status.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)

JUDGE_RUBRIC = """\
You are evaluating a workflow graph that was generated from a natural language prompt.

## Original Prompt
{prompt}

## Generated Graph (JSON)
{graph_json}

## Scoring Rubric
Rate each dimension from 0-10:

1. **prompt_faithfulness**: Does the graph structure match what the prompt asked for? Are the right patterns used (chain, review loop, fan-out, RAG)?
2. **node_specificity**: Are individual node prompts specific enough to produce useful output? Or are they vague placeholders?
3. **data_flow_correctness**: Do edges, ports, and template variables connect nodes correctly? Does upstream data reach downstream nodes?
4. **executability**: Would this graph produce useful output if actually run? Are tool_ids valid? Is code real (not stubs)?

Respond with ONLY a JSON object (no markdown, no explanation):
{{"prompt_faithfulness": <0-10>, "node_specificity": <0-10>, "data_flow_correctness": <0-10>, "executability": <0-10>}}
"""


async def judge_graph(
    prompt: str,
    graph_dict: dict[str, Any],
    *,
    base_url: str = "http://localhost:8080",
) -> dict[str, int] | None:
    """Score a generated graph using LLM-as-judge.

    Returns dict with 4 scores (0-10 each) or None on failure.
    """
    graph_json = json.dumps(graph_dict, indent=2)
    if len(graph_json) > 15000:
        graph_json = graph_json[:15000] + "\n... (truncated)"

    rubric_filled = JUDGE_RUBRIC.format(prompt=prompt, graph_json=graph_json)

    try:
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base_url}/api/chat/message",
                json={
                    "message": rubric_filled,
                    "mode": "ask",
                    "workflow_id": f"__eval_judge__-{uuid4().hex[:8]}",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            channel_id = data.get("stream_channel_id", "")
            if not channel_id:
                log.warning("judge: no stream channel returned")
                return None

            from tests.eval.client import DanClient

            judge_client = DanClient(base_url)
            response_text = ""
            try:
                async for event in judge_client.stream_events(channel_id, timeout=60.0):
                    etype = event.get("type", "")
                    if etype in ("chat_text", "chat_token"):
                        response_text += event.get("text", event.get("token", ""))
                    elif etype == "chat_complete":
                        content = event.get("content", "")
                        if content and not response_text:
                            response_text = content
                        if event.get("detected_mode") != "progress_ack":
                            break
            finally:
                await judge_client.close()

        return _parse_scores(response_text)
    except Exception as exc:
        log.warning("judge scoring failed: %s", exc)
        return None


def _parse_scores(text: str) -> dict[str, int] | None:
    """Extract the 4-score JSON from LLM response text."""
    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx == -1 or end_idx == -1 or start_idx > end_idx:
        return None
    try:
        data = json.loads(text[start_idx : end_idx + 1])
        required = {"prompt_faithfulness", "node_specificity", "data_flow_correctness", "executability"}
        if not required.issubset(data.keys()):
            return None
        return {k: max(0, min(10, int(v))) for k, v in data.items() if k in required}
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
