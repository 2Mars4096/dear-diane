// @vitest-environment happy-dom
import { expect, it } from "vitest";
import { attentionTransitions } from "../WorkNotifications";
it("baselines old work and alerts only on unseen transitions outside the watched session", () => {
  const seen = new Map<string, string>();
  const item = { id: "task:1", thread: "chat", state: "running", title: "Done" };
  expect(attentionTransitions(seen, [item], "")).toEqual([]);
  expect(attentionTransitions(seen, [{ ...item, state: "completed" }], "")).toHaveLength(1);
  expect(attentionTransitions(seen, [{ ...item, state: "completed" }], "")).toEqual([]);
  attentionTransitions(seen, [item], "chat");
  expect(attentionTransitions(seen, [{ ...item, state: "needs_input" }], "chat")).toEqual([]);
  expect(attentionTransitions(seen, [{ ...item, state: "needs_input" }], "")).toEqual([]);
});

it("alerts on a newly completed run after initial history was baselined", () => {
  const seen = new Map([["task:old", "completed"]]);
  expect(attentionTransitions(seen, [{ id: "task:new", thread: "another", state: "completed", title: "Done" }], "", true)).toHaveLength(1);
});

it("notifies the first worker after an empty baseline and routes the click to it", async () => {
  const { act, createElement } = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { vi } = await import("vitest");
  const { default: WorkNotifications } = await import("../WorkNotifications");
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.useFakeTimers(); localStorage.clear();
  let workers: object[] = [];
  const show = vi.fn(async () => {}), open = vi.fn();
  let click: ((target: { thread: string; worker: string }) => void) | undefined;
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ workers }))));
  Object.assign(window, { electronAPI: { attention: { show, onOpen: (fn: typeof click) => { click = fn; return vi.fn(); } } } });
  const host = document.createElement("div"), root = createRoot(host);
  try {
    await act(async () => root.render(createElement(WorkNotifications, { tasks: [], activeThread: "elsewhere", watching: true, onOpen: open })));
    workers = [{ worker_id: "first", thread_id: "chat", status: "needs_input", attempt: "1", request: "q" }];
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(show).toHaveBeenCalledTimes(1);
    click?.({ thread: "chat", worker: "first" });
    expect(open).toHaveBeenCalledWith("chat", "first");
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(show).toHaveBeenCalledTimes(1);
  } finally { act(() => root.unmount()); delete (window as unknown as { electronAPI?: unknown }).electronAPI; vi.unstubAllGlobals(); vi.useRealTimers(); }
});
