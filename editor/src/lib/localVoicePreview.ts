/** Read real partial Qwen output; drafts replace prior text rather than append it. */
export async function localVoicePreview(audio: Blob, signal: AbortSignal, update: (text: string, complete: boolean) => void): Promise<void> {
  const response = await fetch('/api/personal/voice/preview', {method: 'POST', body: audio, signal});
  if (!response.ok || !response.body) throw new Error('Local captions unavailable');
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let pending = '', bytes = 0, done = false;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      bytes += part.value.length;
      if (bytes > 1024 * 1024) throw new Error('Caption response too large');
      pending += decoder.decode(part.value, {stream: true});
      let newline: number;
      while ((newline = pending.indexOf('\n')) >= 0) {
        const line = pending.slice(0, newline); pending = pending.slice(newline + 1);
        if (!line.trim()) continue;
        const value = JSON.parse(line);
        if (value.error) throw new Error('Local captions unavailable');
        if (typeof value.text === 'string' && !signal.aborted) update(value.text, value.done === true);
        if (value.done === true) done = true;
      }
    }
    if (!done) throw new Error('Caption stream interrupted');
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
