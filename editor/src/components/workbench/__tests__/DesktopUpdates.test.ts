// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { DesktopUpdates } from "../DesktopUpdates";
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
afterEach(() => vi.unstubAllGlobals());
it("supports local prepare/install without pretending an unconfigured release channel is current", async () => {
  const idle = { phase: "idle", currentVersion: "1", version: "", source: "", message: "", percent: 0, releaseConfigured: false, localSupported: true };
  const api = { status: vi.fn().mockResolvedValue(idle), action: vi.fn().mockResolvedValue({ ...idle, phase: "ready", version: "2", source: "local" }) };
  Object.defineProperty(window, "electronAPI", { configurable: true, value: { isElectron: true, updates: api } });
  const host = document.createElement("div"); const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(DesktopUpdates)));
    expect(host.textContent).toContain("Published updates aren’t configured");
    const button = (label: string) => [...host.querySelectorAll("button")].find(b => b.textContent === label)!;
    expect(button("Check for updates").disabled).toBe(false);
    await act(async () => button("Choose a different build…").click());
    expect(api.action).toHaveBeenCalledWith("choose");
    expect(api.action).not.toHaveBeenCalledWith("install");
    await act(async () => button("Install and restart").click());
    expect(api.action).toHaveBeenCalledWith("install");
  } finally { act(() => root.unmount()); delete window.electronAPI; }
});

it("offers browser sign-in, shows the device code, and supports cancellation", async () => {
  const base = { phase: "idle", currentVersion: "1", version: "", source: "", message: "", percent: 0, releaseConfigured: true, localSupported: true };
  const signedOut = { ...base, github: { status: "signed_out", login: "", code: "", message: "Sign in to GitHub" } };
  const api = { status: vi.fn().mockResolvedValue(signedOut), action: vi.fn()
    .mockResolvedValueOnce({ ...base, github: { status: "signing_in", login: "", code: "ABCD-1234", message: "Authorize in your browser" } })
    .mockResolvedValueOnce(signedOut) };
  Object.defineProperty(window, "electronAPI", { configurable: true, value: { isElectron: true, updates: api } });
  const host = document.createElement("div"); const root = createRoot(host);
  const button = (text: string) => [...host.querySelectorAll('button')].find(b => b.textContent === text)!;
  try {
    await act(async () => root.render(createElement(DesktopUpdates)));
    await act(async () => button("Sign in to GitHub").click());
    expect(api.action).toHaveBeenCalledWith("github-sign-in");
    expect(host.textContent).toContain("ABCD-1234");
    expect(host.querySelector('a')?.getAttribute('href')).toBe('https://github.com/login/device');
    await act(async () => button("Cancel sign-in").click());
    expect(api.action).toHaveBeenCalledWith("github-cancel");
    expect(host.textContent).not.toContain("ABCD-1234");
  } finally { act(() => root.unmount()); delete window.electronAPI; }
});

it("installs a detected local update without opening the file chooser", async () => {
  const api = { status: vi.fn().mockResolvedValue({phase:"idle",currentVersion:"1",localSupported:true,localBuildAvailable:true}), action: vi.fn().mockResolvedValue({phase:"installing",message:"Restarting"}) };
  Object.defineProperty(window,"electronAPI",{configurable:true,value:{isElectron:true,updates:api}});
  const host=document.createElement("div");const root=createRoot(host);
  try {
    await act(async()=>root.render(createElement(DesktopUpdates)));
    const button=[...host.querySelectorAll("button")].find(b=>b.textContent==="Install local update")!;
    await act(async()=>button.click());
    expect(api.action).toHaveBeenCalledWith("install-local");
    expect(api.action).not.toHaveBeenCalledWith("choose");
  } finally {act(()=>root.unmount());delete window.electronAPI;}
});
