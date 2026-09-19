// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import { useDismissDetails } from "../useDismissDetails";
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
it("retains inside clicks and closes on outside pointer or keyboard focus", () => {
  function Panel() { const ref=useDismissDetails(); return createElement("details", {ref}, createElement("summary",null,"Team"), createElement("span",null,"Settings")); }
  const host=document.createElement("div"); document.body.append(host); const root=createRoot(host);
  try {
    act(() => root.render(createElement(Panel)));
    const details=host.querySelector("details")!;
    details.open=true;
    host.querySelector("span")!.dispatchEvent(new Event("pointerdown",{bubbles:true}));
    expect(details.open).toBe(true);
    document.body.dispatchEvent(new Event("pointerdown",{bubbles:true}));
    expect(details.open).toBe(false);
    details.open=true;
    document.body.dispatchEvent(new Event("focusin",{bubbles:true}));
    expect(details.open).toBe(false);
  } finally { act(() => root.unmount());host.remove(); }
});
it("opens on mouse hover, stays open when its summary is clicked, and closes after the mouse leaves", async () => {
  function Panel() { const ref=useDismissDetails(); return createElement("details", {ref}, createElement("summary",null,"Lead"), createElement("span",null,"Settings")); }
  const host=document.createElement("div"); document.body.append(host); const root=createRoot(host);
  const pointer=(type: string, pointerType="mouse") => Object.assign(new Event(type), { pointerType });
  try {
    act(() => root.render(createElement(Panel)));
    const details=host.querySelector("details")!;
    details.dispatchEvent(pointer("pointerenter", "touch"));
    expect(details.open).toBe(false);
    details.dispatchEvent(pointer("pointerenter"));
    expect(details.open).toBe(true);
    const click=new MouseEvent("click",{bubbles:true,cancelable:true});
    host.querySelector("summary")!.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
    details.dispatchEvent(pointer("pointerleave"));
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(details.open).toBe(false);
  } finally { act(() => root.unmount());host.remove(); }
});
