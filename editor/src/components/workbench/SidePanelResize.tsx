import { useLayoutEffect, useRef, useState } from 'react';
const KEY = 'dan.sidePanelWidth.v1';
export default function SidePanelResize() {
  const handle = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const [width, setWidth] = useState(() => { try { return Number(localStorage.getItem(KEY)) || 380; } catch { return 380; } });
  const [maximum, setMaximum] = useState(800);
  const minimum = Math.min(280, maximum);
  const displayed = Math.max(minimum, Math.min(maximum, width));
  useLayoutEffect(() => {
    const panel = handle.current?.parentElement;
    const main = panel?.parentElement?.querySelector<HTMLElement>('[data-main-drop-area]');
    if (!panel || !main) return;
    const resize = () => setMaximum(Math.max(160, panel.getBoundingClientRect().right - main.getBoundingClientRect().left - 320));
    const observer = new ResizeObserver(resize); observer.observe(main); observer.observe(panel.parentElement!); resize();
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => { const panel = handle.current?.parentElement; panel?.style.setProperty('--wb-side-width', `${displayed}px`); return () => { panel?.style.removeProperty('--wb-side-width'); }; }, [displayed]);
  const save = (value: number) => { const next = Math.max(minimum, Math.min(maximum, value)); setWidth(next); try { localStorage.setItem(KEY, String(next)); } catch { /* Keep resizing available. */ } };
  return <div ref={handle} className="wb-side-resize" role="separator" aria-label="Resize side pane" aria-orientation="vertical" aria-valuemin={minimum} aria-valuemax={maximum} aria-valuenow={displayed} tabIndex={0} title="Drag to resize. Double-click to reset."
    onPointerDown={event => { if (event.button !== 0) return; event.preventDefault(); drag.current = { x:event.clientX, width:displayed }; event.currentTarget.setPointerCapture(event.pointerId); }}
    onPointerMove={event => { if (drag.current) setWidth(Math.max(minimum, Math.min(maximum, drag.current.width + drag.current.x - event.clientX))); }}
    onPointerUp={event => { if (!drag.current) return; save(drag.current.width + drag.current.x - event.clientX); drag.current = null; event.currentTarget.releasePointerCapture(event.pointerId); }}
    onLostPointerCapture={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}
    onDoubleClick={() => save(380)} onKeyDown={event => { if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return; event.preventDefault(); save(event.key === 'Home' ? minimum : event.key === 'End' ? maximum : displayed + (event.key === 'ArrowLeft' ? 1 : -1) * (event.shiftKey ? 40 : 10)); }} />;
}
