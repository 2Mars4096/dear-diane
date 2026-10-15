// @vitest-environment happy-dom
import { expect, it } from 'vitest';
import { resizeComposer } from '../composerInput';
it('grows for multiline text and uses the actual CSS ceiling for scrolling', () => {
 const input=document.createElement('textarea');document.body.append(input);
 input.style.cssText='line-height:24px;padding:10px 0;border:1px solid;max-height:156px;box-sizing:border-box';
 let height=68;Object.defineProperty(input,'scrollHeight',{get:()=>height});
 try {
  input.scrollTop=20;resizeComposer(input);
  expect(input.style.height).toBe('70px');expect(input.scrollTop).toBe(0);
  height=160;resizeComposer(input);
  expect(input.style.height).toBe('156px');expect(input.style.overflowY).toBe('auto');
  height=44;resizeComposer(input);
  expect(input.style.height).toBe('46px');expect(input.style.overflowY).toBe('hidden');
 } finally {input.remove();}
});
