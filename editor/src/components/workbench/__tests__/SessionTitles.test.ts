// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import SessionTitles from '../SessionTitles';
import type { ChatV2ThreadSummary } from '../../../lib/chatV2Api';
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });
it('does not transfer existing sessions and requires explicit opt-in for new ones', async () => {
 Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
 const host=document.createElement('div'), root=createRoot(host);
 const fetcher=vi.fn(async(_url: string, _options?: RequestInit)=>new Response(JSON.stringify({title:null}))); vi.stubGlobal('fetch',fetcher);
 const thread = {id:'old',workflow_id:'p',title_needs_summary:true,created_at:'2026-10-01T00:00:00Z'} as ChatV2ThreadSummary;
 try {
  await act(async()=>root.render(createElement(SessionTitles,{threads:[thread],onUpdated:vi.fn()})));
  expect(fetcher).not.toHaveBeenCalled();
  localStorage.setItem('dan.sessionTitles.enabledAt.v1',String(Date.parse('2026-10-02T00:00:00Z')));
  await act(async()=>root.render(createElement(SessionTitles,{threads:[thread,{...thread,id:'new',created_at:'2026-10-03T00:00:00Z'}],onUpdated:vi.fn()})));
  expect(fetcher).toHaveBeenCalledTimes(1); expect(fetcher.mock.calls[0][0]).toBe('/api/chats/p/new/title');
 } finally { act(()=>root.unmount()); }
});
