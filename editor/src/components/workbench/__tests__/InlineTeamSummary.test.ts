// @vitest-environment happy-dom
import { act, createElement as h } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { InlineTeamSummary } from '../InlineTeamSummary';
import { TeamWorkerStore } from '../teamWorkerStore';
import type { TeamWorker } from '../teamPresentation';
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
it('discloses run-specific worker summaries, updates results, and opens the chosen worker', () => {
 const store=new TeamWorkerStore();
 const worker: TeamWorker={worker_id:'w',profile_id:'slot',worker_name:'Worker 1',parent_run_id:'run',backend:'claude',status:'completed',prompt:'Improve effects',response:'Effects complete',error:'',native_session_id:'s'};
 store.update([worker,{...worker,worker_id:'other',parent_run_id:'older',prompt:'Unrelated work'}]);
 const host=document.createElement('div'),root=createRoot(host),open=vi.fn();
 try {
  act(()=>root.render(h(InlineTeamSummary,{store,runId:'run',lead:'Claude',onOpenWorker:open})));
  expect(host.textContent).toContain('Lead · Claude');
  expect(host.textContent).not.toContain('Effects complete');
  act(()=>host.querySelector<HTMLButtonElement>('.wb-inline-team-toggle')!.click());
  expect(host.textContent).toContain('Finished: Improve effects');
  expect(host.textContent).not.toContain('Unrelated work');
  act(()=>store.update([{...worker,response:'Reviewed result'}]));
  expect(host.textContent).toContain('Reviewed result');
  act(()=>host.querySelector<HTMLButtonElement>('.wb-team-slot-heading button')!.click());
  expect(open).toHaveBeenCalledWith(expect.objectContaining({worker_id:'w'}));
  act(()=>host.querySelector<HTMLButtonElement>('.wb-inline-team-toggle')!.click());
  expect(host.querySelector('.wb-team-slots')).toBeNull();
 } finally {act(()=>root.unmount());}
});
