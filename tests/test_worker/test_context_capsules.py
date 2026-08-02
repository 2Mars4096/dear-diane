from __future__ import annotations

from dan.worker.context_capsules import (
    assemble_context_packet,
    build_tool_context_capsules,
    readiness_signal_from_capsules,
)


def test_file_read_capsule_retains_excerpt_and_raw_ref() -> None:
    capsules = build_tool_context_capsules(
        {
            "tool_id": "file_read",
            "tool_call_id": "tool-1",
            "model_call_id": "model-1",
            "arguments": {"path": "src/app.py", "start_line": 10, "end_line": 30},
            "ok": True,
            "result": {
                "path": "src/app.py",
                "content": "def important():\n    return 42\n",
                "line_count": 2,
                "size": 31,
            },
        },
        source_task_id="task-1",
        source_worker_id="worker-1",
        source_trace_id="trace-1",
    )

    assert len(capsules) == 1
    capsule = capsules[0]
    assert capsule.kind == "file_context"
    assert capsule.source_task_id == "task-1"
    assert capsule.raw_refs[0].kind == "file"
    assert capsule.raw_refs[0].path == "src/app.py"
    assert capsule.raw_refs[0].line_start == 10
    assert capsule.retained_evidence[0].text.startswith("def important")
    assert capsule.unlocks == ["file_context:src/app.py"]


def test_shell_test_capsule_marks_failed_test_as_provisional() -> None:
    capsules = build_tool_context_capsules(
        {
            "tool_id": "shell_command",
            "tool_call_id": "tool-2",
            "model_call_id": "model-2",
            "arguments": {"command": "pytest tests/test_app.py -q"},
            "ok": True,
            "result": {
                "exit_code": 1,
                "stdout": "F\n",
                "stderr": "AssertionError: expected 1\n",
            },
        }
    )

    capsule = capsules[0]
    assert capsule.kind == "test_result"
    assert capsule.artifact_state == "provisional"
    assert "failed" in capsule.summary
    assert capsule.open_questions
    assert "AssertionError" in capsule.retained_evidence[0].text


def test_web_search_capsule_keeps_urls_as_raw_refs() -> None:
    capsules = build_tool_context_capsules(
        {
            "tool_id": "web_search",
            "tool_call_id": "tool-3",
            "model_call_id": "model-3",
            "arguments": {"query": "DAN docs"},
            "ok": True,
            "result": {
                "count": 1,
                "provider": "test",
                "results": [
                    {
                        "title": "Docs",
                        "url": "https://example.com/docs",
                        "snippet": "Reference page",
                    }
                ],
            },
        }
    )

    capsule = capsules[0]
    assert capsule.kind == "web_evidence"
    assert capsule.raw_refs[0].kind == "url"
    assert capsule.raw_refs[0].url == "https://example.com/docs"
    assert capsule.retained_evidence[0].title == "Docs"


def test_context_packet_bounds_capsules_and_evidence() -> None:
    capsules = []
    for index in range(3):
        capsules.extend(
            build_tool_context_capsules(
                {
                    "tool_id": "file_read",
                    "tool_call_id": f"tool-{index}",
                    "model_call_id": "model",
                    "arguments": {"path": f"file{index}.py"},
                    "ok": True,
                    "result": {"path": f"file{index}.py", "content": "x" * 200},
                }
            )
        )

    packet = assemble_context_packet(
        capsules,
        target_task_id="downstream",
        max_capsules=2,
        max_evidence_chars=150,
    )
    signal = readiness_signal_from_capsules(packet.capsules, predicate="file_context_ready")

    assert len(packet.capsules) == 2
    assert len(packet.omitted_capsule_ids) == 1
    assert packet.total_retained_chars <= 150
    assert signal.ready_for_downstream is True
    assert signal.predicate == "file_context_ready"
