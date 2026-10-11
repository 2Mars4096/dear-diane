/** EPUB.js can resolve display() without any views when its host is hidden. */
export function waitForReaderSize(host: HTMLElement, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const observer = new ResizeObserver(check);
    function finish(error?: unknown) {
      observer.disconnect();
      signal.removeEventListener('abort', abort);
      if (error) reject(error); else resolve();
    }
    function abort() { finish(new DOMException('Reader opening cancelled', 'AbortError')); }
    function check() {
      const box = host.getBoundingClientRect();
      if (box.width > 0 && box.height > 0) finish();
    }
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort', abort, { once: true });
    observer.observe(host);
    check();
  });
}
