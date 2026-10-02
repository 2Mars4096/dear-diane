import { useEffect, useRef, useState, type ReactNode } from 'react';
import { readerActions, useReaderState, type ReaderDraft } from '../reader/readerStore';
import { ReaderNotes } from '../reader/ReaderNotes';
import { createTextAnchor, fingerprintText, resolveTextAnchor } from '../reader/lib/paper-anchors';
import type { DocumentFile } from './documents';

function normalizedPositions(text: string) {
  const positions: number[] = []; let normalized = '';
  for (let i = 0; i < text.length; i++) {
    if (/\s/.test(text[i])) { if (normalized && !normalized.endsWith(' ')) { normalized += ' '; positions.push(i); } }
    else { normalized += text[i]; positions.push(i); }
  }
  return positions.slice(0, normalized.trimEnd().length);
}
export function textSelection(text: string, start: number, end: number): ReaderDraft | null {
  if (start < 0 || end <= start || end > text.length) return null;
  while (start < end && /\s/.test(text[start])) start++;
  while (end > start && /\s/.test(text[end - 1])) end--;
  const positions = normalizedPositions(text);
  const anchor = createTextAnchor(text, positions.indexOf(start), positions.indexOf(end - 1) + 1);
  return anchor ? { quote: anchor.quote, pageNumber: 1, rotation: 0, rects: [], anchor: { ...anchor, pageFingerprint: fingerprintText(text) } } : null;
}
function anchorRange(element: HTMLElement, anchor: NonNullable<ReaderDraft['anchor']>) {
  const result = resolveTextAnchor(element.textContent || '', anchor);
  if (result.status === 'missing') return null;
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = []; let node: Node | null;
  while ((node = walker.nextNode())) nodes.push(node as Text);
  const positions = normalizedPositions(nodes.map(n => n.data).join(''));
  const start = positions[result.selection.start], end = positions[result.selection.end - 1] + 1;
  if (start === undefined || !Number.isFinite(end)) return null;
  const range = document.createRange(); let offset = 0, started = false;
  for (const n of nodes) {
    if (!started && start < offset + n.length) { range.setStart(n, start - offset); started = true; }
    if (started && end <= offset + n.length) { range.setEnd(n, end - offset); return range; }
    offset += n.length;
  }
  return null;
}

/** Reuses the reader's durable notes, quote/context anchors and selection workflow. */
export function TextAnnotations({ file, text, children, onAsk }: { file: DocumentFile; text: string; children?: ReactNode; onAsk?: (text: string) => void }) {
  const root = useRef<HTMLDivElement>(null);
  const state = useReaderState(file.path);
  const [selection, setSelection] = useState<ReaderDraft | null>(null);
  const [notes, setNotes] = useState(false);
  const capture = () => {
    const selected = window.getSelection();
    const element = root.current;
    const input = element?.querySelector('textarea');
    if (input && document.activeElement === input) { setSelection(textSelection(text, input.selectionStart, input.selectionEnd)); return; }
    if (!element || !selected?.rangeCount || selected.isCollapsed) { setSelection(null); return; }
    const range = selected.getRangeAt(0);
    if (!element.contains(range.startContainer) || !element.contains(range.endContainer)) return;
    const before = range.cloneRange(); before.selectNodeContents(element); before.setEnd(range.startContainer, range.startOffset);
    const visible = element.textContent || '';
    setSelection(textSelection(visible, before.toString().length, before.toString().length + range.toString().length));
  };
  useEffect(() => {
    setSelection(null);
    const element = root.current;
    if (!element) return;
    const ranges: Range[] = [];
    for (const item of state.comments) {
      const result = item.anchor ? resolveTextAnchor(element.textContent || '', item.anchor) : { status: 'missing' };
      readerActions.setStale(file.path, item.commentId, result.status === 'missing' ? 'missing' : result.status === 'review' ? 'moved' : null);
      const range = item.anchor && anchorRange(element, item.anchor);
      if (range) ranges.push(range);
    }
    if (typeof Highlight !== 'undefined' && CSS.highlights) {
      const shared = CSS.highlights.get('dan-annotations') || new Highlight();
      CSS.highlights.set('dan-annotations', shared);
      ranges.forEach(range => shared.add(range));
      return () => { ranges.forEach(range => shared.delete(range)); };
    }
  }, [text, state.comments, file.path]);
  useEffect(() => {
    if (!state.focus || !root.current) return;
    const item = state.comments.find(comment => comment.commentId === state.focus?.commentId);
    const range = item?.anchor && anchorRange(root.current, item.anchor);
    if (!range) return;
    const selected = window.getSelection(); selected?.removeAllRanges(); selected?.addRange(range);
    range.startContainer.parentElement?.scrollIntoView?.({ block: 'nearest' });
  }, [state.focus]);
  return <div className="dan-text-annotations">
    <div className="dan-document-toolbar">
      <button title="Select a passage of 2–700 characters" disabled={!selection} onMouseDown={event => event.preventDefault()} onClick={() => { if (selection) { readerActions.setDraft(file.path, selection); setNotes(true); } }}>Annotate selection</button>
      {onAsk && <button disabled={!selection} onMouseDown={event => event.preventDefault()} onClick={() => { if (selection) onAsk(`File: ${file.path}\n\n> ${selection.quote}`); }}>Ask Diane</button>}
      <button aria-pressed={notes} onClick={() => setNotes(!notes)}>Notes{state.comments.length ? ` ${state.comments.length}` : ''}</button>
    </div>
    <div className="dan-annotated-content" ref={root} onMouseUp={capture} onKeyUp={capture} onSelect={capture}>{children || <pre tabIndex={0}>{text}</pre>}</div>
    {notes && <ReaderNotes file={file} textDocument onAsk={onAsk} />}
  </div>;
}
