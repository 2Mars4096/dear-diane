import {requestJson} from './http';

export type VoiceProfile = 'warm' | 'bright' | 'steady' | 'composed';
export type VoiceStatus = 'Connecting…' | 'Listening…' | 'Hearing you…' | 'Thinking…' | 'Speaking…';
export type VoiceCallbacks = {
  status: (status: VoiceStatus) => void;
  transcript: (text: string) => void;
  error: (message: string) => void;
  refresh: () => void;
  ended: () => void;
};

/** Local amplitude VAD with leading audio, sustained onset and a pause to finish. */
export class VoiceActivity {
  private leading: Float32Array[] = [];
  private chunks: Float32Array[] = [];
  private loud = 0;
  private quiet = 0;
  private duration = 0;
  private previewAt = 0.6;
  private voiced = 0;
  private previewedVoice = 0;
  private lastPreview = 0;
  private previewAudio?: Blob;
  private speaking = false;
  private rate: number;
  private begin: () => void;
  private end: (data: Blob, coveredPreview?: Blob) => void;
  private preview?: (data: Blob) => void;
  constructor(rate: number, begin: () => void, end: (data: Blob, coveredPreview?: Blob) => void, preview?: (data: Blob) => void) { this.rate = rate; this.begin = begin; this.end = end; this.preview = preview; }
  feed(frame: Float32Array) {
    const seconds = frame.length / this.rate;
    const rms = Math.sqrt(frame.reduce((sum, value) => sum + value * value, 0) / frame.length);
    if (!this.speaking) {
      this.leading.push(frame);
      while (this.leading.length > Math.ceil(this.rate * 0.4 / frame.length)) this.leading.shift();
      this.loud = rms > 0.015 ? this.loud + seconds : 0;
      if (this.loud < 0.18) return;
      this.speaking = true; this.duration = 0; this.previewAt = 0.6; this.voiced = 0; this.previewedVoice = 0; this.lastPreview = 0; this.quiet = 0;
      this.chunks = this.leading; this.leading = []; this.previewAudio = undefined; this.begin();
    } else this.chunks.push(frame);
    this.duration += seconds;
    this.quiet = rms > 0.012 ? 0 : this.quiet + seconds;
    if (rms > 0.012) this.voiced += seconds;
    if (this.quiet >= 1.5 || this.duration >= 29) {
      const captured = this.chunks;
      this.chunks = []; this.speaking = false; this.loud = 0;
      this.end(encodeWav(captured, this.rate), this.voiced === this.previewedVoice ? this.previewAudio : undefined);
    } else if (this.voiced > this.previewedVoice && this.duration - this.lastPreview >= 0.5 && ((this.duration >= this.previewAt && (this.quiet >= 0.08 || this.duration >= this.previewAt + 0.15)) || this.quiet >= 0.35)) {
      this.previewAt = this.duration + 0.8;
      this.lastPreview = this.duration; this.previewedVoice = this.voiced;
      this.previewAudio = encodeWav(this.chunks, this.rate);
      this.preview?.(this.previewAudio);
    }
  }
}

