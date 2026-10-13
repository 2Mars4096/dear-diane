// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { WorkbenchConversation } from "../WorkbenchConversation";
// Test DOM identity independently of the known happy-dom sanitizer limitation.
vi.mock("../../../lib/sanitizeHtml", () => ({ sanitizeHtml: (html: string) => html }));
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
it("keeps rendered text nodes and the native selection when the quote toolbar opens", () => {
  const host=document.createElement("div"); document.body.append(host); const root=createRoot(host);
  const sidecar=vi.fn(); const quote=vi.fn();
  try {
    act(() => root.render(createElement(WorkbenchConversation, {messages:[{id:"a", role:"assistant",content:"Keep this selected text.",timestamp:1}], pending:{},loading:false,status:"Ready",onQuote:quote,onSidecar:sidecar})));
    const paragraph=host.querySelector(".dan-markdown p")!;
    const text=paragraph.firstChild!;
    const range=document.createRange();range.setStart(text,0);range.setEnd(text,9);
    window.getSelection()!.addRange(range);
    act(() => host.querySelector(".wb-conversation-scroll")!.dispatchEvent(new MouseEvent("mouseup",{bubbles:true})));
    expect(host.querySelector(".dan-markdown p")!.firstChild).toBe(text);
    expect(window.getSelection()!.toString()).toBe("Keep this");
    const reply=[...host.querySelectorAll("button")].find(b=>b.textContent=== "Reply to selection")!;
    act(()=>reply.click()); expect(quote).toHaveBeenCalledWith("Keep this", ["a"]);
    window.getSelection()!.addRange(range);
    act(() => host.querySelector(".wb-conversation-scroll")!.dispatchEvent(new MouseEvent("mouseup",{bubbles:true})));
    const button=[...host.querySelectorAll("button")].find(b=>b.textContent==="Sidecar chat")!;
    act(()=>button.click());expect(sidecar).toHaveBeenCalledWith("Keep this");
  } finally { window.getSelection()?.removeAllRanges();act(()=>root.unmount());host.remove(); }
});

it("keeps plan choices attached to their response when later messages arrive", () => {
  const host=document.createElement("div"); const root=createRoot(host);
  try {
    act(()=>root.render(createElement(WorkbenchConversation, {
      messages:[{id:"plan",role:"assistant",content:"Review these choices.",timestamp:1,taskRunRef:{taskId:"task",runId:"run",status:"completed"}}, {id:"next",role:"user",content:"One more detail",timestamp:2}],
      pending:{},loading:false,status:"Ready",onQuote:vi.fn(),
      planReview:{runId:"run",version:1,content:createElement("button",null,"Use recommendation")},
    })));
    expect(host.querySelector('[data-message-id="plan"] button')?.textContent).toBe("Use recommendation");
    expect(host.querySelector('[data-message-id="next"] button')).toBeNull();
    expect(host.querySelectorAll(".wb-conversation-scroll")).toHaveLength(1);
  } finally {act(()=>root.unmount());host.remove();}
});
