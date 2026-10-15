// @vitest-environment happy-dom
import { act, createElement as h } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { ReadingSettings } from '../ReadingSettings';
Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
it('keeps PDF typography controls visible but disabled while layout and reset work',()=>{
 const host=document.createElement('div'),root=createRoot(host),reset=vi.fn();
 try {
  act(()=>root.render(h(ReadingSettings,{layout:h('select',{'aria-label':'Page layout'},h('option',null,'Single page')),onReset:reset})));
  expect(host.querySelector<HTMLSelectElement>('[aria-label="Page layout"]')!.disabled).toBe(false);
  expect(host.querySelector<HTMLSelectElement>('[aria-label="Book styling"]')!.disabled).toBe(true);
  expect(host.querySelector('fieldset')!.disabled).toBe(true);
  expect(host.querySelector('[aria-label="Reading font"]')).not.toBeNull();
  expect(host.textContent).toContain('Typewriter');expect(host.textContent).toContain('Handwritten');
  expect(host.textContent).toContain('PDF text is fixed');
  act(()=>host.querySelector('button')!.click());expect(reset).toHaveBeenCalledOnce();
 } finally{act(()=>root.unmount());}
});
