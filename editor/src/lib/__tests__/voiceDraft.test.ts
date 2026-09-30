// @vitest-environment happy-dom
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {VoiceDraft} from '../voiceDraft';
class FakeRecognition {
  static instances: FakeRecognition[] = [];
  continuous = false; interimResults = false; lang = '';
  onresult: ((event: {results: {isFinal:boolean; 0:{transcript:string}}[]}) => void) | null = null;
  onerror: ((event:{error:string})=>void) | null = null;
  onend: (()=>void) | null = null;
  start = vi.fn(); abort = vi.fn();
  constructor() { FakeRecognition.instances.push(this); }
  words(...texts: string[]) { this.onresult?.({results:texts.map(text=>({isFinal:false,0:{transcript:text}}))}); }
}
let draft: VoiceDraft;
beforeEach(()=>{vi.useFakeTimers(); FakeRecognition.instances=[]; vi.stubGlobal('webkitSpeechRecognition',FakeRecognition);});
afterEach(()=>{draft?.stop();vi.useRealTimers();vi.unstubAllGlobals();});
it('shows incremental words immediately and replaces revisions without duplication',()=>{
 const output=vi.fn();draft=new VoiceDraft(output);draft.start();
 const r=FakeRecognition.instances[0];
 expect(r.continuous && r.interimResults).toBe(true);
 r.words('Find');r.words('Find a cozy');r.words('Find a cozy restaurant');
 expect(output.mock.calls.map(([text])=>text)).toEqual(['Find','Find a cozy','Find a cozy restaurant']);
});
it('invalidates late callbacks at reset and clears previous-turn words',()=>{
 const output=vi.fn();draft=new VoiceDraft(output);draft.start();
 const r=FakeRecognition.instances[0], late=r.onresult!;
 r.words('old words');draft.reset();
 late({results:[{isFinal:false,0:{transcript:'stale words'}}]});
 FakeRecognition.instances[1].words('new words');
 expect(output.mock.calls.map(([text])=>text)).toEqual(['old words','new words']);
 expect(r.abort).toHaveBeenCalledOnce();
});
it('continues after a natural service end and cancels restart on stop',()=>{
 const output=vi.fn();draft=new VoiceDraft(output);draft.start();
 const r=FakeRecognition.instances[0];
 r.onresult?.({results:[{isFinal:true,0:{transcript:'Find somewhere'}}]});r.onend?.();
 vi.advanceTimersByTime(250);FakeRecognition.instances[1].words('cozy');
 expect(output).toHaveBeenLastCalledWith('Find somewhere cozy');
 FakeRecognition.instances[1].onend?.();draft.stop();vi.advanceTimersByTime(1000);
 expect(FakeRecognition.instances).toHaveLength(2);
});
it('falls back on permission failure without a restart loop',()=>{
 const unavailable=vi.fn();draft=new VoiceDraft(vi.fn(),unavailable);draft.start();
 const r=FakeRecognition.instances[0];r.onerror?.({error:'not-allowed'});
 expect(unavailable).toHaveBeenCalledOnce();expect(r.abort).toHaveBeenCalledOnce();
 draft.reset();vi.advanceTimersByTime(1000);expect(FakeRecognition.instances).toHaveLength(1);
});
