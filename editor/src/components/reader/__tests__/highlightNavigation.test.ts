import { describe, expect, it } from 'vitest';
import { paperHighlightAtPoint } from '../lib/paper-highlight-display';
import type { PaperComment } from '../lib/paper-comments';
const note = (commentId: string): PaperComment => ({ commentId, materialId: 'test', pageNumber: 1, quote: 'Passage', text: '', rotation: 0, createdAt: '', updatedAt: '', rects: [{left:.2,top:.2,width:.5,height:.03},{left:.2,top:.25,width:.3,height:.03},{left:0,top:0,width:.001,height:.5}] });
describe('saved highlight navigation', () => {
 it('matches each painted line and ignores gaps and binding artifacts', () => {
  const a=note('a');
  expect(paperHighlightAtPoint([a],.3,.21)).toBe(a);
  expect(paperHighlightAtPoint([a],.3,.26)).toBe(a);
  expect(paperHighlightAtPoint([a],.3,.24)).toBeUndefined();
  expect(paperHighlightAtPoint([a],.0005,.1)).toBeUndefined();
 });
 it('opens the topmost saved highlight where annotations overlap', () => {
  const a=note('a'),b=note('b');
  expect(paperHighlightAtPoint([a,b],.3,.21)).toBe(b);
 });
});
