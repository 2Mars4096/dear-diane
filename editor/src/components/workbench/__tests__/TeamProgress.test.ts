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
    act(() => host.querySelector<HTMLButtonElement>(".wb-team-run-toggle")!.click());
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

it("keeps an expanded conversation open when its worker completes", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ entries: [], before: null }))));
  const host = document.createElement("div"), root = createRoot(host);
  const worker: TeamWorker = { worker_id: "owned", parent_run_id: "run", backend: "codex", status: "needs_input", prompt: "Refine spacing", response: "", error: "", native_session_id: "saved" };
  const render = (status: string) => root.render(createElement(TeamPanel, { workers: [{ ...worker, status }], error: "", onStop: vi.fn(), onClose: vi.fn() }));
  try {
    await act(async () => render("needs_input"));
    act(() => host.querySelector<HTMLButtonElement>(".wb-team-run-toggle")!.click());
    await act(async () => host.querySelector<HTMLButtonElement>(".wb-lane-title")!.click());
    expect(host.querySelector(".wb-lane-title")?.getAttribute("aria-expanded")).toBe("true");
    await act(async () => render("completed"));
    expect(host.querySelector(".wb-lane-title")?.getAttribute("aria-expanded")).toBe("true");
  } finally { act(() => root.unmount()); vi.unstubAllGlobals(); }
});

it('groups workers by their actual lead run and opens the selected lead', async () => {
  const host = document.createElement('div'), root = createRoot(host), openLead = vi.fn();
  const worker: TeamWorker = { worker_id: 'a', parent_run_id: 'run-one', backend: 'codex', status: 'completed', prompt: 'Implement HUD', response: 'HUD changed', error: '', native_session_id: 'session-a', attempt: 2, lead_review: { attempt: 1, decision: 'accepted', evidence: ['Old check'] } };
  try {
    await act(async () => root.render(createElement(TeamPanel, { workers: [worker, { ...worker, worker_id: 'b', parent_run_id: 'run-two', prompt: 'Review camera', origin: 'codex_subagent', lead_review: null }], error: '', onStop: vi.fn(), onClose: vi.fn(), onOpenLead: openLead, activeRunId: 'run-two' })));
    const branches = host.querySelectorAll('.wb-team-branch');
    expect(branches).toHaveLength(2);
    expect(branches[0].textContent).toContain('Review camera');
    act(() => branches[1].querySelector<HTMLButtonElement>('.wb-team-run-toggle')!.click());
    expect(branches[1].textContent).toContain('Implement HUD');
    expect(branches[1].textContent).not.toContain('Review camera');
    expect(branches[1].textContent).toContain('Awaiting lead review');
    expect(branches[1].textContent).not.toContain('Accepted by lead');
    expect(branches[0].textContent).toContain('Codex subagent');
    expect(branches[0].textContent).toContain('Review not reported');
    act(() => branches[0].querySelector<HTMLButtonElement>('.wb-team-lead')!.click());
    expect(openLead).toHaveBeenCalledWith('run-two');
  } finally { act(() => root.unmount()); }
});

it('shows current review evidence and integration separately from completion', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ entries: [], before: null }))));
  const host = document.createElement('div'), root = createRoot(host);
  const worker: TeamWorker = { worker_id: 'a', parent_run_id: 'run', backend: 'codex', status: 'completed', prompt: 'Implement HUD', response: 'HUD changed', error: '', native_session_id: 'session', attempt: 2, lead_review: { attempt: 2, decision: 'accepted', evidence: ['HUD tests pass'] }, baseline_tree: 'base' };
  try {
    await act(async () => root.render(createElement(TeamPanel, { workers: [worker], selectedWorker: 'a', error: '', onStop: vi.fn(), onClose: vi.fn() })));
    expect(host.textContent).toContain('Assignment from lead');
    expect(host.textContent).toContain('Accepted by lead');
    expect(host.textContent).toContain('HUD tests pass');
    expect(host.textContent).toContain('Not applied to project');
    expect(host.textContent).toContain('Worker session');
  } finally { act(() => root.unmount()); vi.unstubAllGlobals(); }
});

it('nests child sessions under their worker rather than the main lead', async () => {
  const host = document.createElement('div'), root = createRoot(host);
  const parent: TeamWorker = { worker_id: 'p', parent_run_id: 'run', backend: 'codex', status: 'completed', prompt: 'Parent task', response: '', error: '', native_session_id: '' };
  try {
    await act(async () => root.render(createElement(TeamPanel, { workers: [parent, { ...parent, worker_id: 'c', parent_worker_id: 'p', prompt: 'Child task' }], error: '', onStop: vi.fn(), onClose: vi.fn() })));
    act(() => host.querySelector<HTMLButtonElement>('.wb-team-run-toggle')!.click());
    const children = host.querySelector('[aria-label="Child sessions"]')!;
    expect(children.textContent).toContain('Child task');
    expect(children.parentElement?.querySelector('.wb-lane-title')?.textContent).toContain('Parent task');
    expect(host.querySelectorAll('.wb-team-lead')).toHaveLength(1);
  } finally { act(() => root.unmount()); }
});


it('pins the current run and sorts collapsed history by time rather than IDs', async () => {
  const host = document.createElement('div'), root = createRoot(host);
  const worker: TeamWorker = { worker_id: 'old', parent_run_id: 'z-old', backend: 'codex', status: 'completed', prompt: 'Older task', response: '', error: '', native_session_id: '', created_at: 10 };
  try {
    await act(async () => root.render(createElement(TeamPanel, { workers: [worker, { ...worker, worker_id: 'new', parent_run_id: 'a-new', created_at: 20 }], activeRunId: 'current', runDetails: { 'a-new': { title: 'Recent request', startedAt: 30000 } }, error: '', onStop: vi.fn(), onClose: vi.fn() })));
    const branches = host.querySelectorAll('.wb-team-branch');
    expect(branches[0].textContent).toContain('Current run');
    expect(branches[1].textContent).toContain('Recent request');
    expect(branches[2].textContent).toContain('Older task');
    expect(branches[0].querySelector('button')?.getAttribute('aria-expanded')).toBe('true');
    expect(branches[1].querySelector('button')?.getAttribute('aria-expanded')).toBe('false');
    expect(branches[1].querySelector('.wb-lane')).toBeNull();
    expect(branches[1].querySelector('time')?.getAttribute('dateTime')).toBe('1970-01-01T00:00:30.000Z');
  } finally { act(() => root.unmount()); }
});
