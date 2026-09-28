// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import * as api from "../../../lib/chatV2Api";
import { SidecarChat } from "../SidecarChat";
vi.mock("../../../lib/chatV2Api", () => ({
  createChatV2Thread:vi.fn(async()=>({id:"side",messages:[]})), getChatV2Thread:vi.fn(async()=>({id:"side",messages:[]})), saveChatV2Thread:vi.fn(async()=>{}),
  createChatV2AgentRun:vi.fn(async()=>({task_run_ref:{run_id:"run"},v2_control_plane:{run_id:"run"}})), executeChatV2AgentRun:vi.fn(async()=>({})),
  getChatV2AgentRun:vi.fn(async()=>({status:"completed",metadata:{backend_result:{summary:"Side answer"}}})), getChatV2AgentRunEvents:vi.fn(async()=>[]),postChatV2AgentRunCommand:vi.fn(async()=>({}))
}));
Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
it("creates a linked side chat and saves the response only to that thread", async()=>{
  localStorage.clear();
  const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
  try {
    await act(async()=>root.render(createElement(SidecarChat,{parentId:"parent",workflowId:"project",workspaceId:"project",workspaceRoot:"/work",context:[{id:"main",role:"assistant",content:"Parent text",timestamp:1}],selection:{text:"Quoted passage",token:1},execution:{backend:"claude"},leadLabel:"Claude",onClose:vi.fn(),onCreated:vi.fn()})));
    const input=host.querySelector("textarea")!;
    act(()=>{Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value")!.set!.call(input,"Explain this");input.dispatchEvent(new Event("input",{bubbles:true}));});
    await act(async()=>[...host.querySelectorAll("button")].find(b=>b.textContent==="Send")!.click());
    expect(api.createChatV2Thread).toHaveBeenCalledWith("project",expect.objectContaining({parent_thread_id:"parent"}));
    expect(api.createChatV2AgentRun).toHaveBeenCalledWith(expect.objectContaining({thread_id:"side",message:expect.stringContaining("Quoted passage")}));
    expect(api.executeChatV2AgentRun).toHaveBeenCalledWith("run",{backend:"claude"});
    const calls=vi.mocked(api.saveChatV2Thread).mock.calls;
    expect(calls.every(call=>call[1]==="side")).toBe(true);
    expect(calls.at(-1)![2].messages!.at(-1)!.content).toBe("Side answer");
    expect(api.getChatV2Thread).not.toHaveBeenCalled(); // no create/load race
    expect(JSON.parse(localStorage.getItem("dan.sidecar.v1:project:parent")!)).toEqual({threadId:"side"});
  } finally {act(()=>root.unmount());host.remove();localStorage.clear();}
});

it('resumes a server-owned reading conversation and persists its run link before execution', async () => {
  vi.clearAllMocks(); localStorage.clear();
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
  const order: string[] = [];
  vi.mocked(api.executeChatV2AgentRun).mockImplementationOnce(async () => { order.push('execute'); return {} as Awaited<ReturnType<typeof api.executeChatV2AgentRun>>; });
  try {
    await act(async () => root.render(createElement(SidecarChat, {
      parentId: 'reader:/papers/a.pdf', workflowId: '_dan_reading', workspaceId: '_dan_reading', workspaceRoot: '/papers', context: [],
      selection: { text: '', token: 0 }, execution: { backend: 'claude' }, leadLabel: 'Claude', onClose: vi.fn(), onCreated: vi.fn(),
      initialLink: { threadId: 'side' }, onLinkChange: async link => { order.push(link.runId ? 'save-run' : 'save-complete'); },
      purpose: { title: 'Reading paper', framing: 'Discuss this paper.', placeholder: 'Ask', empty: 'Read' },
    })));
    expect(api.getChatV2Thread).toHaveBeenCalledWith('_dan_reading', 'side');
    const input = host.querySelector('textarea')!;
    act(() => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'Explain the mechanism'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => Array.from(host.querySelectorAll('button')).find(button => button.textContent === 'Send')!.click());
    expect(api.createChatV2Thread).not.toHaveBeenCalled();
    expect(order[0]).toBe('save-run');
    expect(order).toContain('execute');
    expect(order).toContain('save-complete');
    expect(api.createChatV2AgentRun).toHaveBeenCalledWith(expect.objectContaining({ workflow_id: '_dan_reading', thread_id: 'side' }));
  } finally { act(() => root.unmount()); host.remove(); localStorage.clear(); }
});
