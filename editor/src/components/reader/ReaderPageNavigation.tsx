import { useEffect, useRef, useState } from 'react';

/** Direct page entry plus a hover/focus scrubber. Drag previews; release commits once. */
export function ReaderPageNavigation({ page, total, onGo, previous, next, canPrevious, canNext, loading = false }: {
  page: number; total: number; onGo: (page: number) => void;
  previous: () => void; next: () => void; canPrevious: boolean; canNext: boolean; loading?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<number | null>(null);
  const scrubbing = useRef(false);
  const closeTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(closeTimer.current), []);
  const show = () => { window.clearTimeout(closeTimer.current); setOpen(true); };
  const hide = () => { if (scrubbing.current) return; window.clearTimeout(closeTimer.current); closeTimer.current = window.setTimeout(() => { setOpen(false); setPreview(null); }, 240); };
  const navigate = (value: string) => { const target = Number(value); if (Number.isInteger(target) && target >= 1 && target <= total && target !== page) onGo(target); };
  return <>
    <button aria-label="Previous page" disabled={!canPrevious} onClick={previous}>←</button>
    <div className="wb-reader-page-navigation" onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) hide(); }} onKeyDown={event => { if (event.key === 'Escape') { setOpen(false); setPreview(null); } }}>
      <input aria-label="Page number" key={`${page}:${total}`} type="number" inputMode="numeric" min={1} max={total || 1} disabled={!total || loading} defaultValue={page || ''} style={{ width: `${Math.max(3, String(total).length, String(page).length) + 3}ch` }} onKeyDown={event => { if (event.key === 'Enter') navigate(event.currentTarget.value); }} onBlur={event => { navigate(event.currentTarget.value); event.currentTarget.value = String(page || ''); }} />
      <span aria-live="polite" aria-busy={loading} title={loading ? 'Calculating whole-book pages…' : total ? 'Total pages' : 'Page count unavailable'}>/ {total || (loading ? 'Counting…' : 'Unavailable')}</span>
      {open && total > 0 && !loading && <div className="wb-reader-page-scrubber" onMouseEnter={show} onMouseLeave={hide}>
        <label>Page {preview ?? page} / {total}<input aria-label="Navigate pages" type="range" min={1} max={total} value={preview ?? page} onChange={event => setPreview(Number(event.target.value))} onPointerDown={event => { scrubbing.current = true; event.currentTarget.setPointerCapture(event.pointerId); }} onPointerUp={event => { scrubbing.current = false; navigate(event.currentTarget.value); setPreview(null); }} onPointerCancel={() => { scrubbing.current = false; setPreview(null); }} onKeyUp={event => { if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(event.key)) { navigate(event.currentTarget.value); setPreview(null); } }} /></label>
        <div><span>1</span><span>{total}</span></div>
      </div>}
    </div>
    <button aria-label="Next page" disabled={!canNext} onClick={next}>→</button>
  </>;
}
