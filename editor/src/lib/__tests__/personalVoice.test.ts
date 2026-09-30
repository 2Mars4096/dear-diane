// @vitest-environment happy-dom
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {PersonalVoiceSession, VoiceActivity, encodeWav, meaningfulVoice, type VoiceCallbacks} from '../personalVoice';
import {requestJson} from '../http';
vi.mock('../http', () => ({requestJson: vi.fn()}));
const request = vi.mocked(requestJson);
let node: {port: {onmessage: ((event: {data: Float32Array}) => void) | null}; disconnect: ReturnType<typeof vi.fn>};
let stopTrack: ReturnType<typeof vi.fn>, stopPlayer: ReturnType<typeof vi.fn>, callbacks: VoiceCallbacks;
let session: PersonalVoiceSession | undefined;
function frames(value: number, count: number) { for (let i = 0; i < count; i++) node.port.onmessage?.({data: new Float32Array(2048).fill(value)}); }
async function flush() { for (let i = 0; i < 20; i++) await Promise.resolve(); }
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks();
  stopTrack = vi.fn(); stopPlayer = vi.fn();
  callbacks = {status: vi.fn(), transcript: vi.fn(), error: vi.fn(), refresh: vi.fn(), ended: vi.fn()};
  const track = {stop: stopTrack, onended: null};
  vi.stubGlobal('AudioContext', class {
    sampleRate = 48000; destination = {}; audioWorklet = {addModule: vi.fn(async () => {})};
    resume = vi.fn(async () => {}); close = vi.fn(async () => {});
    createMediaStreamSource = () => ({connect: vi.fn(), disconnect: vi.fn()});
    decodeAudioData = vi.fn(async () => ({}));
    createBufferSource = () => ({connect: vi.fn(), start: vi.fn(), stop: stopPlayer, playbackRate: {value: 1}});
  });
  vi.stubGlobal('AudioWorkletNode', class {
    port = {onmessage: null}; connect = vi.fn(); disconnect = vi.fn();
    constructor() { node = {port: this.port, disconnect: this.disconnect}; }
  });
  Object.defineProperty(navigator, 'mediaDevices', {configurable: true, value: {getUserMedia: vi.fn(async () => ({getTracks: () => [track], getAudioTracks: () => [track]}))}});
});
afterEach(async () => {session?.end(); session = undefined; await flush(); vi.useRealTimers(); vi.unstubAllGlobals();});

it('ignores silence, preserves initial speech, and automatically finishes a turn', async () => {
  const begin = vi.fn(), end = vi.fn();
  const detector = new VoiceActivity(48000, begin, end);
  for (let i = 0; i < 50; i++) detector.feed(new Float32Array(2048));
  expect(begin).not.toHaveBeenCalled();
  for (let i = 0; i < 12; i++) detector.feed(new Float32Array(2048).fill(0.2));
  for (let i = 0; i < 96; i++) detector.feed(new Float32Array(2048));
  expect(begin).toHaveBeenCalledTimes(1); expect(end).toHaveBeenCalledTimes(1);
  const blob = end.mock.calls[0][0] as Blob;
  const view = new DataView(await blob.arrayBuffer());
  expect(view.getUint32(24, true)).toBe(16000);
  expect(blob.size).toBeLessThanOrEqual(960044);
});

it('bounds long recordings and encodes signed PCM correctly', async () => {
  const blob = encodeWav([new Float32Array(48000 * 31).fill(-1)], 48000);
  expect(blob.size).toBe(960044);
  const view = new DataView(await blob.arrayBuffer());
  expect(view.getInt16(44, true)).toBe(-32768);
});

