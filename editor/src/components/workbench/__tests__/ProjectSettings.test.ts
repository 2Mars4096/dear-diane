// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ProjectSettings } from "../ProjectSettings";
import * as api from "../../../lib/api";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(() => {});
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); document.querySelector('meta[name="dan-remote-machine"]')?.remove(); vi.restoreAllMocks(); });

it("fills the folder name, enables creation, and submits it", async () => {
  const save = vi.fn();
  act(() => root.render(createElement(ProjectSettings, { name: "", root: "", creating: true, onSave: save, onClose: vi.fn(), onBrowse: async () => "/Volumes/data/my-project/" })));
  expect(host.querySelector<HTMLButtonElement>('button[type="submit"]')!.disabled).toBe(true);
  const browse = [...host.querySelectorAll("button")].find((button) => button.textContent === "browse folders")!;
  await act(async () => browse.click());
  expect(host.querySelector("input")!.value).toBe("my-project");
  const submit = host.querySelector<HTMLButtonElement>('button[type="submit"]')!;
  expect(submit.disabled).toBe(false);
  act(() => submit.click());
  expect(save).toHaveBeenCalledWith("my-project", "/Volumes/data/my-project/");
});

it("preserves a custom project name when browsing another folder", async () => {
  const save = vi.fn();
  act(() => root.render(createElement(ProjectSettings, { name: "", root: "/first", creating: true, onSave: save, onClose: vi.fn(), onBrowse: async () => "/second" })));
  const input = host.querySelector("input")!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "Custom name");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const browse = [...host.querySelectorAll("button")].find((button) => button.textContent === "browse folders")!;
  await act(async () => browse.click());
  expect(input.value).toBe("Custom name");
  act(() => host.querySelector<HTMLButtonElement>('button[type="submit"]')!.click());
  expect(save).toHaveBeenCalledWith("Custom name", "/second");
});

function remoteHost() {
  const meta = document.createElement("meta");
  meta.name = "dan-remote-machine"; meta.content = "mini"; document.head.append(meta);
}

it("opens a remote folder as a project without invoking the Mac picker", async () => {
  remoteHost();
  const save = vi.fn(), localPicker = vi.fn();
  const folders = vi.spyOn(api, "listWorkspaceRootSuggestions").mockResolvedValue({ root: "/home/adam/projects", suggestions: [
    { path: "/home/adam/projects", name: "projects", label: "projects", kind: "current" },
    { path: "/home/adam/projects/research", name: "research", label: "research", kind: "match" },
  ] });
  await act(async () => root.render(createElement(ProjectSettings, { name: "", root: "", creating: true, onSave: save, onClose: vi.fn(), onBrowse: localPicker })));
  expect(folders).toHaveBeenLastCalledWith("/home/adam/projects/", 50);
  expect(host.textContent).toContain("Folder on mini");
  expect(host.textContent).not.toContain("Drop a folder here");
  act(() => host.querySelector<HTMLButtonElement>('button[title="Use /home/adam/projects/research"]')!.click());
  expect(host.querySelector<HTMLInputElement>("#project-folder-path")!.value).toBe("/home/adam/projects/research");
  act(() => host.querySelector<HTMLButtonElement>('button[type="submit"]')!.click());
  expect(save).toHaveBeenCalledWith("research", "/home/adam/projects/research");
  expect(localPicker).not.toHaveBeenCalled();
});

it("lets remote folder discovery retry after a connection failure", async () => {
  remoteHost();
  const folders = vi.spyOn(api, "listWorkspaceRootSuggestions").mockRejectedValueOnce(new Error("Offline"));
  await act(async () => root.render(createElement(ProjectSettings, { name: "", root: "", creating: true, onSave: vi.fn(), onClose: vi.fn(), onBrowse: vi.fn() })));
  expect(host.querySelector('[role="alert"]')!.textContent).toContain("Could not load folders on mini");
  folders.mockResolvedValue({ root: "/home/adam/projects", suggestions: [{ path: "/home/adam/projects", name: "projects", label: "projects", kind: "current" }] });
  await act(async () => [...host.querySelectorAll("button")].find(button => button.textContent === "Retry")!.click());
  expect(host.querySelector('[role="alert"]')).toBeNull();
  expect(host.querySelector('button[title="Use /home/adam/projects"]')).not.toBeNull();
});
