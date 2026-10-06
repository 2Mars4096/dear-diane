// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest';
import { extendOcrSelectionAcrossGap } from '../lib/ocr-selection-lines';
afterEach(() => { document.getSelection()?.removeAllRanges(); document.body.replaceChildren(); vi.restoreAllMocks(); Reflect.deleteProperty(document, 'caretRangeFromPoint'); });
function fixture(ocr = true) {
  const layer = document.createElement('div');
  const lines = ['Earlier heading', 'First paragraph line', 'Second paragraph line'].map((text, index) => {
    const span = document.createElement('span'); span.textContent = text;
    if (ocr) span.dataset.ocrLine = 'true';
    vi.spyOn(span, 'getBoundingClientRect').mockReturnValue(new DOMRect(30, [10, 80, 110][index], 240, 20));
    layer.append(span); return span;
  });
  document.body.append(layer);
  const selection = document.getSelection()!;
  selection.collapse(lines[1].firstChild!, 4);
  const caret = document.createRange(); caret.setStart(lines[2].firstChild!, 8); caret.collapse(true);
  const hit = vi.fn(() => caret);
  Object.defineProperty(document, 'caretRangeFromPoint', { configurable: true, value: hit });
  return { layer, lines, selection, hit };
}
it('corrects a gap endpoint before the anchor without moving the anchor', () => {
  const { layer, lines, selection, hit } = fixture();
  selection.extend(lines[0].firstChild!, 2);
  expect(extendOcrSelectionAcrossGap(layer, 100, 108)).toBe(true);
  expect(selection.anchorNode).toBe(lines[1].firstChild);
  expect(selection.anchorOffset).toBe(4);
  expect(selection.focusNode).toBe(lines[2].firstChild);
  expect(selection.focusOffset).toBe(8);
  expect(hit).toHaveBeenCalledWith(100, 120);
});
it('leaves on-line selection, margins and large paragraph gaps untouched', () => {
  const { layer, selection, hit } = fixture();
  for (const [x, y] of [[100, 90], [10, 108], [100, 50], [100, 180]]) expect(extendOcrSelectionAcrossGap(layer, x, y)).toBe(false);
  expect(hit).not.toHaveBeenCalled();
  expect(selection.anchorOffset).toBe(4);
});
it('does not change native PDF text selection', () => {
  const { layer, hit } = fixture(false);
  expect(extendOcrSelectionAcrossGap(layer, 100, 108)).toBe(false);
  expect(hit).not.toHaveBeenCalled();
});
