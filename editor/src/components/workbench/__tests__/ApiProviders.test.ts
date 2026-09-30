// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { ApiProviders } from '../ApiProviders';
import { requestJson } from '../../../lib/http';
vi.mock('../../../lib/http', () => ({ requestJson: vi.fn() }));
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
it('masks and clears entered keys, supports removal, and does not store keys in browser profiles', async () => {
  const item = { id: 'openrouter', label: 'OpenRouter', configured: false, saved: false, key_env: 'OPENROUTER_API_KEY' };
  vi.mocked(requestJson).mockResolvedValueOnce({ providers: [item] }).mockResolvedValueOnce({ providers: [{ ...item, configured: true, saved: true }] }).mockResolvedValueOnce({ providers: [item] });
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(ApiProviders)));
    const input = host.querySelector<HTMLInputElement>('input')!;
    expect(input.type).toBe('password');
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'test-secret');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    expect(requestJson).toHaveBeenCalledWith('/api/model-providers/openrouter/key', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ api_key: 'test-secret' }) }));
    expect(input.value).toBe('');
    expect(host.textContent).not.toContain('test-secret');
    expect(JSON.stringify(localStorage)).not.toContain('test-secret');
    await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === 'Remove saved key')!.click());
    expect(requestJson).toHaveBeenLastCalledWith('/api/model-providers/openrouter/key', expect.objectContaining({ body: JSON.stringify({ api_key: '' }) }));
  } finally { act(() => root.unmount()); host.remove(); vi.resetAllMocks(); }
});
