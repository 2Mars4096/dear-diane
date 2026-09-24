import { afterEach, expect, it, vi } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
const mocks = vi.hoisted(() => ({ handlers: new Map<string, (...args: any[]) => any>(), events: new Map<string, (...args: any[]) => any>(), quit: vi.fn(), install: vi.fn(), idle: vi.fn(), check: vi.fn(), download: vi.fn(), prepare: vi.fn() }));
vi.mock("electron", () => ({ app: { getPath: () => "/tmp/dan-update-ipc", getVersion: () => "1.0.0", isPackaged: true, quit: mocks.quit, once: vi.fn() }, ipcMain: { handle: (name: string, fn: (...args: any[]) => any) => mocks.handlers.set(name, fn) }, dialog: {} }));
vi.mock("electron-updater", () => ({ autoUpdater: { on: (name: string, fn: (...args: any[]) => any) => mocks.events.set(name, fn), checkForUpdates: mocks.check, downloadUpdate: mocks.download, setFeedURL: vi.fn(), quitAndInstall: mocks.install } }));
vi.mock("../../electron/localUpdate", () => ({ assertNoActiveWork: mocks.idle, stageBundle: mocks.prepare }));
vi.mock("../../electron/githubAuth", () => ({
  githubRelease: { provider: "github", owner: "owner", repo: "private" },
  GitHubAuth: class { state = { status: "connected", login: "tester", code: "", message: "" }; refresh = vi.fn(); cancel = vi.fn(); start = vi.fn(); token = vi.fn().mockResolvedValue("test-secret"); },
}));
import { registerDesktopUpdates } from "../../electron/desktopUpdates";
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });
it("restricts update IPC to the main window, separates download from install, and blocks busy runs", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "dan-update-feed-"));
  const previous = (process as any).resourcesPath;
  Object.defineProperty(process, "resourcesPath", { configurable: true, value: root });
  try {
    await fs.writeFile(path.join(root, "app-update.yml"), "provider: generic\nurl: https://example.test/releases\n");
    const frame = {}, contents = { mainFrame: frame };
    registerDesktopUpdates({ window: () => ({ webContents: contents }) as any, graphs: () => root, backend: () => ({ owned: true }) });
    const event = { sender: contents, senderFrame: frame };
    const action = mocks.handlers.get("updates:action")!;
    await expect(action({ ...event, senderFrame: {} }, "install")).rejects.toThrow("Untrusted");
    mocks.check.mockImplementation(async () => mocks.events.get("update-available")!({ version: "2.0.0" }));
    expect((await action(event, "check")).phase).toBe("available");
    expect(mocks.download).not.toHaveBeenCalled();
    mocks.download.mockImplementation(async () => mocks.events.get("update-downloaded")!({ version: "2.0.0" }));
    expect((await action(event, "download")).phase).toBe("ready");
    expect(mocks.install).not.toHaveBeenCalled();
    mocks.idle.mockRejectedValueOnce(Error("Finish active work"));
    expect((await action(event, "install")).message).toBe("Finish active work");
    expect(mocks.install).not.toHaveBeenCalled();
    mocks.idle.mockResolvedValue(undefined); vi.useFakeTimers();
    expect((await action(event, "install")).phase).toBe("installing");
    vi.runAllTimers(); expect(mocks.install).toHaveBeenCalledWith(false, true);
  } finally {
    Object.defineProperty(process, "resourcesPath", { configurable: true, value: previous });
    await fs.rm(root, { recursive: true, force: true });
  }
});

it("checks active work before preparing an automatically found local build", async () => {
  const frame={},contents={mainFrame:frame};
  registerDesktopUpdates({window:()=>({webContents:contents}) as any,graphs:()=>"/tmp/graphs",backend:()=>({owned:true})});
  mocks.prepare.mockClear();mocks.idle.mockRejectedValueOnce(Error("Finish active work"));
  const result=await mocks.handlers.get("updates:action")!({sender:contents,senderFrame:frame},"install-local");
  expect(result.message).toBe("Finish active work");expect(mocks.prepare).not.toHaveBeenCalled();
});
