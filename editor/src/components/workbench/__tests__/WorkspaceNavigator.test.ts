// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import WorkspaceNavigator from '../WorkspaceNavigator';
import { workspaceItems, orderedWorkspaceItems, type ItemGroup } from '../workspaceItems';
import { pathDocument } from '../../documents/documents';
Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
const thread=(id:string)=>({id,workflow_id:'p',title:id,message_count:1,created_at:'2026-09-01',updated_at:`2026-09-0${id==='a'?1:id==='b'?2:3}`});
const groups:ItemGroup[]=[{name:'Research',workspaceId:'p',root:'/research',threads:[thread('a'),thread('b'),thread('c')]}];
function harness() {
  localStorage.clear();
  const host=document.createElement('div'), root=createRoot(document.createElement('div')), select=vi.fn(), menu=vi.fn(), close=vi.fn();document.body.append(host);
  const render=(active='p:c', extra:Partial<Parameters<typeof WorkspaceNavigator>[0]>={})=>act(()=>root.render(createElement(WorkspaceNavigator,{host,groups,tabs:[],documents:{},dirty:{},active,query:'',archived:false,preferences:{},running:[],unread:[],onSelect:select,onMenu:menu,onClose:close,onArchive:vi.fn(),onStop:vi.fn(),...extra})));
  const down=(key:string,code='')=>act(()=>window.dispatchEvent(new KeyboardEvent('keydown',{key,code,ctrlKey:true,cancelable:true})));
  const cleanup=()=>{act(()=>root.unmount());host.remove();localStorage.clear();};
  return {host,select,menu,close,render,down,cleanup};
}
it('seeds eligible recent destinations immediately and shows numbers only while the modifier is held',()=>{
  const h=harness();try{
    h.render();expect(h.host.querySelectorAll('.wb-item-row')).toHaveLength(3);expect(h.host.querySelector('kbd')).toBeNull();
    h.down('Control');expect(h.host.querySelector('[data-item-key="p:b"] kbd')?.textContent).toBe('Ctrl+1');
    h.down('&','Digit1');expect(h.select.mock.calls.at(-1)?.[0].key).toBe('p:b');
    act(()=>window.dispatchEvent(new KeyboardEvent('keyup',{key:'Control'})));expect(h.host.querySelector('kbd')).toBeNull();
  }finally{h.cleanup();}
});
it('freezes both row order and targets across selections, then releases them on blur',()=>{
  const h=harness();try{
    h.render();h.down('Control');const order=[...h.host.querySelectorAll('[data-item-key]')].map(node=>node.getAttribute('data-item-key'));
    h.down('1');h.render('p:b');h.down('2');expect(h.select.mock.calls.at(-1)?.[0].key).toBe('p:a');
    expect([...h.host.querySelectorAll('[data-item-key]')].map(node=>node.getAttribute('data-item-key'))).toEqual(order);
    act(()=>window.dispatchEvent(new Event('blur')));expect(h.host.querySelector('kbd')).toBeNull();
    h.render('p:b',{host:null});h.down('1');expect(h.select.mock.calls.at(-1)?.[0].key).toBe('p:c');
  }finally{h.cleanup();}
});
it('shows each pinned session once and keeps pins independent from shortcut order',()=>{
  const h=harness();try{
    h.render('p:c',{preferences:{'p:a':{pinned:true}}});expect(h.host.querySelector('[data-item-key]')?.getAttribute('data-item-key')).toBe('p:a');
    expect(h.host.querySelectorAll('[data-item-key="p:a"]')).toHaveLength(1);h.down('1');expect(h.select.mock.calls.at(-1)?.[0].key).toBe('p:b');
    act(()=>h.host.querySelector<HTMLButtonElement>('[aria-label="Actions for a"]')!.click());expect(h.menu.mock.calls[0][0].key).toBe('p:a');
  }finally{h.cleanup();}
});
it('includes documents in recent switching, with project and unsaved state on the same row',()=>{
  const h=harness();const file={...pathDocument('/research/note.md'),workspaceId:'p',projectName:'Research'};
  const extra={tabs:[{id:'file:/research/note.md',kind:'file' as const,label:'note.md'}],documents:{'file:/research/note.md':file},dirty:{[file.path]:true}};
  try{
    h.render('p:c',extra);h.render('tab:file:/research/note.md',extra);h.render('p:c',extra);h.down('1');
    expect(h.select.mock.calls.at(-1)?.[0].key).toBe('tab:file:/research/note.md');
    expect(h.host.querySelector('[data-item-key="tab:file:/research/note.md"]')?.textContent).toContain('Unsaved');
    expect(h.host.querySelector('[data-item-key="tab:file:/research/note.md"] button')?.getAttribute('title')).toContain('Research');
    expect(h.host.querySelector('.wb-item-meta')).toBeNull();
    act(()=>h.host.querySelector<HTMLButtonElement>('[aria-label="Close note.md"]')!.click());expect(h.close).toHaveBeenCalledWith('file:/research/note.md');
  }finally{h.cleanup();}
});
it('ignores closed/deleted and archived targets and leaves modal shortcuts alone',()=>{
  const h=harness();try{
    localStorage.setItem('dan.workspaceVisits.v1',JSON.stringify(['missing','p:a','p:b']));
    const archived=[{...groups[0],threads:[{...thread('a'),archived:true},thread('b'),thread('c')]}];
    h.render('p:c',{groups:archived});h.down('1');expect(h.select.mock.calls.at(-1)?.[0].key).toBe('p:b');
    const dialog=document.createElement('dialog');dialog.setAttribute('open','');document.body.append(dialog);h.select.mockClear();h.down('1');expect(h.select).not.toHaveBeenCalled();dialog.remove();
  }finally{h.cleanup();}
});
it('keeps background title/timestamp changes from reordering the list',()=>{
  const h=harness();try{
    h.render();const before=[...h.host.querySelectorAll('[data-item-key]')].map(node=>node.getAttribute('data-item-key'));
    h.render('p:c',{groups:[{...groups[0],threads:groups[0].threads.map(t=>t.id==='a'?{...t,title:'Renamed',updated_at:'2030-01-01'}:t)}]});
    expect([...h.host.querySelectorAll('[data-item-key]')].map(node=>node.getAttribute('data-item-key'))).toEqual(before);
  }finally{h.cleanup();}
});
it('deduplicates recovered sessions and resolves file projects by the longest matching root',()=>{
  const items=workspaceItems([...groups,{...groups[0],name:'Nested',root:'/research/sub',workspaceId:'nested'}],[{id:'file:/research/sub/a.txt',kind:'file',label:'a.txt'}],{'file:/research/sub/a.txt':pathDocument('/research/sub/a.txt')},{});
  expect(items.filter(item=>item.key==='p:a')).toHaveLength(1);expect(items.find(item=>item.kind==='file')?.project).toBe('Nested');
  const sorted=orderedWorkspaceItems(items,[],{},new Map(items.map(item=>[item.key,item.timestamp])));expect(sorted).toHaveLength(4);
});
it('shows Mac hints on the modifier key event even before metaKey is set',()=>{
  const platform=vi.spyOn(navigator,'platform','get').mockReturnValue('MacIntel');const h=harness();
  try{h.render();act(()=>window.dispatchEvent(new KeyboardEvent('keydown',{key:'Meta',code:'MetaLeft'})));expect(h.host.querySelector('[data-item-key="p:b"] kbd')?.textContent).toBe('⌘1');act(()=>window.dispatchEvent(new KeyboardEvent('keyup',{key:'Meta'})));expect(h.host.querySelector('kbd')).toBeNull();}finally{h.cleanup();platform.mockRestore();}
});
