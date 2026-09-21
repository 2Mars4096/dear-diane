import asyncio
import json
import subprocess
import sys
import time

import pytest

from dan.processes import ProcessManager


def wait_for(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_process_outlives_its_starter_and_is_reattached(tmp_path):
    manager = ProcessManager(tmp_path)
    import shlex
    script = "import time; print('ready', flush=True); time.sleep(60)"
    record = manager.start(f"{sys.executable} -u -c {shlex.quote(script)}", str(tmp_path), name="sleeper")
    try:
        assert record["status"] == "running" and record["name"] == "sleeper"
        assert wait_for(lambda: "ready" in manager.logs(record["id"]))
        # A fresh manager (as after a backend restart) finds it still running by PID.
        again = ProcessManager(tmp_path)
        assert [row["status"] for row in again.list()] == ["running"]
        with pytest.raises(ValueError):
            again.remove(record["id"])
    finally:
        stopped = manager.stop(record["id"])
    assert stopped["status"] == "stopped"
    assert wait_for(lambda: subprocess.run(["ps", "-p", str(record["pid"])], capture_output=True).returncode != 0)
    manager.remove(record["id"])
    assert manager.list() == []


def test_finished_command_is_reported_exited_and_inputs_are_validated(tmp_path):
    manager = ProcessManager(tmp_path)
    record = manager.start("echo done", str(tmp_path))
    assert wait_for(lambda: manager.get(record["id"])["status"] == "exited")
    assert "done" in manager.logs(record["id"])
    with pytest.raises(ValueError):
        manager.start("   ", str(tmp_path))
    with pytest.raises(ValueError):
        manager.start("echo x", str(tmp_path / "missing"))
    with pytest.raises(ValueError):
        manager.get("../etc")


def test_bridge_starts_processes_for_sandboxed_agents(tmp_path, monkeypatch):
    from dan.native_workers import proc_bridge
    import dan.processes as processes
    monkeypatch.setattr(processes, "manager", lambda: ProcessManager(tmp_path / "graphs"))
    queue = tmp_path / "queue"; queue.mkdir()

    async def scenario():
        server = asyncio.create_task(proc_bridge.serve(queue, str(tmp_path), workspace_id="w", thread_id="t", backend="codex"))
        async def call(*args):
            proc = await asyncio.create_subprocess_exec(sys.executable, proc_bridge.__file__, "--queue", str(queue), *args, stdout=asyncio.subprocess.PIPE)
            out, _ = await proc.communicate()
            return json.loads(out)
        try:
            started = await call("start", "--name", "svc", "--", sys.executable, "-c", "import time; time.sleep(30)")
            assert started["ok"] and started["result"]["origin"] == "codex" and started["result"]["workspace_id"] == "w"
            listed = await call("list")
            assert [row["name"] for row in listed["result"]["processes"]] == ["svc"]
            return started["result"]["id"]
        finally:
            server.cancel(); await asyncio.gather(server, return_exceptions=True)
    process_id = asyncio.run(scenario())
    manager = ProcessManager(tmp_path / "graphs")
    assert manager.get(process_id)["status"] == "running"      # still alive after the bridge (the agent run) ended
    manager.stop(process_id)
