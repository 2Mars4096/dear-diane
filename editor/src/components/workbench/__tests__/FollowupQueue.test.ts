// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { FollowupQueue } from "../FollowupQueue";
Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
it("steers the existing queue ID, updates its task, and keeps failed entries available", async()=>{
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 const onTask=vi.fn(); const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({task:{task_id:"t"}})});
 vi.stubGlobal("fetch",fetcher);
 const props={rows:[{id:"queue:q1",detail:"Request",status:"queued",kind:"followup",lane:"continue_after_current",runId:"r1"}],runId:"r1",canSteer:true,onTask};
 try {
  await act(async()=>root.render(createElement(FollowupQueue,props)));
  await act(async()=>host.querySelector("button")!.click());
  expect(fetcher).toHaveBeenCalledWith("/api/v2/agent-runs/r1/queue/q1/steer",{method:"POST"});
  expect(onTask).toHaveBeenCalledWith({task_id:"t"});
  fetcher.mockResolvedValueOnce({ok:false,json:async()=>({detail:"Turn has ended"})});
  await act(async()=>host.querySelector("button")!.click());
  expect(host.querySelector('[role="alert"]')?.textContent).toBe("Turn has ended");
  expect(host.querySelector("button")!.disabled).toBe(false);
  await act(async()=>root.render(createElement(FollowupQueue,{...props,canSteer:false})));
  expect(host.querySelector("button")!.disabled).toBe(true);
 } finally {await act(async()=>root.unmount());host.remove();vi.unstubAllGlobals();}
});

it("removes a waiting entry and reports its saved request", async()=>{
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 const onTask=vi.fn(); const onRemoved=vi.fn(); const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({task:{task_id:"t"}})});
 vi.stubGlobal("fetch",fetcher);
 const row={id:"queue:q2",detail:"Later",status:"queued",kind:"followup",lane:"append",runId:"r1",clientMessageId:"u2"};
 try {
  await act(async()=>root.render(createElement(FollowupQueue,{rows:[row],runId:"r1",canSteer:true,onTask,onRemoved})));
  await act(async()=>host.querySelector<HTMLButtonElement>('[aria-label="Remove from Up next"]')!.click());
  expect(fetcher).toHaveBeenCalledWith("/api/v2/agent-runs/r1/queue/q2/cancel",{method:"POST"});
  expect(onTask).toHaveBeenCalledWith({task_id:"t"});
  expect(onRemoved).toHaveBeenCalledWith(row);
 } finally {await act(async()=>root.unmount());host.remove();vi.unstubAllGlobals();}
});
