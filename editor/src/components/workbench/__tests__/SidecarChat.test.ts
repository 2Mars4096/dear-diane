// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import * as api from "../../../lib/chatV2Api";
import { ReadingChat } from "../../papers/ReadingChat";
import { SidecarChat } from "../SidecarChat";
vi.mock("../../../lib/chatV2Api", () => ({
  createChatV2Thread:vi.fn(async()=>({id:"side",messages:[]})), getChatV2Thread:vi.fn(async()=>({id:"side",messages:[]})), saveChatV2Thread:vi.fn(async()=>{}),
  createChatV2AgentRun:vi.fn(async()=>({task_run_ref:{run_id:"run"},v2_control_plane:{run_id:"run"}})), executeChatV2AgentRun:vi.fn(async()=>({})),
  getChatV2AgentRun:vi.fn(async()=>({status:"completed",metadata:{backend_result:{summary:"Side answer"}}})), getChatV2AgentRunEvents:vi.fn(async()=>[]),postChatV2AgentRunCommand:vi.fn(async()=>({}))
}));
beforeEach(() => { vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ runtimes: [{ id: 'claude', label: 'Claude', available: true, accounts: [], models: [], efforts: [] }, { id: 'codex', label: 'Codex', available: true, accounts: [], models: [], efforts: [] }] }), { status: 200 }))); });
afterEach(() => vi.unstubAllGlobals());
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
    expect(api.createChatV2AgentRun).toHaveBeenCalledWith(expect.objectContaining({thread_id:"side",surface:"chat:side",surface_type:"chat",surface_id:"side",message:expect.stringContaining("Quoted passage")}));
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

it('sends a selected PDF crop as an attachment with source context', async () => {
  vi.clearAllMocks(); localStorage.clear();
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(SidecarChat, {
      parentId: 'pdf', workflowId: 'project', workspaceId: 'project', workspaceRoot: '/work', context: [],
      selection: { text: 'Selected area', token: 1, context: 'Selected image: /work/crop.png', attachment: { id: 'crop', kind: 'figure', name: 'crop.png', path: '/work/crop.png', mimeType: 'image/png' } },
      purpose: { title: 'Reading', framing: 'Read this PDF', placeholder: '', empty: '' },
      execution: { backend: 'claude' }, leadLabel: 'Claude', onClose: vi.fn(), onCreated: vi.fn(),
    })));
    const input = host.querySelector('textarea')!;
    act(() => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'Explain the marked paragraph'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => Array.from(host.querySelectorAll('button')).find(button => button.textContent === 'Send')!.click());
    expect(api.createChatV2AgentRun).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringContaining('/work/crop.png'), surface_context: expect.objectContaining({ appended_attachments: [expect.objectContaining({ kind: 'figure', path: '/work/crop.png' })] }) }));
    expect(api.saveChatV2Thread).toHaveBeenCalledWith('project', 'side', expect.objectContaining({ messages: expect.arrayContaining([expect.objectContaining({ role: 'user', attachments: [expect.objectContaining({ path: '/work/crop.png' })] })]) }));
  } finally { act(() => root.unmount()); host.remove(); localStorage.clear(); }
});


it('retains the draft and rolls back optimistic messages after admission fails', async () => {
  vi.clearAllMocks(); localStorage.clear();
  vi.mocked(api.createChatV2AgentRun).mockRejectedValueOnce(new Error('Request rejected'));
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(SidecarChat, { parentId: 'parent', workflowId: 'project', workspaceId: 'project', workspaceRoot: '/work', context: [], selection: { text: '', token: 0 }, execution: { backend: 'claude' }, leadLabel: 'Claude', onClose: vi.fn(), onCreated: vi.fn() })));
    const input = host.querySelector('textarea')!;
    act(() => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'summarize'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => Array.from(host.querySelectorAll('button')).find(button => button.textContent === 'Send')!.click());
    expect(input.value).toBe('summarize');
    expect(host.querySelector('[role=alert]')?.textContent).toContain('Request rejected');
    expect(host.querySelectorAll('article')).toHaveLength(0);
    expect(vi.mocked(api.saveChatV2Thread).mock.calls.at(-1)?.[2].messages).toEqual([]);
    expect(api.executeChatV2AgentRun).not.toHaveBeenCalled();
  } finally { act(() => root.unmount()); host.remove(); localStorage.clear(); }
});

