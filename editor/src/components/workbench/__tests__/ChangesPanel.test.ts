// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import ChangesPanel from "../ChangesPanel";
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
it("refreshes an open review automatically and stops polling while hidden", async () => {
  vi.useFakeTimers();
  let diff = "+first";
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ snapshots: [{ id: "one", label: "Request", created_at: 1 }], selected: "one", files: [{ path: "a.txt", diff, kind: "modified" }], omitted: [] })));
  vi.stubGlobal("fetch", fetcher);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  const host = document.createElement("div"), root = createRoot(host), comment = vi.fn();
  const render = (active: boolean) => root.render(createElement(ChangesPanel, { root: "/repo", thread: "chat", active, onComment: comment }));
  try {
    await act(async () => render(true));
    expect(host.textContent).toContain("+first");
    diff = "+second";
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(host.textContent).toContain("+second");
    await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent === "Ask for changes")!.click());
    expect(comment).toHaveBeenCalledWith(expect.stringContaining("+second"));
    await act(async () => render(false));
    const calls = fetcher.mock.calls.length;
    await act(async () => vi.advanceTimersByTimeAsync(10000));
    expect(fetcher).toHaveBeenCalledTimes(calls);
  } finally { act(() => root.unmount()); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); }
});