it('keeps listening across two turns and interrupts playback with context', async () => {
  let current = 'turn-1';
  request.mockImplementation(async (url, options) => {
    if (url.includes('/transcribe')) return {text: 'Somewhere cozier'};
    if (url.endsWith('/speech')) return {audio: btoa('synthetic'), rate: 1};
    if (options?.method === 'POST') return {id: current, state: 'queued'};
    return {turns: [{id: current, state: 'completed', reply: 'Here is an idea.'}]};
  });
  session = new PersonalVoiceSession('composed', callbacks); await session.start();
  frames(0.2, 12); frames(0, 96); await flush();
  await vi.advanceTimersByTimeAsync(1100); await flush();
  expect(callbacks.status).toHaveBeenCalledWith('Speaking…');
  current = 'turn-2'; frames(0.2, 12); frames(0, 96); await flush();
  expect(stopPlayer).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1100); await flush();
  const posts = request.mock.calls.filter(([url, options]) => url === '/api/personal/conversation' && options?.method === 'POST');
  expect(posts).toHaveLength(2);
  expect(JSON.parse(posts[1][1]!.body as string)).toMatchObject({voice_profile: 'composed', interrupted_turn_id: 'turn-1'});
  expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);
  session.end(); expect(stopTrack).toHaveBeenCalledTimes(1); expect(callbacks.ended).toHaveBeenCalledTimes(1);
});

it('stops a superseded pending action before admitting the next voice turn', async () => {
  let stopped = false;
  request.mockImplementation(async (url, options) => {
    if (url.includes('/transcribe')) return {text: 'Remind me tomorrow'};
    if (url.endsWith('/stop')) { stopped = true; return {}; }
    if (options?.method === 'POST') return {id: 'turn-1', state: 'queued'};
    return {turns: [{id: 'turn-1', state: stopped ? 'stopped' : 'running'}]};
  });
  session = new PersonalVoiceSession('warm', callbacks); await session.start();
  frames(0.2, 12); frames(0, 96); await flush();
  frames(0.2, 36); await flush();
  expect(request).toHaveBeenCalledWith('/api/personal/conversation/turn-1/stop', {method: 'POST'});
  expect(callbacks.error).not.toHaveBeenCalled();
});

it('cleans up permission that arrives after the session was ended', async () => {
  let permit!: (value: unknown) => void;
  vi.mocked(navigator.mediaDevices.getUserMedia).mockImplementation(() => new Promise(resolve => { permit = resolve as (value: unknown) => void; }));
  session = new PersonalVoiceSession('warm', callbacks); const start = session.start(); await flush(); session.end();
  permit({getTracks: () => [{stop: stopTrack}]}); await start;
  expect(stopTrack).toHaveBeenCalledTimes(1);
  expect(request).not.toHaveBeenCalled();
});

it('previews speech, keeps short pauses in one utterance, and waits for a longer break', () => {
  const begin=vi.fn(), end=vi.fn(), preview=vi.fn();
  const detector=new VoiceActivity(1000,begin,end,preview);
  const feed=(volume:number,count:number)=>{for(let i=0;i<count;i++) detector.feed(new Float32Array(100).fill(volume));};
  feed(0.2,20); feed(0,10);
  expect(end).not.toHaveBeenCalled();
  expect(preview.mock.calls.length).toBeGreaterThanOrEqual(2);
  feed(0.2,10); feed(0,14);
  expect(end).not.toHaveBeenCalled();
  feed(0,2);
  expect(begin).toHaveBeenCalledTimes(1); expect(end).toHaveBeenCalledTimes(1);
});

it('shows interim text without submitting a conversation turn', async () => {
  request.mockResolvedValue({text:'Find a cozy place'});
  session=new PersonalVoiceSession('warm',callbacks); await session.start();
  frames(0.2,80); await flush();
  expect(callbacks.transcript).toHaveBeenCalledWith('Find a cozy place');
  expect(request.mock.calls.filter(([url])=>url==='/api/personal/conversation')).toHaveLength(0);
  session.end();
  expect(stopTrack).toHaveBeenCalledTimes(1);
});

