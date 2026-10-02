// @vitest-environment happy-dom
import { afterEach, expect, it } from 'vitest';
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { ReaderNotes } from '../ReaderNotes';
import { readerActions } from '../readerStore';
import { readPaperComments } from '../lib/paper-comments';
Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
const file={name:'Scan.pdf',path:'/test/offline.pdf',url:'blob:test'};
const selection={pageNumber:1,rotation:0 as const,quote:'Selected passage',originalQuote:'bad OCR',quoteStatus:'deferred' as const,rects:[{left:.2,top:.3,width:.2,height:.04}],anchor:null,draftId:'offline-note'};
afterEach(()=>{readerActions.forget(file.path);localStorage.clear();});
function mount(){const host=document.createElement('div');document.body.append(host);const root=createRoot(host);act(()=>root.render(createElement(ReaderNotes,{file})));return {host,close:()=>{act(()=>root.unmount());host.remove();}};}
function type(host:HTMLElement,text:string){const input=host.querySelector('textarea')!;act(()=>{Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(input,text);input.dispatchEvent(new Event('input',{bubbles:true}));});return input;}
function save(host:HTMLElement){act(()=>host.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));}
it('saves offline immediately and persists pending quote geometry through reload',()=>{
  readerActions.setDraft(file.path,selection);let view=mount();
  try {
    type(view.host,'Keep this thought.');save(view.host);
    expect(readPaperComments({material_id:file.path})[0]).toMatchObject({text:'Keep this thought.',quoteStatus:'deferred',originalQuote:'bad OCR',rects:selection.rects});
    view.close();readerActions.forget(file.path);view=mount();
    expect(view.host.textContent).toContain('Keep this thought.');expect(view.host.textContent).toContain('Retry quote');
    act(()=>readerActions.finishQuote(file.path,'offline-note',{...selection,quote:'真实原文。',quoteSource:'vision',quoteModel:'qwen/test'}));
    expect(view.host.textContent).toContain('真实原文。');expect(view.host.textContent).toContain('Keep this thought.');
  } finally {view.close();}
});
it('background transcription does not clear typing or resurrect a deleted comment',()=>{
  readerActions.setDraft(file.path,{...selection,quoteStatus:'pending'});const view=mount();
  try {
    const input=type(view.host,'Still typing');
    act(()=>readerActions.finishQuote(file.path,'offline-note',{...selection,quote:'原文',quoteSource:'vision'}));
    expect(input.value).toBe('Still typing');save(view.host);
    act(()=>readerActions.removeComment(file.path,'offline-note'));
    act(()=>readerActions.finishQuote(file.path,'offline-note',{...selection,quote:'原文',quoteSource:'vision'}));
    expect(readPaperComments({material_id:file.path})).toEqual([]);
  } finally {view.close();}
});
