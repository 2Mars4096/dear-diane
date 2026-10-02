import { useLayoutEffect, useRef, useState } from 'react';
const KEY = 'dan.sidebarWidth.v1';
const DEFAULT = 260;
export default function SidebarResize() {
  const handle = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const [width, setWidth] = useState(() => {
    try { const value = Number(localStorage.getItem(KEY)); return Number.isFinite(value) && value >= 200 ? Math.min(560, value) : DEFAULT; } catch { return DEFAULT; }
  });
  const [viewport, setViewport] = useState(window.innerWidth);
  const maximum = Math.min(560, Math.floor(viewport * .45));
  const clamp = (value: number) => Math.max(200, Math.min(maximum, value));
  const displayed = clamp(width);
  useLayoutEffect(() => {
    const resize = () => setViewport(window.innerWidth);
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);
  useLayoutEffect(() => {
    const parent = handle.current?.parentElement;
    parent?.style.setProperty('--wb-sidebar-width', `${displayed}px`);
    return () => { parent?.style.removeProperty('--wb-sidebar-width'); };
  }, [displayed]);
  const save = (value: number) => { const next = clamp(value); setWidth(next); try { localStorage.setItem(KEY, String(next)); } catch { /* Resizing still works without storage. */ } };
  return <div ref={handle} className="wb-sidebar-resize" role="separator" aria-label="Resize sidebar" aria-orientation="vertical" aria-valuemin={200} aria-valuemax={maximum} aria-valuenow={displayed} tabIndex={0} title="Drag to resize sidebar. Double-click to reset."
    onPointerDown={event => { if (event.button !== 0) return; event.preventDefault(); drag.current = { x:event.clientX, width:displayed }; event.currentTarget.setPointerCapture(event.pointerId); }}
    onPointerMove={event => { if (drag.current) setWidth(clamp(drag.current.width + event.clientX - drag.current.x)); }}
    onPointerUp={event => { if (!drag.current) return; save(drag.current.width + event.clientX - drag.current.x); drag.current = null; event.currentTarget.releasePointerCapture(event.pointerId); }}
    onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}
    onDoubleClick={() => save(DEFAULT)}
    onKeyDown={event => { if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return; event.preventDefault(); save(event.key === 'Home' ? 200 : event.key === 'End' ? maximum : displayed + (event.key === 'ArrowRight' ? 1 : -1) * (event.shiftKey ? 40 : 10)); }} />;
}
