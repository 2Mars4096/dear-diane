// @vitest-environment happy-dom
import { act, createElement as h, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it } from 'vitest';
import { NativeWorkerSettings, type WorkerProfiles, type Runtime } from '../NativeWorkers';

it('adds independent workers, edits one model, and removes only that worker', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const host = document.createElement('div'), root = createRoot(host);
  let saved: WorkerProfiles = {};
  const catalog: Runtime[] = [{ id: 'codex', label: 'Codex', available: true, accounts: [{ id: 'default', label: 'Default' }], models: ["model-a", "model-b"], efforts: [], fast: false, setup: '', version: '' }];
  function Harness() {
    const [profiles, setProfiles] = useState<WorkerProfiles>({});
    saved = profiles;
    return h(NativeWorkerSettings, { profiles, onChange: setProfiles, inline: true, catalog });
  }
  try {
    await act(async () => root.render(h(Harness)));
    act(() => host.querySelector<HTMLButtonElement>('.wb-add-worker')!.click());
    const firstId = Object.keys(saved)[0];
    act(() => host.querySelector<HTMLButtonElement>('.wb-add-worker')!.click());
    expect(Object.keys(saved)).toHaveLength(2);
    const secondId = Object.keys(saved)[1];
    expect(firstId).not.toBe(secondId);
    expect(saved[firstId].backend).toBe('codex');
    expect(host.textContent).toContain('2 configured');
    const model = host.querySelector<HTMLSelectElement>('select[aria-label="Model"]')!;
    act(() => { model.value = 'model-b'; model.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(saved[secondId].model).toBe('model-b');
    expect(saved[firstId].model).toBe('');
    const childToggle = [...host.querySelectorAll<HTMLLabelElement>('label')].find(label => label.textContent?.includes('Allow child sessions'))!.querySelector<HTMLInputElement>('input')!;
    expect(childToggle.checked).toBe(false);
    act(() => childToggle.click());
    expect(saved[secondId].allow_children).toBe(true);
    expect(saved[firstId].allow_children).toBeUndefined();
    expect(host.querySelector('.wb-worker-config-heading button')!.firstElementChild?.tagName.toLowerCase()).toBe('svg');
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Remove Worker 2"]')!.click());
    expect(Object.keys(saved)).toEqual([firstId]);
    expect(host.textContent).toContain('1 configured');
  } finally { act(() => root.unmount()); }
});
