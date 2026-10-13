// @vitest-environment happy-dom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import PlanPanel from '../PlanPanel';
import { emptyPlan, useSessionPlan, type PlanState } from '../sessionPlan';
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });
const state = (): PlanState => ({ ...emptyPlan(), mode:'plan', version:2, revision:1, document:{ title:'Paper search', outcome:'Reach every match', scope:'Includes: result list\nLater: semantic search', specifications:'Titles wrap', steps:'1. Inspect\n2. Implement\n3. Verify', assumptions:'Keep existing ranking', blockers:'' } });
function mount(initial = state()) {
  const host=document.createElement('div'); document.body.append(host); const root=createRoot(host);
  const props={ state:initial, loaded:true, saving:false, busy:false, error:'', permission:'Auto', storageKey:'test', onSave:vi.fn(async()=>{}), onDiscuss:vi.fn(), onAnswer:vi.fn(), onStart:vi.fn(), onProposal:vi.fn(async()=>{}), onReload:vi.fn(async()=>{}), onChat:vi.fn() };
  const render=()=>act(()=>root.render(createElement(PlanPanel,props))); render();
  const button=(text:string)=>Array.from(host.querySelectorAll('button')).find(b=>b.textContent?.includes(text))!;
  return { host, props, render, button, close:()=>{ act(()=>root.unmount()); host.remove(); } };
}
it('shows a chat plan without section editing or discussion and preserves start guards',()=>{
  const ui=mount();try {
    expect(ui.host.textContent).toContain('Titles wrap');
    expect(ui.host.textContent).not.toContain('Discuss');
    expect(ui.host.querySelector('textarea')).toBeNull();
    expect(ui.button('Start this plan').disabled).toBe(false);
    ui.props.permission='Read only';ui.render();expect(ui.button('Start this plan').disabled).toBe(true);
    ui.props.permission='Auto';ui.props.state={...state(),document:{...state().document!,blockers:'Choose source'}};ui.render();expect(ui.button('Start this plan').disabled).toBe(true);
  }finally{ui.close();}
});
for (const mode of ['single','multiple'] as const) it(`submits ${mode} choices explicitly without preselection`,()=>{
  const ui=mount({...state(),questions_asked:1,question:{question:'Which scope?',reason:'Changes work',recommended:'Results',alternatives:['Filters','Ranking'],selection_mode:mode}});
  try {
    const inputs=Array.from(ui.host.querySelectorAll<HTMLInputElement>('input'));
    expect(inputs.every(input=>!input.checked)).toBe(true);
    expect(ui.button('Submit answer').disabled).toBe(true);
    act(()=>inputs[0].click());act(()=>inputs[1].click());
    expect(inputs[0].checked).toBe(mode==='multiple');
    expect(inputs[0].type).toBe(mode==='multiple' ? 'checkbox' : 'radio');
    expect(ui.host.textContent).toContain(mode==='multiple' ? 'Choose multiple' : 'Choose one');
    expect(ui.props.onAnswer).not.toHaveBeenCalled();
    act(()=>ui.button('Submit answer').click());
    expect(ui.props.onAnswer).toHaveBeenCalledWith(mode==='multiple' ? 'For "Which scope?": Results; Filters' : 'For "Which scope?": Filters');
    expect(ui.host.textContent).not.toContain('Start this plan');
  }finally{ui.close();}
});
it('does not show a prior session plan when requests resolve after switching sessions',async()=>{
  let resolveFirst: (value: Response)=>void=()=>{};
  vi.stubGlobal('fetch',vi.fn((url:string)=>url.includes('/first/') ? new Promise<Response>(resolve=>{resolveFirst=resolve;}) : Promise.resolve(new Response(JSON.stringify({...state(),document:{...state().document!,title:'Second session'}})))));
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
  function Fixture({id}:{id:string}){const plan=useSessionPlan({workflowId:'project',id});return createElement('div',null,plan.state.document?.title||'Empty');}
  try {
    await act(async()=>root.render(createElement(Fixture,{id:'first'})));
    await act(async()=>root.render(createElement(Fixture,{id:'second'})));
    await act(async()=>resolveFirst(new Response(JSON.stringify(state()))));
    expect(host.textContent).toBe('Second session');
  }finally{act(()=>root.unmount());host.remove();}
});

it('submits a batch including Other and comments in one answer',()=>{
  const ui=mount({...state(),questions:[{question:'Scope?',reason:'',recommended:'Small',alternatives:['Large']},{question:'Targets?',reason:'',recommended:'Web',alternatives:['Desktop'],selection_mode:'multiple'}]});
  try {
    const groups=ui.host.querySelectorAll('fieldset');
    act(()=>groups[0].querySelectorAll<HTMLInputElement>('input[type=radio]')[2].click());
    const custom=groups[0].querySelector<HTMLTextAreaElement>('textarea')!;
    act(()=>{Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(custom,'Medium');custom.dispatchEvent(new Event('input',{bubbles:true}));});
    act(()=>groups[1].querySelectorAll<HTMLInputElement>('input[type=checkbox]')[0].click());
    act(()=>groups[1].querySelectorAll<HTMLInputElement>('input[type=checkbox]')[1].click());
    const comment=groups[1].querySelector('textarea')!;
    act(()=>{Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(comment,'Desktop first');comment.dispatchEvent(new Event('input',{bubbles:true}));});
    act(()=>ui.button('Submit answers').click());
    expect(ui.props.onAnswer).toHaveBeenCalledWith('For "Scope?": Medium\n\nFor "Targets?": Web; Desktop; Desktop first');
  }finally{ui.close();}
});
it('does not render an empty planning greeting',()=>{const ui=mount({...emptyPlan(),mode:'plan'});try{expect(ui.host.textContent).toBe('');}finally{ui.close();}});

it('selects Other and replaces the previous single choice when comments are typed',()=>{
  const ui=mount({...state(),question:{question:'Scope?',reason:'',recommended:'Small',alternatives:['Large']}});
  try {
    const inputs=ui.host.querySelectorAll<HTMLInputElement>('input[type=radio]');act(()=>inputs[0].click());
    const comment=ui.host.querySelector('textarea')!;
    act(()=>{Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(comment,'Custom scope');comment.dispatchEvent(new Event('input',{bubbles:true}));});
    expect(inputs[0].checked).toBe(false);expect(inputs[2].checked).toBe(true);
    expect(ui.host.textContent).not.toContain('selected ·');
  }finally{ui.close();}
});
