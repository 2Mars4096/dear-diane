// @vitest-environment happy-dom
import { act, createElement as h, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it } from 'vitest';
import { ModelFields } from '../ModelFields';
import { EMPTY_PROFILE, type Runtime, type WorkerProfile } from '../modelSelection';

it('keeps presets available for a saved older model and preserves it until selection', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const host = document.createElement('div'), root = createRoot(host);
  let saved: WorkerProfile = { ...EMPTY_PROFILE, model: 'claude-opus-5' };
  const runtime: Runtime = { id: 'claude', label: 'Claude Code', available: true, accounts: [], models: ['claude-opus-5-5'], model_labels: { 'claude-opus-5-5': 'Opus 5.5' }, efforts: [], fast: false, setup: '', version: '' };
  function Harness() {
    const [profile, update] = useState(saved); saved = profile;
    return h(ModelFields, { runtime, profile, onChange: update });
  }
  try {
    await act(async () => root.render(h(Harness)));
    const select = host.querySelector<HTMLSelectElement>('select[aria-label="Model"]')!;
    expect(select.textContent).toContain('Opus 5.5');
    expect(saved.model).toBe('claude-opus-5');
    expect(host.querySelector<HTMLInputElement>('input[aria-label="Custom model ID"]')!.value).toBe('claude-opus-5');
    act(() => { select.value = 'claude-opus-5-5'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(saved.model).toBe('claude-opus-5-5');
    expect(host.querySelector('input[aria-label="Custom model ID"]')).toBeNull();
    act(() => { select.value = '__custom__'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(host.querySelector('input[aria-label="Custom model ID"]')).not.toBeNull();
    expect(select.textContent).toContain('Opus 5.5');
  } finally { act(() => root.unmount()); }
});