it('preserves earlier audio when the user resumes during final transcription', async () => {
  let resolveFirst!: (value: {text:string})=>void;
  let firstSize=0, secondSize=0, transcriptions=0;
  request.mockImplementation(async(url,options)=>{
    if(url.includes('/transcribe')) {
      transcriptions++;
      if(transcriptions===1) {firstSize=(options!.body as Blob).size; return new Promise(resolve=>{resolveFirst=resolve;});}
      secondSize=(options!.body as Blob).size;
      return {text:'Find a cozy place in Central'};
    }
    if(url.endsWith('/speech')) return {audio:btoa('synthetic'),rate:1};
    if(options?.method==='POST') return {id:'combined',state:'queued'};
    return {turns:[{id:'combined',state:'completed',reply:'Okay'}]};
  });
  session=new PersonalVoiceSession('warm',callbacks); await session.start();
  frames(0.2,12); frames(0,96); await flush();
  frames(0.2,12); frames(0,96);
  resolveFirst({text:'Find a cozy place'}); await flush();
  expect(secondSize).toBeGreaterThan(firstSize);
  const posts=request.mock.calls.filter(([url,options])=>url==='/api/personal/conversation' && options?.method==='POST');
  expect(posts).toHaveLength(1);
  expect(JSON.parse(posts[0][1]!.body as string).text).toBe('Find a cozy place in Central');
});

it('coalesces slow previews to the newest audio without overlapping requests',async()=>{
  let resolveFirst!: (value:{text:string})=>void;
  let calls=0; const sizes:number[]=[];
  request.mockImplementation(async(url,options)=>{
    if(!url.includes('/transcribe')) throw new Error('No conversation should be submitted');
    sizes.push((options!.body as Blob).size);
    if(++calls===1) return new Promise(resolve=>{resolveFirst=resolve;});
    return {text:'The latest complete phrase'};
  });
  session=new PersonalVoiceSession('warm',callbacks); await session.start();
  frames(.2,36); await flush();
  expect(calls).toBe(1);
  frames(.2,100); await flush();
  expect(calls).toBe(1);
  resolveFirst({text:'The first words'}); await flush();
  expect(calls).toBe(2);
  expect(sizes[1]).toBeGreaterThan(sizes[0]*3);
  expect(callbacks.transcript).toHaveBeenLastCalledWith('The latest complete phrase');
});

it('previews a brief phrase during a pause without repeatedly transcribing silence',()=>{
  const end=vi.fn(), preview=vi.fn();
  const detector=new VoiceActivity(1000,vi.fn(),end,preview);
  for(let i=0;i<6;i++) detector.feed(new Float32Array(100).fill(.2));
  for(let i=0;i<6;i++) detector.feed(new Float32Array(100));
  expect(preview).toHaveBeenCalledTimes(1);
  for(let i=0;i<7;i++) detector.feed(new Float32Array(100));
  expect(preview).toHaveBeenCalledTimes(1);
  expect(end).not.toHaveBeenCalled();
});

it('filters only hesitation sounds, preserving short answers and substantive speech', () => {
  for (const text of ['嗯。', '嘶。', '呃，嗯', 'um, uh...', 'hmm', '']) expect(meaningfulVoice(text)).toBe(false);
  for (const text of ['yes', 'no', 'stop', '好', '不', '停', '嗯，明天提醒我', 'um, somewhere cozy']) expect(meaningfulVoice(text)).toBe(true);
});
it.each(['running', 'completed'])('leaves a %s response alone during filler-only speech', async state => {
  let text='Find a cozy restaurant';
  request.mockImplementation(async (url, options) => {
    if(url.includes('/transcribe')) return {text};
    if(url.endsWith('/speech')) return {audio:btoa('synthetic'),rate:1};
    if(options?.method==='POST') return {id:'original',state};
    return {turns:[{id:'original',state,reply:'An idea'}]};
  });
  session=new PersonalVoiceSession('warm',callbacks); await session.start();
  frames(.2,12);frames(0,96);await flush();
  expect(callbacks.transcript).toHaveBeenLastCalledWith('');
  text='嘶。'; frames(.2,36);await flush();frames(0,96);await flush();
  expect(request.mock.calls.filter(([url])=>url.endsWith('/stop'))).toHaveLength(0);
  expect(request.mock.calls.filter(([url,options])=>url==='/api/personal/conversation' && options?.method==='POST')).toHaveLength(1);
  expect(stopPlayer).not.toHaveBeenCalled();
  expect(callbacks.error).not.toHaveBeenCalled();
});

