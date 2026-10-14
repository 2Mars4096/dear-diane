// @vitest-environment happy-dom
import { act, createElement as h } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { WorkbenchConversation } from '../WorkbenchConversation';
import type { ChatMessage } from '../../../types/chat';
vi.mock('../../shared/MarkdownRenderer', () => ({ default: ({ content }: { content: string }) => h('p', null, content) }));
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

it('ignores status-only updates and pauses auto-follow while reading details', async () => {
  const host = document.createElement('div'), root = createRoot(host);
  const message: ChatMessage = { id: 'reply', role: 'assistant', content: 'Working', timestamp: 1, toolCalls: [{ id: 'tool', toolName: 'Read file', argsPreview: 'file.txt', status: 'running' }] };
  const render = (content: string, status: string) => root.render(h(WorkbenchConversation, { messages: [{ ...message, content }], pending: { reply: true }, loading: false, status, onQuote: vi.fn() }));
  try {
    await act(async () => render('Working', 'running'));
    const scroller = host.querySelector<HTMLDivElement>('.wb-conversation-scroll')!;
    let top = 1500;
    const setTop = vi.fn((value: number) => { top = value; });
    Object.defineProperties(scroller, { scrollHeight: { value: 2000 }, clientHeight: { value: 500 }, scrollTop: { get: () => top, set: setTop } });
    await act(async () => render('Working', 'New activity'));
    expect(setTop).not.toHaveBeenCalled();
    await act(async () => render('New response text', 'New activity'));
    expect(setTop).toHaveBeenCalled();
    setTop.mockClear();
    act(() => host.querySelector<HTMLElement>('summary')!.click());
    act(() => scroller.dispatchEvent(new Event('scroll')));
    await act(async () => render('More response text', 'Another status'));
    expect(setTop).not.toHaveBeenCalled();
    expect(host.querySelector('.wb-jump')?.textContent).toContain('Latest');
  } finally { act(() => root.unmount()); }
});