it('uses independent sidebar mode and lead selections for execution', async () => {
  vi.clearAllMocks(); localStorage.clear();
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
  const execution = { backend: 'claude', profile_policy: { permission_mode: 'auto' } };
  try {
    await act(async () => root.render(createElement(SidecarChat, { parentId: 'parent', workflowId: 'project', workspaceId: 'project', workspaceRoot: '/work', context: [], selection: { text: '', token: 0 }, execution, leadLabel: 'Claude', onClose: vi.fn(), onCreated: vi.fn() })));
    act(() => { const mode = host.querySelector('select[aria-label="Mode"]') as HTMLSelectElement; mode.value = 'plan'; mode.dispatchEvent(new Event('change', { bubbles: true })); const lead = host.querySelector('select[aria-label="Lead agent"]') as HTMLSelectElement; lead.value = 'codex'; lead.dispatchEvent(new Event('change', { bubbles: true })); });
    const input = host.querySelector('textarea')!;
    act(() => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'Inspect'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => Array.from(host.querySelectorAll('button')).find(button => button.textContent === 'Send')!.click());
    expect(api.executeChatV2AgentRun).toHaveBeenCalledWith('run', expect.objectContaining({ backend: 'native_codex', profile_policy: expect.objectContaining({ permission_mode: 'plan', autonomy_mode: 'review' }) }));
    expect(execution).toEqual({ backend: 'claude', profile_policy: { permission_mode: 'auto' } });
    expect(host.querySelector('button[aria-label="Attach files"]')).not.toBeNull();
  } finally { act(() => root.unmount()); host.remove(); localStorage.clear(); }
});


it('reopens a saved Next message and starts it after the previous run finishes', async () => {
  vi.clearAllMocks(); localStorage.clear();
  localStorage.setItem('dan.sidecar.v1:project:parent', JSON.stringify({ threadId: 'side', runId: 'previous', assistantId: 'previous-answer' }));
  localStorage.setItem('dan.sidecar.v1:project:parent:next', JSON.stringify({ draft: 'Continue the analysis', quote: '', quoteContext: '', files: [], execution: { backend: 'native_codex' } }));
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(SidecarChat, { parentId: 'parent', workflowId: 'project', workspaceId: 'project', workspaceRoot: '/work', context: [], selection: { text: '', token: 0 }, execution: { backend: 'claude' }, leadLabel: 'Claude', onClose: vi.fn(), onCreated: vi.fn() })));
    expect(api.createChatV2Thread).not.toHaveBeenCalled();
    expect(api.createChatV2AgentRun).toHaveBeenCalledTimes(1);
    expect(api.createChatV2AgentRun).toHaveBeenCalledWith(expect.objectContaining({ thread_id: 'side', message: expect.stringContaining('Continue the analysis') }));
    expect(api.executeChatV2AgentRun).toHaveBeenCalledWith('run', { backend: 'native_codex' });
    expect(localStorage.getItem('dan.sidecar.v1:project:parent:next')).toBeNull();
  } finally { act(() => root.unmount()); host.remove(); localStorage.clear(); }
});


it('includes the open PDF path in an unselected whole-file request', async () => {
  vi.clearAllMocks(); localStorage.clear();
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(ReadingChat, { file: { name: 'RFS_SRFS_guide.pdf', path: '/papers/RFS_SRFS_guide.pdf', url: '/api/pdf' }, onLink: vi.fn(), workflowId: 'project', workspaceId: 'project', workspaceRoot: '/work', selection: { text: '', token: 0 }, execution: { backend: 'claude' }, leadLabel: 'Claude', onClose: vi.fn(), onCreated: vi.fn() })));
    const input = host.querySelector('textarea')!;
    act(() => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'summarize'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => Array.from(host.querySelectorAll('button')).find(button => button.textContent === 'Send')!.click());
    const request = vi.mocked(api.createChatV2AgentRun).mock.calls[0][0];
    expect(request.message).toContain('/papers/RFS_SRFS_guide.pdf');
    expect(request.message).toContain('Read the file when a whole-document answer is needed');
    expect(request.message).toContain('summarize');
  } finally { act(() => root.unmount()); host.remove(); localStorage.clear(); }
});
