import { expect, it } from "vitest";
import { visibleQueuedTranscript } from "../queuedTranscript";
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