export function encodeWav(chunks: Float32Array[], rate: number): Blob {
  const samples = new Float32Array(chunks.reduce((sum, chunk) => sum + chunk.length, 0));
  let offset = 0;
  for (const chunk of chunks) { samples.set(chunk, offset); offset += chunk.length; }
  const count = Math.min(480000, Math.floor(samples.length * 16000 / rate));
  const buffer = new ArrayBuffer(44 + count * 2), view = new DataView(buffer);
  const ascii = (at: number, text: string) => { [...text].forEach((char, index) => view.setUint8(at + index, char.charCodeAt(0))); };
  ascii(0, 'RIFF'); view.setUint32(4, 36 + count * 2, true); ascii(8, 'WAVE'); ascii(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  ascii(36, 'data'); view.setUint32(40, count * 2, true);
  for (let i = 0; i < count; i++) {
    const from = Math.floor(i * rate / 16000), to = Math.max(from + 1, Math.floor((i + 1) * rate / 16000));
    let value = 0;
    for (let j = from; j < Math.min(to, samples.length); j++) value += samples[j];
    value = Math.max(-1, Math.min(1, value / (to - from)));
    view.setInt16(44 + i * 2, value < 0 ? value * 32768 : value * 32767, true);
  }
  return new Blob([buffer], {type: 'audio/wav'});
}

/** Join a resumed utterance before it has been admitted as an action. */
export async function joinVoiceAudio(first: Blob | undefined, second: Blob): Promise<Blob> {
  if (!first) return second;
  const [a, b] = await Promise.all([first.arrayBuffer(), second.arrayBuffer()]);
  const size = a.byteLength + b.byteLength - 44;
  if (size > 960044) throw new Error('That voice message is over 30 seconds. Please use shorter messages.');
  const bytes = new Uint8Array(size);
  bytes.set(new Uint8Array(a)); bytes.set(new Uint8Array(b, 44), a.byteLength);
  const view = new DataView(bytes.buffer);
  view.setUint32(4, size - 8, true); view.setUint32(40, size - 44, true);
  return new Blob([bytes], {type: 'audio/wav'});
}

/** Ignore hesitation sounds only; short answers and commands remain meaningful. */
export function meaningfulVoice(text: string): boolean {
  const words = text.toLowerCase().replace(/[\p{P}\p{S}]/gu, ' ').trim();
  return !!words && !/^(?:(?:u+h+|u+m+|e+r+m*|h+m+|嗯+|呃+|唔+|啊+|额+)\s*)+$/u.test(words);
}

type Turn = {id: string; state: string; reply?: string};
const running = (turn: Turn) => ['queued', 'running', 'applying'].includes(turn.state);
const sleep = (ms: number, signal: AbortSignal) => new Promise<void>((resolve, reject) => {
  const abort = () => { clearTimeout(timer); reject(new DOMException('Stopped', 'AbortError')); };
  const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
  if (signal.aborted) abort(); else signal.addEventListener('abort', abort, {once: true});
});

/** One explicitly opened session, continuously listening until end/navigation. */
export class PersonalVoiceSession {
  private context?: AudioContext;
  private stream?: MediaStream;
  private capture?: AudioWorkletNode;
  private source?: MediaStreamAudioSourceNode;
  private player?: AudioBufferSourceNode;
  private request?: AbortController;
  private previewRequest?: AbortController;
  private queuedPreview?: {data: Blob; generation: number};
  private pendingAudio?: Blob;
  private previewResult?: {data: Blob; result: Promise<string | undefined>};
  private generation = 0;
  private utterance = 0;
  private recognized = false;
  private recognition?: AbortController;
  private closed = false;
  private turn?: string;
  private spokenTurn?: string;
  private interruptedTurn?: string;
  private pending: Promise<void> = Promise.resolve();
  private profile: VoiceProfile;
  private callbacks: VoiceCallbacks;
  constructor(profile: VoiceProfile, callbacks: VoiceCallbacks) { this.profile = profile; this.callbacks = callbacks; }

  async start() {
    try {
      if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) throw new Error('Voice needs microphone access in a secure browser or the Mac app.');
      this.context = new AudioContext();
      await this.context.resume();
      this.callbacks.status('Connecting…');
      const media = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1}});
      if (this.closed) { media.getTracks().forEach(track => track.stop()); return; }
      this.stream = media;
      media.getAudioTracks().forEach(track => { track.onended = () => this.fail(new Error('Microphone disconnected.')); });
      await this.context.audioWorklet.addModule('/app/voice-capture.js');
      if (this.closed) return;
      this.source = this.context.createMediaStreamSource(media);
      this.capture = new AudioWorkletNode(this.context, 'diane-voice-capture');
      const detector = new VoiceActivity(this.context.sampleRate, () => this.beginUtterance(), (data, coveredPreview) => {
        const utterance = this.utterance;
        void this.transcribe(data, utterance, coveredPreview).catch(error => {
          if (utterance === this.utterance && !this.closed) this.fail(error);
        });
      }, data => { void this.preview(data); });
      this.capture.port.onmessage = event => { if (!this.closed) detector.feed(event.data); };
      this.source.connect(this.capture); this.capture.connect(this.context.destination);
      this.callbacks.status('Listening…');
      document.addEventListener('visibilitychange', this.visibility);
      window.addEventListener('pagehide', this.end);
      window.addEventListener('hashchange', this.navigation);
    } catch (error) { if (!this.closed) this.fail(error); }
  }

  private navigation = () => { if (location.hash !== '#personal') this.end(); };
  private visibility = () => { if (document.hidden) this.end(); };
  private fail(error: unknown) {
    this.end();
    const message = error instanceof Error ? error.message : 'Voice stopped. You can keep typing.';
    this.callbacks.error(message === 'Permission denied' ? 'Microphone access was denied. Allow it in browser settings to use voice.' : message);
  }
  private beginUtterance() {
    this.utterance++; this.recognized = false; this.previewResult = undefined;
    this.recognition?.abort();
    this.queuedPreview = undefined; this.previewRequest?.abort(); this.previewRequest = undefined;
    if (!this.pendingAudio) this.callbacks.transcript('');
  }
  private async preview(data: Blob) {
    if (this.closed) return;
    if (this.previewRequest) { this.queuedPreview = {data, generation: this.utterance}; return; }
    const controller = new AbortController(); this.previewRequest = controller;
    const utterance = this.utterance;
    let resolveResult!: (text: string | undefined) => void;
    const result = new Promise<string | undefined>(resolve => { resolveResult = resolve; });
    this.previewResult = {data, result};
    try {
      const audio = await joinVoiceAudio(this.pendingAudio, data);
      if (controller.signal.aborted || this.closed) return;
      const {text} = await requestJson<{text: string}>(`/api/personal/voice/transcribe?operation_id=${crypto.randomUUID()}`, {method: 'POST', body: audio, signal: controller.signal, timeoutMs: 70000});
      resolveResult(controller.signal.aborted ? undefined : text);
      if (!this.closed && utterance === this.utterance && !controller.signal.aborted && meaningfulVoice(text)) {
        this.interrupt(); this.callbacks.transcript(text);
      }
    } catch { /* A missed preview must not submit or discard the final utterance. */ }
    finally {
      resolveResult(undefined);
      if (this.previewRequest === controller) {
        this.previewRequest = undefined;
        const latest = this.queuedPreview; this.queuedPreview = undefined;
        if (latest && !this.closed && latest.generation === this.utterance) void this.preview(latest.data);
      }
    }
  }
  private interrupt() {
    if (this.recognized) return;
    this.recognized = true;
    this.generation++;
    this.request?.abort();
    if (this.spokenTurn) this.interruptedTurn = this.spokenTurn;
    this.player?.stop(); this.player = undefined; this.spokenTurn = undefined;
    this.pending = this.pending.then(() => this.stopTurn());
    this.callbacks.status('Hearing you…');
  }
  private async transcribe(data: Blob, utterance: number, coveredPreview?: Blob) {
    // Keep slow previews visible. Reuse only a snapshot containing every voiced frame.
    const reusable = coveredPreview && this.previewResult?.data === coveredPreview ? this.previewResult.result : undefined;
    this.queuedPreview = undefined;
    data = await joinVoiceAudio(this.pendingAudio, data);
    if (this.closed || utterance !== this.utterance) return;
    this.pendingAudio = data;
    const controller = new AbortController(); this.recognition = controller;
    const previewText = await reusable;
    if (this.closed || utterance !== this.utterance) return;
    const text = previewText ?? (await requestJson<{text: string}>(`/api/personal/voice/transcribe?operation_id=${crypto.randomUUID()}`, {method: 'POST', body: data, signal: controller.signal, timeoutMs: 70000})).text;
    if (this.closed || utterance !== this.utterance) return;
    this.previewRequest?.abort(); this.previewRequest = undefined; this.previewResult = undefined;
    this.pendingAudio = undefined;
    if (!meaningfulVoice(text)) { this.callbacks.transcript(''); return; }
    this.interrupt(); this.callbacks.transcript(text);
    const generation = this.generation;
    this.pending = this.respond(text, generation, utterance, this.pending).catch(error => {
      if (!this.closed && generation === this.generation) this.fail(error);
    });
  }
  private async stopTurn() {
    if (!this.turn) return;
    const turn = this.turn;
    await requestJson(`/api/personal/conversation/${encodeURIComponent(turn)}/stop`, {method: 'POST'});
    // Applying effects cannot be undone by stopping speech. Wait for the receipt.
    for (let attempt = 0; attempt < 20; attempt++) {
      const snapshot = await requestJson<{turns: Turn[]}>('/api/personal/conversation');
      if (!snapshot.turns.some(item => item.id === turn && running(item))) { this.turn = undefined; return; }
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw new Error('The previous action is finishing. Check the chat before continuing.');
  }
  private async respond(text: string, generation: number, utterance: number, previous: Promise<void>) {
    await previous;
    if (this.closed || generation !== this.generation) return;
    await this.stopTurn();
    if (this.closed || generation !== this.generation) return;
    const controller = new AbortController(); this.request = controller;
    const signal = controller.signal;
    this.callbacks.status('Thinking…');
    // Do not abort admission: retain the returned ID to stop a superseded turn.
    const turn = await requestJson<Turn>('/api/personal/conversation', {method: 'POST', body: JSON.stringify({operation_id: crypto.randomUUID(), text, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', voice_profile: this.profile, interrupted_turn_id: this.interruptedTurn || null})});
    this.turn = turn.id; this.interruptedTurn = undefined; this.callbacks.refresh();
    if (this.closed || generation !== this.generation) { await this.stopTurn(); return; }
    if (utterance === this.utterance) this.callbacks.transcript('');
    let reply = turn;
    for (let attempt = 0; running(reply); attempt++) {
      if (attempt >= 720) throw new Error('The reply is taking too long. Voice stopped; check the chat.');
      await sleep(1000, signal);
      const snapshot = await requestJson<{turns: Turn[]}>('/api/personal/conversation', {signal});
      reply = snapshot.turns.find(item => item.id === turn.id) || reply;
    }
    this.turn = undefined; this.callbacks.refresh();
    if (reply.state !== 'completed') { this.callbacks.status('Listening…'); return; }
    this.spokenTurn = reply.id;
    const speech = await requestJson<{audio: string; rate: number}>('/api/personal/voice/speech', {method: 'POST', body: JSON.stringify({operation_id: crypto.randomUUID(), turn_id: reply.id, profile: this.profile}), signal, timeoutMs: 70000});
    if (this.closed || generation !== this.generation || !this.context) return;
    const bytes = Uint8Array.from(atob(speech.audio), value => value.charCodeAt(0));
    const buffer = await this.context.decodeAudioData(bytes.buffer);
    if (this.closed || generation !== this.generation) return;
    this.player = this.context.createBufferSource(); this.player.buffer = buffer; this.player.playbackRate.value = speech.rate;
    this.player.connect(this.context.destination); this.spokenTurn = reply.id;
    this.player.onended = () => {
      if (!this.closed && generation === this.generation) { this.spokenTurn = undefined; this.player = undefined; this.callbacks.status('Listening…'); }
    };
    this.callbacks.status('Speaking…'); this.player.start();
  }

  end = () => {
    if (this.closed) return;
    this.closed = true; this.generation++; this.utterance++; this.request?.abort(); this.recognition?.abort();
    this.queuedPreview = undefined; this.previewRequest?.abort(); this.previewRequest = undefined; this.pendingAudio = undefined;
    this.player?.stop(); this.player = undefined;
    this.capture?.disconnect(); this.source?.disconnect();
    this.stream?.getTracks().forEach(track => { track.onended = null; track.stop(); });
    if (this.capture) this.capture.port.onmessage = null;
    void this.context?.close().catch(() => {});
    void this.pending.finally(() => this.stopTurn()).catch(() => { this.callbacks.error('Voice ended. Check the chat for the last action’s status.'); });
    document.removeEventListener('visibilitychange', this.visibility); window.removeEventListener('pagehide', this.end); window.removeEventListener('hashchange', this.navigation);
    this.callbacks.ended();
  };
}
