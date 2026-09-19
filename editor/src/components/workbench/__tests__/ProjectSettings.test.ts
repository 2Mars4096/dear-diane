// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ProjectSettings } from "../ProjectSettings";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(() => {});
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); });

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
