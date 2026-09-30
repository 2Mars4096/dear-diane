/** Best-effort streaming captions. Never admits an action or owns turn boundaries. */
type Result = {isFinal: boolean; 0: {transcript: string}};
type Recognition = {
  continuous: boolean; interimResults: boolean; lang: string;
  onresult: ((event: {results: ArrayLike<Result>}) => void) | null;
  onerror: ((event: {error: string}) => void) | null;
  onend: (() => void) | null;
  start: () => void; abort: () => void;
};
type RecognitionWindow = {SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition};

export class VoiceDraft {
  private recognition?: Recognition;
  private timer?: ReturnType<typeof setTimeout>;
  private closed = false;
  private prefix = '';
  private committed = '';
  private failures = 0;
  private output: (text: string) => void;
  private unavailable: () => void;
  constructor(output: (text: string) => void, unavailable = () => {}) { this.output = output; this.unavailable = unavailable; }
  start() {
    if (this.closed) return;
    const browser = window as unknown as RecognitionWindow;
    const Constructor = browser.SpeechRecognition || browser.webkitSpeechRecognition;
    if (!Constructor) return;
    try {
      const recognition = new Constructor(); this.recognition = recognition;
      recognition.continuous = true; recognition.interimResults = true;
      recognition.lang = navigator.language || 'en-US';
      recognition.onresult = event => {
        if (this.closed || this.recognition !== recognition) return;
        const results = Array.from(event.results);
        this.committed = results.filter(item => item.isFinal).map(item => item[0].transcript).join(' ');
        const draft = [this.prefix, ...results.map(item => item[0].transcript)].filter(Boolean).join(' ').trim();
        if (draft) { this.failures = 0; this.output(draft); }
      };
      recognition.onerror = event => {
        if (event.error !== 'no-speech') { this.failures++; this.unavailable(); }
        if (['not-allowed', 'service-not-allowed', 'language-not-supported', 'audio-capture'].includes(event.error)) this.stop();
      };
      recognition.onend = () => {
        if (this.closed || this.recognition !== recognition) return;
        this.recognition = undefined;
        this.prefix = [this.prefix, this.committed].filter(Boolean).join(' '); this.committed = '';
        if (this.failures < 2) this.timer = setTimeout(() => this.start(), 250);
      };
      recognition.start();
    } catch { this.unavailable(); this.stop(); /* Server recognition remains available. */ }
  }
  /** New user turn: discard old recognizer callbacks and accumulated words. */
  reset() {
    if (this.closed) return;
    this.detach(); this.prefix = ''; this.committed = ''; this.start();
  }
  private detach() {
    clearTimeout(this.timer);
    const recognition = this.recognition; this.recognition = undefined;
    if (recognition) {
      recognition.onresult = null; recognition.onerror = null; recognition.onend = null;
      try { recognition.abort(); } catch { /* Already ended. */ }
    }
  }
  stop() { this.closed = true; this.detach(); }
}
