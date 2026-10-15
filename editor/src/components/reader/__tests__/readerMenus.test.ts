// @vitest-environment happy-dom
import { act, createElement as h } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { ReaderContents } from '../ReaderMenus';
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
it('searches chapters and closes after selecting the original navigation target', async () => {
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host),select=vi.fn();
 try {
  await act(async()=>root.render(h(ReaderContents,{items:[{id:'one.xhtml',label:'First chapter'},{id:'two.xhtml#section',label:'Second chapter'}],value:'one.xhtml',onSelect:select})));
  const details=host.querySelector('details')!;details.open=true;
  const input=host.querySelector('input')!;
  await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,'second');input.dispatchEvent(new Event('input',{bubbles:true}));});
  expect(host.querySelectorAll('nav button')).toHaveLength(1);
  act(()=>host.querySelector<HTMLButtonElement>('nav button')!.click());
  expect(select).toHaveBeenCalledWith('two.xhtml#section');expect(details.open).toBe(false);
  details.open=true;act(()=>input.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
  expect(details.open).toBe(false);expect(document.activeElement).toBe(host.querySelector('summary'));
 } finally {act(()=>root.unmount());host.remove();}
});
