// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { ActivitySummary, elapsedLabel } from '../ActivitySummary';
Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
it('formats elapsed time without negative values or invalid timestamps',()=>{
 expect(elapsedLabel(1000,129000)).toBe('2m08s');expect(elapsedLabel(1000,0)).toBe('0m00s');expect(elapsedLabel(undefined,1000)).toBe('');expect(elapsedLabel(1000,NaN)).toBe('');
});
it('ticks from the recorded start, reports observed activity, and freezes at recorded completion',()=>{
 vi.useFakeTimers();vi.setSystemTime(129000);const host=document.createElement('div'),root=createRoot(host);
 const render=(active:boolean,end?:number)=>act(()=>root.render(createElement(ActivitySummary,{events:[],active,startedAt:1000,endedAt:end,live:'Running pytest checks',children:null})));
 try{render(true);expect(host.textContent).toContain('Thinking (2m08s)');expect(host.textContent).toContain('Running checks');act(()=>vi.advanceTimersByTime(2000));expect(host.textContent).toContain('(2m10s)');render(false,130000);expect(host.textContent).toContain('Work details (2m09s)');act(()=>vi.advanceTimersByTime(10000));expect(host.textContent).toContain('(2m09s)');expect(host.textContent).not.toContain('Running checks');}finally{act(()=>root.unmount());vi.useRealTimers();}
});
