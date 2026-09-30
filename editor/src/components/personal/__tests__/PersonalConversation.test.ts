// @vitest-environment happy-dom
import {act, createElement as h} from 'react';
import {createRoot} from 'react-dom/client';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import PersonalConversation from '../PersonalConversation';
import {requestJson} from '../../../lib/http';
vi.mock('../../../lib/http', () => ({requestJson:vi.fn()}));
vi.mock('../../../lib/personalApi', () => ({personalApi:{}}));
let host: HTMLDivElement, root: ReturnType<typeof createRoot>;
const mock=vi.mocked(requestJson);
beforeEach(() => {
  Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true}); localStorage.clear(); vi.resetAllMocks();
  Element.prototype.scrollIntoView=vi.fn();
  mock.mockResolvedValue({turns:[],model:'fixture/model'});
  host=document.createElement('div'); document.body.append(host); root=createRoot(host);
});
afterEach(() => {act(()=>root.unmount());host.remove();vi.restoreAllMocks();});
async function mount() {await act(async()=>root.render(h(PersonalConversation)));}
function button(label:string) {return host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;}
it('starts with one conversation composer and keeps forms behind Activity',async()=>{
  await mount();
  expect(host.querySelector('h1')?.textContent).toBe('What can I help you with?');
  expect(host.querySelectorAll('textarea')).toHaveLength(1);
  expect(host.querySelector('.personal-review')).toBeNull();
  expect(host.textContent).not.toContain('New commitment');
});
it('keeps the exact draft operation after a lost response and restores the durable reply',async()=>{
  localStorage.setItem('dan.personal.conversation.draft.v1',JSON.stringify({text:'Remind me tomorrow at 2pm',operation:'chat-replay-001',sources:[]}));
  let fail=true;
  mock.mockImplementation(async(_url,options)=>{
    if(options?.method==='POST') {if(fail) throw new Error('Connection lost'); return {};}
    return {turns:fail?[]:[{id:'turn1',text:'Remind me tomorrow at 2pm',reply:'Reminder saved.',state:'completed'}],model:'fixture/model'};
  });
  await mount();
  await act(async()=>button('Send message').click());
  expect(host.querySelector('textarea')!.value).toBe('Remind me tomorrow at 2pm');
  fail=false;
  await act(async()=>button('Send message').click());
  const posts=mock.mock.calls.filter(([,options])=>options?.method==='POST');
  expect(posts).toHaveLength(2);
  expect(JSON.parse(posts[0][1]!.body as string).operation_id).toBe('chat-replay-001');
  expect(posts[0][1]!.body).toEqual(posts[1][1]!.body);
  expect(host.querySelector('textarea')!.value).toBe('');
  expect(host.textContent).toContain('Reminder saved.');
});
it('restores working state and sends Stop to the existing turn',async()=>{
  mock.mockResolvedValue({turns:[{id:'turn1',text:'Schedule it',state:'running'}],model:'fixture/model'});
  await mount();
  expect(button('Send message')).toBeNull();
  await act(async()=>button('Stop Diane').click());
  expect(mock).toHaveBeenCalledWith('/api/personal/conversation/turn1/stop',{method:'POST'});
});
it('does not silently accept a message when no AI model is configured',async()=>{
  mock.mockResolvedValue({turns:[],model:''});
  await mount();
  expect(button('Send message').disabled).toBe(true);
  expect(host.textContent).toContain('Chat needs an AI connection');
});

it('updates elapsed time every second from the saved turn and stops when the reply arrives',async()=>{
  vi.useFakeTimers();
  try {
    vi.setSystemTime(new Date('2026-09-30T02:00:00Z'));
    mock.mockResolvedValue({turns:[{id:'turn1',text:'Help me',state:'running',created_at:'2026-09-30T01:00:01Z'}],model:'fixture/model'});
    await mount();
    expect(host.querySelector('time')?.textContent).toBe('00h59m59s');
    await act(async()=>{await vi.advanceTimersByTimeAsync(1000);});
    expect(host.querySelector('time')?.textContent).toBe('01h00m00s');
    mock.mockResolvedValue({turns:[{id:'turn1',text:'Help me',state:'completed',reply:'Done.',created_at:'2026-09-30T01:00:01Z'}],model:'fixture/model'});
    await act(async()=>{await vi.advanceTimersByTimeAsync(1000);});
    expect(host.querySelector('time')).toBeNull();
    expect(host.textContent).toContain('Done.');
  } finally {vi.useRealTimers();}
});
