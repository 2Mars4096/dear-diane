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
    expect(button("Check for updates").disabled).toBe(true);
    expect(button("Install local update")).toBeUndefined();
    expect(host.textContent).not.toContain("GitHub");
    await act(async () => button("Choose a different build…").click());
    expect(api.action).toHaveBeenCalledWith("choose");
    expect(api.action).not.toHaveBeenCalledWith("install");
    await act(async () => button("Install and restart").click());
    expect(api.action).toHaveBeenCalledWith("install");
  } finally { act(() => root.unmount()); delete window.electronAPI; }
});

it("checks for updates without offering account controls, even with stale login state", async () => {
  const state = { phase: "idle", currentVersion: "1", version: "", source: "", message: "", percent: 0, releaseConfigured: true, localSupported: true, github: { status: "signed_out", login: "", code: "", message: "Sign in" } };
  const api = { status: vi.fn().mockResolvedValue(state), action: vi.fn().mockResolvedValue({ ...state, message: "No published release is available yet." }) };
  Object.defineProperty(window, "electronAPI", { configurable: true, value: { updates: api } });
  const host = document.createElement("div"); const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(DesktopUpdates)));
    expect(host.textContent).not.toMatch(/GitHub|sign in|connection/i);
    const check = [...host.querySelectorAll("button")].find(button => button.textContent === "Check for updates")!;
    expect(check.disabled).toBe(false);
    await act(async () => check.click());
    expect(api.action).toHaveBeenCalledExactlyOnceWith("check");
    expect(host.textContent).toContain("No published release is available yet.");
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

it('shows and clears update dots on Settings and the update action together', async () => {
  const { SettingsUpdateButton } = await import('../SettingsUpdateButton');
  const { setDesktopUpdateStatus } = await import('../desktopUpdateStatus');
  const state = { phase: 'available', currentVersion: '0.1.0', version: '0.1.1', source: 'release', message: 'Update available', percent: 0, releaseConfigured: true, localSupported: true };
  Object.defineProperty(window, 'electronAPI', { configurable: true, value: { updates: { status: vi.fn().mockResolvedValue(state), action: vi.fn() } } });
  const host = document.createElement('div'); const root = createRoot(host);
  try {
    await act(async () => root.render(createElement('div', null, createElement(SettingsUpdateButton, { onClick: vi.fn() }), createElement(DesktopUpdates))));
    expect(host.querySelectorAll('.wb-update-dot')).toHaveLength(2);
    expect(host.querySelector('[aria-label="Settings, update available"]')).not.toBeNull();
    await act(async () => setDesktopUpdateStatus({ ...state, phase: 'ready' }));
    expect(host.querySelectorAll('.wb-update-dot')).toHaveLength(2);
    await act(async () => setDesktopUpdateStatus({ ...state, phase: 'idle' }));
    expect(host.querySelectorAll('.wb-update-dot')).toHaveLength(0);
  } finally { act(() => root.unmount()); delete window.electronAPI; }
});

it('does not erase a new update notification with an older in-flight status response', async () => {
  const { SettingsUpdateButton } = await import('../SettingsUpdateButton');
  const { setDesktopUpdateStatus } = await import('../desktopUpdateStatus');
  const base = { phase: 'idle', currentVersion: '0.1.0', version: '', source: '', message: '', percent: 0, releaseConfigured: true, localSupported: true };
  let finish!: (state: typeof base) => void;
  const status = new Promise<typeof base>(resolve => { finish = resolve; });
  Object.defineProperty(window, 'electronAPI', { configurable: true, value: { updates: { status: () => status } } });
  const host = document.createElement('div'); const root = createRoot(host);
  try {
    await act(async () => { setDesktopUpdateStatus(base); root.render(createElement(SettingsUpdateButton, { onClick: vi.fn() })); });
    await act(async () => setDesktopUpdateStatus({ ...base, phase: 'available', version: '0.1.1' }));
    await act(async () => { finish(base); await status; });
    expect(host.querySelector('.wb-update-dot')).not.toBeNull();
  } finally { act(() => root.unmount()); delete window.electronAPI; }
});


it("checks first, then offers one download-and-install action", async () => {
  const idle = { phase: "idle", currentVersion: "1", version: "", source: "", message: "", percent: 0, releaseConfigured: true, localSupported: true, localBuildAvailable: true };
  const api = { status: vi.fn().mockResolvedValue(idle), action: vi.fn()
    .mockResolvedValueOnce({ ...idle, phase: "available", version: "2", source: "release" })
    .mockResolvedValueOnce({ ...idle, phase: "installing", version: "2", source: "release" }) };
  Object.defineProperty(window, "electronAPI", { configurable: true, value: { updates: api } });
  const host = document.createElement("div"); const root = createRoot(host);
  const primary = () => {
    const buttons = host.querySelectorAll<HTMLButtonElement>(".wb-update-overview button");
    expect(buttons).toHaveLength(1);
    return buttons[0];
  };
  try {
    await act(async () => root.render(createElement(DesktopUpdates)));
    expect(primary().textContent).toBe("Check for updates");
    await act(async () => primary().click());
    expect(api.action.mock.calls).toEqual([["check"]]);
    expect(primary().textContent).toBe("Download and install");
    await act(async () => primary().click());
    expect(api.action.mock.calls).toEqual([["check"], ["download-install"]]);
    expect(primary().textContent).toBe("Restarting…");
    expect(primary().disabled).toBe(true);
  } finally { act(() => root.unmount()); delete window.electronAPI; }
});
