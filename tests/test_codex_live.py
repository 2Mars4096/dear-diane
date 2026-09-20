"""Exercise the real JSON-RPC transport against a local protocol fixture."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

from dan.native_workers.service import NativeTeam
from dan.server.chat_v2 import AgentRunCommand, build_v2_bridge_context
from dan.server.chat_request import ChatMessageRequest
from dan.server.chat_v2_store import ChatV2Store


@pytest.fixture
def app_server(tmp_path, monkeypatch):
    script = tmp_path / "codex-fixture"
    log = tmp_path / "rpc.jsonl"
    script.write_text(f"#!{sys.executable}\n" + '''import sys,json,os
log=open(os.environ["RPC_LOG"],"a")
def send(row): print(json.dumps(row),flush=True)
for line in sys.stdin:
 row=json.loads(line); log.write(json.dumps(row)+"\\n"); log.flush()
 method=row.get("method")
 if method=="initialized": continue
 result={}
 if method in ["thread/start","thread/resume"]: result={"thread":{"id":"thread-live"}}
 if method=="turn/start": result={"turn":{"id":"turn-live","status":"inProgress"}}
 if method=="turn/steer":
  if os.environ.get("REJECT_STEER"):
   send({"id":row["id"],"error":{"message":"Turn already finished"}}); continue
  result={"turnId":"turn-live"}
 send({"id":row["id"],"result":result})
 if method=="turn/start":
  send({"method":"item/agentMessage/delta","params":{"threadId":"thread-live","itemId":"m1","delta":"Working"}})
 if method=="turn/steer":
  send({"method":"item/completed","params":{"threadId":"thread-live","item":{"id":"m2","type":"agentMessage","text":"Follow-up received"}}})
  send({"method":"turn/completed","params":{"threadId":"thread-live","turn":{"id":"turn-live","status":"completed","error":None}}})
''')
    script.chmod(0o700)
    def launch(*args):
        return [str(script)], {**os.environ, "RPC_LOG":str(log)}
    monkeypatch.setattr("dan.native_workers.service.launch", launch)
    return log


@pytest.mark.asyncio
async def test_live_steer_stays_in_same_turn_and_streams_result(tmp_path, app_server):
    team = NativeTeam("run",str(tmp_path),{"codex":{"enabled":True,"_live_steering":True,"permission":"plan","model":"test-model","effort":"low"}}, tmp_path/"records")
    row = await team.start("codex","Wait for my correction")
    try:
        async with asyncio.timeout(5):
            while not team.steering:
                await asyncio.sleep(.01)
            client = team.steering[row["worker_id"]]
            result = await client.steer([SimpleNamespace(id="queued-1",text="Correct this",metadata={})])
            assert result["turnId"] == "turn-live"
            await team.tasks[row["worker_id"]]
        saved=team.records[row["worker_id"]]
        assert saved["status"]=="completed"
        assert saved["response"]=="Follow-up received"
        calls=[json.loads(line) for line in app_server.read_text().splitlines()]
        assert sum(call.get("method")=="turn/start" for call in calls)==1
        thread=next(call["params"] for call in calls if call.get("method")=="thread/start")
        assert thread["sandbox"]=="read-only" and thread["approvalPolicy"]=="never"
        steer=next(call["params"] for call in calls if call.get("method")=="turn/steer")
        assert steer["expectedTurnId"]=="turn-live" and steer["clientUserMessageId"]=="queued-1"
        assert not team.steering and not team.processes
    finally:
        await team.close()


def store_with_queue(tmp_path):
    store=ChatV2Store(tmp_path/"store")
    accepted=store.accept_bridge_context(build_v2_bridge_context(ChatMessageRequest(workflow_id="w",thread_id="t",message="Work",mode="agent")),stream_channel_id="test")
    store.update_run_metadata(accepted.run_id,{"selected_backend":"codex","live_steering":True},status="running")
    for text in ["First","Second"]:
        store.queue_agent_command(AgentRunCommand(command="continue_after_current",run_id=accepted.run_id,task_id=accepted.task_id,idempotency_key=text,payload={"text":text}))
    return store,accepted


def test_queue_steer_reuses_entry_and_preserves_other_waiting_messages(tmp_path):
    store,accepted=store_with_queue(tmp_path)
    first,second=store.get_task(accepted.task_id).queue_items
    store.steer_queued_item(accepted.run_id,second.id)
    items=store.claim_queued_run_items(accepted.run_id)
    assert [item.id for item in items]==[second.id]
    assert store.get_task(accepted.task_id).queue_items[0].status=="queued"
    with pytest.raises(ValueError,match="already left"):
        store.steer_queued_item(accepted.run_id,second.id)
    store.release_injected_run_items(accepted.run_id,reason="steer_not_accepted")
    assert len(store.get_task(accepted.task_id).queue_items)==2
    store.update_run_metadata(accepted.run_id,{"live_steering":False})
    with pytest.raises(ValueError,match="no live steering"):
        store.steer_queued_item(accepted.run_id,first.id)


@pytest.mark.asyncio
async def test_native_adapter_delivers_and_acknowledges_only_accepted_steer(tmp_path, monkeypatch, app_server):
    from dan.native_workers.lead import NativeLeadAdapter
    from dan.server.chat_v2_backend import run_agent_backend
    monkeypatch.setenv("DAN_GRAPHS_DIR",str(tmp_path/"graphs"))
    store,accepted=store_with_queue(tmp_path)
    second=store.get_task(accepted.task_id).queue_items[1]
    store.update_run_metadata(accepted.run_id, {"live_steering": False})
    async with asyncio.timeout(8):
        execution = asyncio.create_task(run_agent_backend(store,accepted.run_id,adapter=NativeLeadAdapter("codex")))
        while not store.get_run(accepted.run_id).metadata.get("live_steering"):
            await asyncio.sleep(.01)
        store.steer_queued_item(accepted.run_id,second.id)
        result=await execution
    assert result.status=="completed"
    task=store.get_task(accepted.task_id)
    delivered=next(item for item in task.queue_items if item.id==second.id)
    assert delivered.status=="completed"
    assert not task.metadata.get("live_steering")


@pytest.mark.asyncio
async def test_rejected_steer_does_not_close_active_turn_and_stop_cleans_transport(tmp_path, monkeypatch, app_server):
    monkeypatch.setenv("REJECT_STEER","1")
    team=NativeTeam("run",str(tmp_path),{"codex":{"enabled":True,"_live_steering":True,"permission":"auto","resume_session":"saved-session"}},tmp_path/"records")
    row=await team.start("codex","Wait")
    try:
        async with asyncio.timeout(5):
            while not team.steering:
                await asyncio.sleep(.01)
            client=team.steering[row["worker_id"]]
            with pytest.raises(ValueError,match="already finished"):
                await client.steer([SimpleNamespace(id="q",text="Update",metadata={})])
        calls=[json.loads(line) for line in app_server.read_text().splitlines()]
        assert next(call for call in calls if call.get("method")=="thread/resume")["params"]["threadId"]=="saved-session"
    finally:
        await team.close()
    assert team.records[row["worker_id"]]["status"]=="stopped"
    assert not team.steering and not team.processes
