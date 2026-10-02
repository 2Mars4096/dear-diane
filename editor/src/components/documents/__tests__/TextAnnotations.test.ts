// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { TextAnnotations, textSelection } from '../TextAnnotations';
import { readerActions } from '../../reader/readerStore';
import { readPaperComments } from '../../reader/lib/paper-comments';
const file = { source: 'local' as const, name: 'report.tex', path: '/test/report.tex', url: '/report.tex' };
afterEach(() => { readerActions.forget(file.path); localStorage.clear(); });
it('anchors the selected occurrence through whitespace normalization', () => {
 const source = '  first\n\n repeat  and repeat end';
 const start = source.lastIndexOf('repeat');
 const draft = textSelection(source, start, start + 6)!;
 expect(draft.quote).toBe('repeat'); expect(draft.anchor?.start).toBe(17);
 expect(textSelection(source, 0, 2)).toBeNull();
 expect(textSelection(source, 0, 0)).toBeNull();
});
it('persists text annotations without PDF rectangles and stages the quote with its note', async () => {
 Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
 const draft = textSelection('A selected passage.', 2, 18)!;
 readerActions.addComment(file.path, draft, 'Please clarify this.');
 const persisted = readPaperComments({ material_id: file.path });
 expect(persisted).toHaveLength(1); expect(persisted[0].rects).toEqual([]);
 readerActions.forget(file.path);
 const host = document.createElement('div'); document.body.append(host); const root = createRoot(host), onAsk = vi.fn();
 try {
  await act(async () => root.render(createElement(TextAnnotations, { file, text: 'A selected passage.', onAsk })));
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === 'Notes 1')!.click());
  expect(host.textContent).toContain('Please clarify this.');
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === 'Use in chat')!.click());
  expect(onAsk).toHaveBeenCalledWith(expect.stringContaining('Please clarify this.'));
  expect(onAsk.mock.calls[0][0]).toContain('selected passage');
  expect(onAsk.mock.calls[0][0]).toContain(file.path);
 } finally { act(() => root.unmount()); host.remove(); }
});
