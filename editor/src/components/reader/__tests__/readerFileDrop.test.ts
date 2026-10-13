// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest';
import { bridgeReaderFileDrops } from '../lib/reader-file-drop';
afterEach(() => vi.unstubAllGlobals());
it('forwards iframe file drops once, preserving native drag objects, but leaves text drags alone', () => {
  // Happy DOM omits native file drag types and DragEvent payloads.
  vi.stubGlobal('DragEvent', class extends Event { dataTransfer: DataTransfer | null; constructor(type: string, init: DragEventInit) { super(type, init); this.dataTransfer = init.dataTransfer || null; } });
  const frameDocument = document.implementation.createHTMLDocument();
  const target = document.createElement('section'); document.body.append(target);
  const onDrop = vi.fn(); window.addEventListener('drop', onDrop);
  const stop = bridgeReaderFileDrops(frameDocument, target);
  try {
    const dataTransfer = new DataTransfer(); dataTransfer.items.add(new File(['hello'], 'note.txt')); Object.defineProperty(dataTransfer, 'types', { value: ['Files'] });
    frameDocument.dispatchEvent(new DragEvent('drop', { dataTransfer, cancelable: true }));
    expect(onDrop).toHaveBeenCalledOnce();
    expect(onDrop.mock.calls[0][0].dataTransfer).toBe(dataTransfer);
    const text = new DataTransfer(); text.setData('text/plain', 'selected passage');
    frameDocument.dispatchEvent(new DragEvent('drop', { dataTransfer: text }));
    expect(onDrop).toHaveBeenCalledOnce();
    stop(); frameDocument.dispatchEvent(new DragEvent('drop', { dataTransfer }));
    expect(onDrop).toHaveBeenCalledOnce();
  } finally { stop(); target.remove(); window.removeEventListener('drop', onDrop); }
});
