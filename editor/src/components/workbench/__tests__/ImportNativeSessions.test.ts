// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ImportNativeSessions } from "../ImportNativeSessions";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
afterEach(() => vi.restoreAllMocks());
it("selects only importable sessions and retries failures without duplicating successes", async () => {
  vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(() => {});
  const sessions = [{ id:"a", title:"Long title ".repeat(100), can_import:true, backend:"codex" }, { id:"b", title:"Second", can_import:true, backend:"codex" }, { id:"c", title:"Unsupported", can_import:false, backend:"codex" }];
  const requests: string[] = [];
  let fail = true;
  vi.stubGlobal("fetch", vi.fn(async (_url, init) => {
    if (!init?.method) return { ok:true, json:async () => ({sessions}) };
    const body = JSON.parse(init.body);
    expect(body.fork).toBe(true);
    expect(body.workspace_id).toBe("project-2");
    requests.push(body.source_id);
    return { ok:body.source_id !== "b" || !fail, json:async () => ({id:body.source_id, detail:"Try again"}) };
  }));
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host); const onImport = vi.fn(); const onClose = vi.fn();
  try {
    await act(async () => root.render(createElement(ImportNativeSessions, { workspace:"/project", workspaceId:"project-2", onImport, onClose })));
    expect(host.querySelector('[role="tablist"]')).toBeNull();
    expect(host.querySelector("strong")!.textContent!.length).toBeLessThan(185);
    const button = (text: string) => [...host.querySelectorAll("button")].find((b) => b.textContent === text)!;
    const selectAll = host.querySelector<HTMLInputElement>('input[aria-label="Select all"]')!;
    const firstSession = host.querySelector<HTMLInputElement>('input[name="native-session"]')!;
    act(() => firstSession.click());
    expect(selectAll.indeterminate).toBe(true);
    act(() => selectAll.click());
    expect(selectAll.checked).toBe(true);
    expect(selectAll.indeterminate).toBe(false);
    act(() => selectAll.click());
    expect(firstSession.checked).toBe(false);
    act(() => selectAll.click());
    expect([...host.querySelectorAll<HTMLInputElement>('input[name="native-session"]')].map((i) => i.checked)).toEqual([true,true,false]);
    await act(async () => button("Import 2 as forks").click());
    expect(requests).toEqual(["a","b"]); expect(onClose).not.toHaveBeenCalled();
    fail = false;
    await act(async () => button("Import 1 as fork").click());
    expect(requests).toEqual(["a","b","b"]); expect(onImport).toHaveBeenCalledTimes(2); expect(onClose).toHaveBeenCalledTimes(1);
  } finally { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); }
});

it("shows populated source tabs and preserves other-tab selections when selecting or clearing all", async () => {
  vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(() => {});
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok:true, json:async () => ({sessions:[
    {id:"c1", backend:"codex", title:"Codex chat", can_import:true},
    {id:"c2", backend:"codex", title:"Another Codex chat", can_import:true},
    {id:"a1", backend:"claude", title:"Claude chat", can_import:true},
  ]}) })));
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(ImportNativeSessions, {workspace:"/project", workspaceId:"project", onImport:vi.fn(), onClose:vi.fn()})));
    const tabs = [...host.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Codex2", "Claude Code1"]);
    const all = () => host.querySelector<HTMLInputElement>('input[aria-label="Select all"]')!;
    act(() => all().click());
    act(() => tabs[1].click());
    expect(host.querySelector("strong")!.textContent).toBe("Claude chat");
    expect(all().checked).toBe(false);
    act(() => all().click());
    expect(host.textContent).toContain("Import 3 as forks");
    act(() => all().click());
    expect(host.textContent).toContain("Import 2 as forks");
    act(() => tabs[1].dispatchEvent(new KeyboardEvent("keydown", {key:"ArrowLeft", bubbles:true})));
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tabs[0]);
    expect(all().checked).toBe(true);
  } finally { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); }
});
