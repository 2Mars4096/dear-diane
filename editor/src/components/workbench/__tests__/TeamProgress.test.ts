// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { TeamPanel } from "../TeamProgress";
import type { TeamWorker } from "../teamPresentation";
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

it("shows observed Codex children and results without offering unsupported Stop", async () => {
  const host = document.createElement("div"); const root = createRoot(host);
  const stop = vi.fn();
  const child: TeamWorker = { worker_id: "child", parent_run_id: "run", backend: "codex", status: "running", prompt: "Review display", response: "", error: "", native_session_id: "native", can_stop: false, activity: "Reading files" };
  const owned = { ...child, worker_id: "owned", prompt: "Check tests", can_stop: true };
  const render = (workers: TeamWorker[]) => act(() => root.render(createElement(TeamPanel, { workers, error: "", onStop: stop, onClose: vi.fn() })));
  try {
    render([child, owned]);
    expect(host.textContent).toContain("Review display");
    expect(host.textContent).toContain("reading files");
    const buttons = host.querySelectorAll<HTMLButtonElement>('button[aria-label^="Stop"]');
    expect(buttons).toHaveLength(1);
    act(() => buttons[0].click());
    expect(stop).toHaveBeenCalledWith(owned);
    render([{ ...child, status: "completed", response: "Display reviewed" }]);
    expect(host.textContent).toContain("Display reviewed");
    expect(host.querySelector('button[aria-label^="Stop"]')).toBeNull();
  } finally { act(() => root.unmount()); }
});
