// @vitest-environment happy-dom
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SideTabs, type SideTabItem } from "../SideTabs";
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const tabs: SideTabItem[] = [
  { id: "chat", label: "Reading" }, { id: "notes", label: "Notes" },
  { id: "files", label: "Files" }, { id: "literature", label: "Literature" },
  { id: "preview", label: "Preview" }, { id: "activity", label: "Activity", live: true },
  { id: "team", label: "Team", count: 2 }, { id: "processes", label: "Processes" },
];
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
function setup(items = tabs) {
  const onSelect = vi.fn(); const onClose = vi.fn();
  act(() => root.render(h(SideTabs, { tabs: items, active: "files", reading: true, onSelect, onClose })));
  return { onSelect, onClose, trigger: host.querySelector<HTMLButtonElement>(".wb-side-switcher")! };
}
function key(target: Element, value: string) {
  act(() => target.dispatchEvent(new KeyboardEvent("keydown", { key: value, bubbles: true })));
}
it("shows the current tool, exposes all eight tools on demand, and returns focus after selection", () => {
  const { trigger, onSelect } = setup();
  expect(trigger.textContent).toBe("Files");
  expect(host.querySelector('[role="menu"]')).toBeNull();
  act(() => trigger.click());
  const items = host.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]');
  expect(items).toHaveLength(8); expect(document.activeElement).toBe(items[2]);
  expect(items[0].textContent).toContain("Discuss the open document");
  expect(items[3].textContent).toContain("Import PDFs");
  act(() => items[3].click());
  expect(onSelect).toHaveBeenCalledWith("literature");
  expect(trigger.getAttribute("aria-expanded")).toBe("false"); expect(document.activeElement).toBe(trigger);
});
it("supports arrow navigation, Home, End, Escape and outside dismissal", () => {
  const { trigger, onSelect } = setup(); key(trigger, "ArrowDown");
  const items = host.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]');
  key(items[2], "ArrowDown"); expect(document.activeElement).toBe(items[3]);
  key(items[3], "End"); expect(document.activeElement).toBe(items[7]);
  key(items[7], "ArrowDown"); expect(document.activeElement).toBe(items[0]);
  key(items[0], "ArrowUp"); expect(document.activeElement).toBe(items[7]);
  key(items[7], "Home"); expect(document.activeElement).toBe(items[0]);
  key(items[0], "Escape"); expect(document.activeElement).toBe(trigger);
  expect(onSelect).not.toHaveBeenCalled(); expect(trigger.getAttribute("aria-expanded")).toBe("false");
  act(() => trigger.click());
  act(() => document.body.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true })));
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  act(() => trigger.click());
  const outside = document.createElement("button"); document.body.append(outside);
  act(() => outside.focus()); outside.remove();
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
});
it("retains contextual tools, background activity and the close action", () => {
  const { trigger, onSelect, onClose } = setup(tabs.filter((item) => item.id !== "files"));
  act(() => trigger.click());
  expect(host.querySelector('[aria-checked="true"]')?.textContent).toContain("Files");
  key(document.activeElement!, "Escape");
  act(() => host.querySelector<HTMLButtonElement>(".wb-side-running")!.click());
  expect(onSelect).toHaveBeenCalledWith("activity");
  act(() => host.querySelector<HTMLButtonElement>(".wb-side-close")!.click()); expect(onClose).toHaveBeenCalledOnce();
});
