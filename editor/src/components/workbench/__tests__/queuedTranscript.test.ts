import { expect, it } from "vitest";
import { attachFollowupReply, visibleQueuedTranscript, withQueueReceipts } from "../queuedTranscript";
import type { ChatMessage } from "../../../types/chat";
import type { ChatV2TaskSnapshot } from "../../../lib/chatV2Api";
const messages = [
  { id: "u1", role: "user", content: "First" },
  { id: "a1", role: "assistant", content: "Working on it" },
  { id: "u2", role: "user", content: "Next" },
  { id: "a2", role: "assistant", content: "Queued after the current Diane run." },
] as ChatMessage[];
const base = {task_id:"t",thread_id:"s",status:"running",phase:"running",latest_progress:"",latest_artifact_refs:[],blocker:"",trace_refs:[]};
const tasks = (status: string): ChatV2TaskSnapshot[] => [{...base, metadata:{queue_items:[{id:"q",task_id:"t",lane:"continue_after_current",position:1,text:"Next",status, metadata:{command_payload:{client_message_id:"u2",client_assistant_id:"a2"}}}]}}] as ChatV2TaskSnapshot[];
it("keeps waiting requests and acknowledgements out, then reveals the delivered request", () => {
  expect(visibleQueuedTranscript(messages, tasks("queued")).map(m=>m.id)).toEqual(["u1","a1"]);
  expect(visibleQueuedTranscript(messages, tasks("injected")).map(m=>m.id)).toEqual(["u1","a1","u2"]);
  expect(visibleQueuedTranscript(messages, tasks("completed"), {a2:true}).map(m=>m.id)).toEqual(["u1","a1","u2","a2"]);
});
it("hides saved legacy queue pairs without hiding matching ordinary chat messages", () => {
  const legacy: ChatV2TaskSnapshot[] = [{...base, metadata:{queue_items:[{id:"q",task_id:"t",lane:"continue_after_current",position:1,text:"Next",status:"queued"}]}}];
  expect(visibleQueuedTranscript(messages, legacy).map(m=>m.id)).toEqual(["u1","a1"]);
  const ordinary = messages.map(m=>m.id==="a2"?{...m,content:"A real answer"}:m);
  expect(visibleQueuedTranscript(ordinary, legacy)).toEqual(ordinary);
});

it("recognizes checkpoint-append receipts from older installed clients", () => {
  const legacy = tasks("queued");
  delete legacy[0].metadata!.queue_items![0].metadata;
  const checkpoint = messages.map(m => m.id === "a2" ? {...m, content:"Queued for checkpoint append at position 1."} : m);
  expect(visibleQueuedTranscript(checkpoint, legacy).map(m => m.id)).toEqual(["u1", "a1"]);
  legacy[0].metadata!.queue_items![0].status = "injected";
  expect(visibleQueuedTranscript(checkpoint, legacy).map(m => m.id)).toEqual(["u1", "a1", "u2"]);
});

it("hides withdrawn requests and orphaned acknowledgement replies", () => {
  const withdrawn = tasks("cancelled");
  expect(visibleQueuedTranscript(messages, withdrawn).map(m => m.id)).toEqual(["u1", "a1"]);
  const orphan = [...messages.slice(0, 2), { id: "a9", role: "assistant", content: "Queued for checkpoint append at position 1." }] as ChatMessage[];
  expect(visibleQueuedTranscript(orphan, []).map(m => m.id)).toEqual(["u1", "a1"]);
  const streaming = [...messages.slice(0, 2), { id: "a9", role: "assistant", content: "Queued for checkpoint append at position 1.", taskRunRef: { runId: "r", status: "running" } }] as ChatMessage[];
  expect(visibleQueuedTranscript(streaming, []).map(m => m.id)).toEqual(["u1", "a1", "a9"]);
});


