// @vitest-environment happy-dom
import {act, createElement as h} from 'react';
import {createRoot} from 'react-dom/client';
import {afterEach, expect, it, vi} from 'vitest';
import VoiceControl from '../VoiceControl';
const fixture = vi.hoisted(() => ({callbacks: null as null | {transcript(text: string): void}}));
vi.mock('../../../lib/http', () => ({requestJson: vi.fn(async () => ({enabled: true, profiles: [{id: 'warm', name: 'Warm'}]}))}));
vi.mock('../../../lib/personalVoice', () => ({PersonalVoiceSession: class {
  constructor(_profile: string, callbacks: {transcript(text: string): void}) { fixture.callbacks = callbacks; }
  start() { return Promise.resolve(); }
  end() {}
}}));
afterEach(() => vi.restoreAllMocks());
it('paints fast transcripts and protects newer speech from pending clears', async () => {
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true});
  const frames = new Map<number, FrameRequestCallback>(); let id = 0;
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => { frames.set(++id, callback); return id; });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(key => { frames.delete(key); });
  const frame = () => { const pending = [...frames.values()]; frames.clear(); act(() => pending.forEach(callback => callback(0))); };
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
  try {
    await act(async () => root.render(h(VoiceControl, {disabled: false, refresh: vi.fn(), onError: vi.fn(), onActive: vi.fn()})));
    await act(async () => host.querySelector<HTMLButtonElement>('button')!.click());
    act(() => { fixture.callbacks!.transcript('Book a table'); fixture.callbacks!.transcript(''); });
    expect(host.textContent).toContain('Book a table');
    frame(); expect(host.textContent).toContain('Book a table');
    act(() => fixture.callbacks!.transcript('Tomorrow evening'));
    frame(); expect(host.textContent).toContain('Tomorrow evening');
    act(() => fixture.callbacks!.transcript(''));
    frame(); frame(); expect(host.querySelector('.personal-voice-transcript')).toBeNull();
    act(() => fixture.callbacks!.transcript(''));
  } finally { act(() => root.unmount()); host.remove(); }
  expect(frames.size).toBe(0);
});
