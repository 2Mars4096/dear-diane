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
