// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MainTabs, type MainTab } from "../MainTabs";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const tabs: MainTab[] = [{ id: "chat", kind: "chat", label: "Chat" }, { id: "pdf:a", kind: "pdf", label: "a.pdf" }];
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.spyOn(navigator, "platform", "get").mockReturnValue("MacIntel");
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); });
const press = (options: KeyboardEventInit) => {
  const event = new KeyboardEvent("keydown", { code: "KeyW", key: "w", bubbles: true, cancelable: true, ...options });
  act(() => window.dispatchEvent(event));
  return event;
};
it("closes only the active tab with Control-Command-W and leaves native Mac combinations alone", () => {
  const onClose = vi.fn();
  act(() => root.render(createElement(MainTabs, { tabs, active: "pdf:a", onSelect: () => {}, onClose })));
  for (const keys of [{ metaKey: true }, { metaKey: true, altKey: true }, { metaKey: true, shiftKey: true }, { ctrlKey: true }]) {
    expect(press(keys).defaultPrevented).toBe(false);
  }
  expect(onClose).not.toHaveBeenCalled();
  expect(press({ metaKey: true, ctrlKey: true }).defaultPrevented).toBe(true);
  expect(onClose).toHaveBeenCalledExactlyOnceWith("pdf:a");
  act(() => root.render(createElement(MainTabs, { tabs, active: "chat", onSelect: () => {}, onClose })));
  press({ metaKey: true, ctrlKey: true });
  expect(onClose).toHaveBeenLastCalledWith("chat");
});
it("does not repeat closes or close behind a dialog, while composing, or on the last tab", () => {
  const onClose = vi.fn();
  act(() => root.render(createElement(MainTabs, { tabs, active: "pdf:a", onSelect: () => {}, onClose })));
  press({ metaKey: true, ctrlKey: true, repeat: true });
  press({ metaKey: true, ctrlKey: true, isComposing: true });
  const dialog = document.createElement("dialog"); dialog.open = true; document.body.append(dialog);
  press({ metaKey: true, ctrlKey: true }); dialog.remove();
  act(() => root.render(createElement(MainTabs, { tabs: [tabs[0]], active: "chat", onSelect: () => {}, onClose })));
  press({ metaKey: true, ctrlKey: true });
  expect(onClose).not.toHaveBeenCalled();
});
it("offers Control-Alt-W on Windows/Linux without taking over Control-W", () => {
  vi.spyOn(navigator, "platform", "get").mockReturnValue("Linux x86_64");
  const onClose = vi.fn();
  act(() => root.render(createElement(MainTabs, { tabs, active: "pdf:a", onSelect: () => {}, onClose })));
  expect(press({ ctrlKey: true }).defaultPrevented).toBe(false);
  press({ ctrlKey: true, altKey: true });
  expect(onClose).toHaveBeenCalledExactlyOnceWith("pdf:a");
});
