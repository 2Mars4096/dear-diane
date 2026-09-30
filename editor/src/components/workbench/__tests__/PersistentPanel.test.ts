// @vitest-environment happy-dom
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PersistentPanel } from "../PersistentPanel";
import { ProcessesPanel, type ManagedProcess } from "../ProcessesPanel";
import { ReaderNotes } from "../../reader/ReaderNotes";
import { readerActions } from "../../reader/readerStore";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); });

it("keeps process drafts, expanded logs, and DOM scroll across hide/show, but resets for a new project", async () => {
  const item: ManagedProcess = { id: "p1", name: "Preview", command: "npm run dev", cwd: "/project", status: "exited", origin: "user", pid: 1, started_at: 1, ended_at: 2 };
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ logs: "Ready" })));
  vi.stubGlobal("fetch", fetcher);
  const render = async (active: boolean, cwd = "/project") => act(async () => root.render(h(PersistentPanel, {
    active, name: "Processes", key: cwd,
    children: h(ProcessesPanel, { cwd, workspaceId: "test", processes: [item], error: "", onChanged: () => {} }),
  })));
  await render(false);
  expect(host.querySelector("input")).toBeNull(); // unopened tools have no effects
  await render(true);
  const input = host.querySelector("input")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "echo draft");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    host.querySelector<HTMLButtonElement>(".wb-lane-title")!.click();
  });
  const logs = host.querySelector("pre")!;
  logs.scrollTop = 75;
  await render(false);
  expect(host.querySelector<HTMLElement>("[role=region]")!.hidden).toBe(true);
  await render(true);
  expect(host.querySelector("input")).toBe(input);
  expect(input.value).toBe("echo draft");
  expect(host.querySelector("pre")).toBe(logs);
  expect(logs.scrollTop).toBe(75);
  expect(fetcher).toHaveBeenCalledTimes(1);
  await render(true, "/other-project");
  expect(host.querySelector("input")!.value).toBe("");
  expect(host.querySelector("pre")).toBeNull();
});

it("preserves an unsaved reader note while another tool is visible", async () => {
  const file = { path: "/test/keep-note.pdf", name: "book.pdf", url: "/book.pdf", root: "/test" };
  readerActions.setDraft(file.path, { pageNumber: 1, quote: "A passage", rects: [], rotation: 0, anchor: null });
  const render = async (active: boolean) => act(async () => root.render(h(PersistentPanel, {
    active, name: "Notes", children: h(ReaderNotes, { file }),
  })));
  await render(true);
  const input = host.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "Unfinished thought");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await render(false);
  await render(true);
  expect(host.querySelector("textarea")).toBe(input);
  expect(input.value).toBe("Unfinished thought");
  expect(host.querySelector("form")!.textContent).toContain("Save note");
});
