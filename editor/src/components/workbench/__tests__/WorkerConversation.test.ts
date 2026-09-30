// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import WorkerConversation from "../WorkerConversation";
import type { TeamWorker } from "../teamPresentation";
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
it("loads older history and resumes only the targeted interrupted worker", async () => {
  const worker: TeamWorker = { worker_id: "w", parent_run_id: "p", backend: "claude", status: "interrupted", prompt: "Work", response: "", error: "", native_session_id: "saved", can_reply: true };
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(init?.method === "POST" ? { ...worker, status: "running" } : { entries: [{ cursor: url.includes("before=") ? 0 : 100, event: { text: url.includes("before=") ? "Earlier message" : "Recent message" } }], before: url.includes("before=") ? null : 100 })));
  vi.stubGlobal("fetch", fetcher);
  const host = document.createElement("div"), root = createRoot(host), changed = vi.fn();
  try {
    await act(async () => { root.render(createElement(WorkerConversation, { worker, onChange: changed })); });
    expect(host.textContent).toContain("Recent message");
    await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === "Load earlier")!.click());
    expect(host.textContent).toContain("Earlier message");
    await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === "Resume task")!.click());
    expect(fetcher.mock.calls.some(([url, init]) => url === "/api/native-workers/p/w/reply" && init?.method === "POST")).toBe(true);
    expect(changed).toHaveBeenCalledWith(expect.objectContaining({ worker_id: "w", status: "running" }));
  } finally { act(() => root.unmount()); vi.unstubAllGlobals(); }
});

it("answers a single question with one option click", async () => {
  const worker: TeamWorker = { worker_id: "w", parent_run_id: "p", backend: "codex", status: "needs_input", prompt: "Work", response: "", error: "", native_session_id: "saved", requests: [{ id: "q", method: "item/tool/requestUserInput", params: { questions: [{ id: "format", question: "Which format?", options: [{ label: "Summary" }] }] } }] };
  const fetcher = vi.fn(async (_url: string, init?: RequestInit) => new Response(JSON.stringify(init?.method === "POST" ? { ...worker, status: "running", requests: [] } : { entries: [], before: null })));
  vi.stubGlobal("fetch", fetcher);
  const host = document.createElement("div"), root = createRoot(host), changed = vi.fn();
  try {
    await act(async () => root.render(createElement(WorkerConversation, { worker, onChange: changed })));
    await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent === "Summary")!.click());
    const answer = fetcher.mock.calls.find(([url]) => url.endsWith("/answer"));
    expect(JSON.parse(String(answer?.[1]?.body))).toEqual({ request_id: "q", answers: { format: "Summary" } });
    expect(changed).toHaveBeenCalledWith(expect.objectContaining({ status: "running" }));
  } finally { act(() => root.unmount()); vi.unstubAllGlobals(); }
});

it("drains a final burst of events after the worker finishes", async () => {
  const worker: TeamWorker = { worker_id: "w", parent_run_id: "p", backend: "codex", status: "running", prompt: "Work", response: "", error: "", native_session_id: "saved" };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(url.includes("after=1") ? { entries: [{ cursor: 2, event: { text: "Final response" } }], before: 2, has_more: false } : url.includes("after=0") ? { entries: [{ cursor: 1, event: { text: "More activity" } }], before: 1, has_more: true } : { entries: [{ cursor: 0, event: { text: "Started" } }], before: null, has_more: false }))));
  const host = document.createElement("div"), root = createRoot(host);
  try {
    await act(async () => root.render(createElement(WorkerConversation, { worker })));
    await act(async () => root.render(createElement(WorkerConversation, { worker: { ...worker, status: "completed" } })));
    expect(host.textContent).toContain("Final response");
  } finally { act(() => root.unmount()); vi.unstubAllGlobals(); }
});
