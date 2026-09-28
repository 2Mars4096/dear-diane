// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import DocumentView from '../DocumentView';
import type { DocumentDraft } from '../documents';
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => { Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true}); host=document.createElement('div');document.body.append(host);root=createRoot(host); });
afterEach(() => {act(()=>root.unmount());host.remove();vi.restoreAllMocks();vi.unstubAllGlobals();});
const file={source:'local' as const,name:'notes.md',path:'/tmp/notes.md',root:'/tmp',url:'/preview/notes.md'};
it('retains drafts across unmounts and sends revisions without dropping edits on conflicts',async()=>{
 const drafts: Record<string,DocumentDraft> = {[file.path]:{text:'unsaved draft',saved:'original',revision:'v1'}};
 const fetcher=vi.fn(async()=>new Response(JSON.stringify({detail:'File changed on disk'}),{status:409}));vi.stubGlobal('fetch',fetcher);
 const onDirty=vi.fn();
 await act(async()=>root.render(createElement(DocumentView,{file,active:true,drafts,onDirty})));
 expect(host.querySelector('textarea')?.value).toBe('unsaved draft');expect(fetcher).not.toHaveBeenCalled();
 await act(async()=>[...host.querySelectorAll('button')].find(x=>x.textContent==='Save')!.click());
 expect(fetcher).toHaveBeenCalledWith('/api/workspace-files/document',expect.objectContaining({method:'PUT',body:JSON.stringify({path:file.path,root_path:'/tmp',revision:'v1',content:'unsaved draft'})}));
 expect(host.textContent).toContain('File changed on disk');expect(drafts[file.path].text).toBe('unsaved draft');
 await act(async()=>root.render(null));
 await act(async()=>root.render(createElement(DocumentView,{file,active:true,drafts,onDirty})));
 expect(host.querySelector('textarea')?.value).toBe('unsaved draft');expect(onDirty).toHaveBeenCalledWith(file.path,true);
});
it('saves the submitted snapshot and advances the revision',async()=>{
 const drafts: Record<string,DocumentDraft> = {[file.path]:{text:'edited',saved:'original',revision:'v1'}};
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({revision:'v2'}))));
 const onDirty=vi.fn();await act(async()=>root.render(createElement(DocumentView,{file,active:true,drafts,onDirty})));
 await act(async()=>window.dispatchEvent(new KeyboardEvent('keydown',{key:'s',ctrlKey:true,cancelable:true})));
 expect(drafts[file.path]).toEqual({text:'edited',saved:'edited',revision:'v2'});expect(onDirty).toHaveBeenLastCalledWith(file.path,false);
});
