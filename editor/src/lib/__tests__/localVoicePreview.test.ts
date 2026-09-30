import {afterEach, expect, it, vi} from 'vitest';
import {localVoicePreview} from '../localVoicePreview';
afterEach(()=>vi.unstubAllGlobals());
it('decodes partial UTF-8 lines and sends cumulative captions without duplication', async()=>{
 const bytes=new TextEncoder().encode('{"text":"Hello"}\n{"text":"Hello你好","done":true}\n');
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(new ReadableStream({start(c){for(const byte of bytes)c.enqueue(new Uint8Array([byte]));c.close();}}))));
 const update=vi.fn();await localVoicePreview(new Blob(),new AbortController().signal,update);
 expect(update.mock.calls.map(([text])=>text)).toEqual(['Hello','Hello你好']);
});
it('rejects incomplete or failed streams for fallback',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response('{"error":"unavailable"}\n')));
 await expect(localVoicePreview(new Blob(),new AbortController().signal,vi.fn())).rejects.toThrow('unavailable');
});