it('keeps voice open after a failed reply already visible in chat', async () => {
  request.mockImplementation(async (url, options) => {
    if(url.includes('/transcribe')) return {text:'Find somewhere cozy'};
    if(options?.method==='POST') return {id:'failed-turn',state:'failed',reply:'Budget exhausted'};
    return {turns:[]};
  });
  session=new PersonalVoiceSession('warm',callbacks);await session.start();
  frames(.2,12);frames(0,96);await flush();
  expect(callbacks.transcript).toHaveBeenLastCalledWith('');
  expect(callbacks.status).toHaveBeenLastCalledWith('Listening…');
  expect(callbacks.error).not.toHaveBeenCalled();
  expect(stopTrack).not.toHaveBeenCalled();
});

it('takes timed snapshots during uninterrupted speech and includes a little boundary context', () => {
  const preview=vi.fn(), end=vi.fn();
  const detector=new VoiceActivity(1000,vi.fn(),end,preview);
  for(let i=0;i<6;i++) detector.feed(new Float32Array(100).fill(.2));
  expect(preview).not.toHaveBeenCalled();
  for(let i=0;i<4;i++) detector.feed(new Float32Array(100).fill(.2));
  expect(preview).toHaveBeenCalledTimes(1);
  for(let i=0;i<10;i++) detector.feed(new Float32Array(100).fill(.2));
  expect(preview).toHaveBeenCalledTimes(2);
  expect((preview.mock.calls[1][0] as Blob).size).toBeGreaterThan((preview.mock.calls[0][0] as Blob).size);
  expect(end).not.toHaveBeenCalled();
});

it('reuses a slow preview covering all speech after the pause instead of restarting ASR', async () => {
  let finish!: (value:{text:string})=>void;
  let previewSignal: AbortSignal | undefined;
  request.mockImplementation(async (url, options) => {
    if(url.includes('/transcribe')) {
      previewSignal=options?.signal as AbortSignal;
      return new Promise(resolve=>{finish=resolve;});
    }
    if(options?.method==='POST') return {id:'reused',state:'queued'};
    return {turns:[]};
  });
  session=new PersonalVoiceSession('warm',callbacks);await session.start();
  frames(.2,12);frames(0,12);await flush();
  expect(request.mock.calls.filter(([url])=>url.includes('/transcribe'))).toHaveLength(1);
  frames(0,84);await flush();
  expect(previewSignal?.aborted).toBe(false);
  expect(request.mock.calls.filter(([url])=>url.includes('/transcribe'))).toHaveLength(1);
  finish({text:'A quiet place please'});await flush();
  const posts=request.mock.calls.filter(([url,o])=>url==='/api/personal/conversation' && o?.method==='POST');
  expect(posts).toHaveLength(1);
  expect(JSON.parse(posts[0][1]!.body as string).text).toBe('A quiet place please');
  expect(callbacks.transcript).toHaveBeenCalledWith('A quiet place please');
  expect(callbacks.transcript).toHaveBeenLastCalledWith('');
});

it('recognizes quiet syllables separated by short gaps instead of waiting forever', () => {
  const begin = vi.fn(), end = vi.fn(), preview = vi.fn();
  const detector = new VoiceActivity(48000, begin, end, preview);
  for (let i = 0; i < 12; i++) {
    detector.feed(new Float32Array(2048).fill(0.01));
    detector.feed(new Float32Array(2048));
  }
  expect(begin).toHaveBeenCalledTimes(1);
  expect(preview).toHaveBeenCalled();
  for (let i = 0; i < 40; i++) detector.feed(new Float32Array(2048));
  expect(end).toHaveBeenCalledTimes(1);
});

it('reports missing capture frames and releases the microphone', async () => {
  session = new PersonalVoiceSession('warm', callbacks); await session.start();
  await vi.advanceTimersByTimeAsync(6000);
  expect(callbacks.error).toHaveBeenCalledWith(expect.stringContaining('No microphone audio'));
  expect(stopTrack).toHaveBeenCalledTimes(1);
});

it('keeps ordinary silence open when the microphone is delivering frames', async () => {
  session = new PersonalVoiceSession('warm', callbacks); await session.start();
  for (let i = 0; i < 8; i++) { frames(0, 1); await vi.advanceTimersByTimeAsync(1000); }
  expect(callbacks.error).not.toHaveBeenCalled();
  expect(request).not.toHaveBeenCalled();
});
