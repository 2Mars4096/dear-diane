// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import SessionMenu from '../SessionMenu';
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
it('renames inline, keeps failed actions retryable, and shows danger-styled deletion while protecting running sessions', async () => {
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host), action = vi.fn().mockRejectedValueOnce(new Error('Offline')), close = vi.fn();
  const render = (archived=false, running=true) => act(() => root.render(createElement(SessionMenu, { x:10,y:10,session:{id:'s',workflow_id:'p'},title:'Original',archived,pinned:false,unread:false,running,projects:[],onAction:action,onClose:close })));
  const button = (label:string) => [...document.querySelectorAll('button')].find(node=>node.textContent===label)!;
  try {
    render(); expect(button('Delete permanently…').disabled).toBe(true); expect(button('Delete permanently…').className).toBe('wb-session-delete'); expect(button('Fork conversation').disabled).toBe(true);
    act(()=>button('Rename').click());
    await act(async()=>document.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
    expect(action).toHaveBeenCalledWith('rename','Original'); expect(document.querySelector('[role=alert]')?.textContent).toBe('Offline'); expect(close).not.toHaveBeenCalled();
    act(()=>button('Back').click());render(true);expect(button('Restore')).toBeTruthy();expect(button('Delete permanently…')).toBeTruthy();expect(button('Open in workspace')).toBeUndefined();
    render(false,false); action.mockResolvedValue(undefined); await act(async()=>button('Delete permanently…').click()); expect(action).toHaveBeenCalledWith('delete',undefined);
    act(()=>document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'})));expect(close).toHaveBeenCalled();
  } finally {act(()=>root.unmount());host.remove();}
});
