import type { Worker } from 'tesseract.js';

/** Uses only bundled, same-origin models. Images never leave the device for OCR. */
export async function recognizePersonalImage(file: File, signal?: AbortSignal): Promise<string> {
  if (file.size > 10 * 1024 * 1024) throw new Error('Choose an image no larger than 10 MiB.');
  let worker: Worker | undefined;
  let expired = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let abort: (() => void) | undefined;
  const interruption = new Promise<never>((_, reject) => {
    abort = () => { expired = true; reject(new Error('Image reading was stopped.')); };
    timer = setTimeout(() => { expired = true; reject(new Error('Image reading took too long. Try a smaller, clearer image.')); }, 90000);
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
  });
  const recognize = async () => {
    const bitmap = await createImageBitmap(file);
    try {
      if (expired) throw new Error('Image reading stopped.');
      if (!bitmap.width || bitmap.width * bitmap.height > 20_000_000) throw new Error('Choose an image no larger than 20 megapixels.');
      const scale = Math.min(1, 4096 / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.ceil(bitmap.width * scale); canvas.height = Math.ceil(bitmap.height * scale);
      const context = canvas.getContext('2d', { alpha: false });
      if (!context) throw new Error('This browser cannot read image text.');
      context.fillStyle = '#fff'; context.fillRect(0, 0, canvas.width, canvas.height); context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      const { createWorker } = await import('tesseract.js');
      const base = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/tesseract`;
      worker = await createWorker(['eng', 'chi_sim', 'chi_tra'], 1, { workerPath: `${base}/worker.min.js`, corePath: base, langPath: base, gzip: true, logger: () => undefined });
      if (expired) { await worker.terminate(); throw new Error('Image reading stopped.'); }
      const result = await worker.recognize(canvas);
      canvas.width = canvas.height = 1;
      const text = result.data.text.trim();
      if (!text) throw new Error('No readable text was found. Try a clearer image or paste a transcription.');
      if (new TextEncoder().encode(text).length > 65536) throw new Error('This image contains too much text. Capture a smaller section.');
      return text;
    } finally { bitmap.close(); }
  };
  try { return await Promise.race([recognize(), interruption]); }
  finally {
    if (timer) clearTimeout(timer);
    if (abort) signal?.removeEventListener('abort', abort);
    expired = true;
    if (worker) await worker.terminate();
  }
}
