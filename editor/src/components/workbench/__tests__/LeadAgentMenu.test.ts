// @vitest-environment happy-dom
import { act, createElement, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { LeadAgentMenu, type LeadAgentId } from "../LeadAgentMenu";
import type { WorkerProfiles } from "../NativeWorkers";
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it("keeps lead, account, model, reasoning, and fast settings inside a single dropdown", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ok:true, json:async () => ({runtimes:[{id:"codex", label:"Codex", available:true, accounts:[{id:"default", label:"Default"},{id:"work",label:"Work"}], models:["gpt-test"], efforts:["high"], fast:true}]})})));
  const host=document.createElement("div"); document.body.append(host); const root=createRoot(host);
  const saved = vi.fn();
  function Harness() {
    const [selected, select] = useState<LeadAgentId>("native");
    const [profiles, update] = useState<WorkerProfiles>({});
    return createElement(LeadAgentMenu, {selected,onChange:select,profiles,onProfilesChange:(next) => { update(next); saved(next); },modelId:"default",modelOptions:[{id:"default",label:"Default"}],onModelChange:vi.fn()});
  }
  try {
    await act(async () => root.render(createElement(Harness)));
    const agent=host.querySelector<HTMLSelectElement>('select[aria-label="Lead agent"]')!;
    await act(async () => {agent.value="codex"; agent.dispatchEvent(new Event("change",{bubbles:true}));});
    expect(host.querySelectorAll("summary")).toHaveLength(1);
    const select = (label: string) => [...host.querySelectorAll("label")].find((node) => node.textContent?.startsWith(label))!.querySelector("select")!;
    for (const [label,value] of [["Account","work"],["Model","gpt-test"],["Reasoning","high"]]) {
      act(() => {const input=select(label);input.value=value;input.dispatchEvent(new Event("change",{bubbles:true}));});
    }
    act(() => host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    expect(saved.mock.lastCall![0].codex).toMatchObject({account:"work",model:"gpt-test",effort:"high",fast:true});
  } finally {act(() => root.unmount());host.remove();}
});