it("places the existing answer after accepted steering messages, preserving later turns", () => {
  const history = [messages[0], {...messages[1], taskRunRef: {runId:"r1", status:"completed"}}, messages[2],
    {id:"u3", role:"user", content:"Another steer"},
    {id:"u4", role:"user", content:"Later turn"},
    {id:"a4", role:"assistant", content:"Later answer", taskRunRef:{runId:"r4", status:"completed"}}] as ChatMessage[];
  const snapshot = tasks("completed");
  const item = snapshot[0].metadata!.queue_items![0];
  item.lane = "append";
  item.metadata = {...item.metadata, delivered_run_id:"r1"};
  snapshot[0].metadata!.queue_items!.push({...item, id:"q2", metadata:{delivered_run_id:"r1", command_payload:{client_message_id:"u3"}}});
  expect(visibleQueuedTranscript(history, snapshot).map(m=>m.id)).toEqual(["u1","u2","u3","a1","u4","a4"]);
  expect(history.map(m=>m.id)).toEqual(["u1","a1","u2","u3","u4","a4"]);
  expect(visibleQueuedTranscript(visibleQueuedTranscript(history, snapshot), snapshot)).toEqual(visibleQueuedTranscript(history, snapshot));
});

it("does not move the original answer for a waiting steer or a separately promoted Next", () => {
  const history = [messages[0], {...messages[1], taskRunRef:{runId:"r1",status:"completed"}}, messages[2],
    {...messages[3], content:"Next answer", taskRunRef:{runId:"r2",status:"completed"}}] as ChatMessage[];
  const snapshot = tasks("completed");
  snapshot[0].metadata!.queue_items![0].metadata!.delivered_run_id = "r2";
  expect(visibleQueuedTranscript(history, snapshot).map(m=>m.id)).toEqual(["u1","a1","u2","a2"]);
  snapshot[0].metadata!.queue_items![0].status = "queued";
  expect(visibleQueuedTranscript(history.slice(0,3), snapshot).map(m=>m.id)).toEqual(["u1","a1"]);
});

it("links a promoted reply before the stream starts and reuses it on reconnect", () => {
  const history = messages.slice(0,3);
  const reply = {...messages[3], content:""};
  const attached = attachFollowupReply(history, reply, "r2", "t");
  expect(attached.at(-1)?.taskRunRef).toEqual({runId:"r2",taskId:"t",status:"running"});
  const reconnected = attachFollowupReply(attached, attached.at(-1)!, "r2", "t");
  expect(reconnected.filter(m=>m.taskRunRef?.runId === "r2")).toHaveLength(1);
  expect(history).toHaveLength(3);
});

it("keeps a leased steer in Up next until acceptance, but reveals a promoted Next", () => {
  const snapshot = tasks("injected");
  const item = snapshot[0].metadata!.queue_items![0];
  item.metadata!.admitted_run_id = "r1";
  expect(visibleQueuedTranscript(messages, snapshot).map(m=>m.id)).toEqual(["u1","a1"]);
  item.metadata!.continued_run_id = "r2";
  expect(visibleQueuedTranscript(messages, snapshot).map(m=>m.id)).toEqual(["u1","a1","u2"]);
});

it('orders an accepted reply using terminal receipts after the backend removes the active queue item', () => {
 const snapshot = tasks('completed');
 const receipt = snapshot[0].metadata.queue_items![0];
 receipt.metadata = {...receipt.metadata, delivered_run_id:'r1'};
 snapshot[0].metadata.queue_receipts = [receipt]; snapshot[0].metadata.queue_items = [];
 const history = [messages[0],{...messages[1],taskRunRef:{runId:'r1',status:'running'}},messages[2]] as ChatMessage[];
 expect(visibleQueuedTranscript(history,snapshot).map(m=>m.id)).toEqual(['u1','u2','a1']);
});

it('preserves delivery receipts when stream-only progress is newer than the server queue snapshot', () => {
 const progress = tasks('queued')[0], server = tasks('completed')[0];
 const receipt = server.metadata.queue_items![0]; receipt.metadata = {...receipt.metadata,delivered_run_id:'r1'};
 server.metadata.queue_receipts = [receipt]; server.metadata.queue_items = [];
 const merged = withQueueReceipts(progress, server);
 expect(merged.metadata.queue_items).toEqual([]);
 const history = [messages[0],{...messages[1],taskRunRef:{runId:'r1',status:'running'}},messages[2]] as ChatMessage[];
 expect(visibleQueuedTranscript(history,[merged]).map(m=>m.id)).toEqual(['u1','u2','a1']);
});
