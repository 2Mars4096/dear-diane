import { useRef, useState } from 'react';
import type { PdfSelectionRect } from './lib/paper-comments';
import styles from './interactive-pdf-viewer.module.css';

type Point = { x: number; y: number };
export function AreaSelection({ onSelect, onCancel }: { onSelect: (rect: PdfSelectionRect) => void; onCancel: () => void }) {
  const start = useRef<Point | null>(null);
  const [rect, setRect] = useState<PdfSelectionRect | null>(null);
  const position = (event: React.PointerEvent<HTMLDivElement>): Point => {
    const box = event.currentTarget.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)), y: Math.max(0, Math.min(1, (event.clientY - box.top) / box.height)) };
  };
  const rectangle = (point: Point): PdfSelectionRect => ({ left: Math.min(start.current!.x, point.x), top: Math.min(start.current!.y, point.y), width: Math.abs(start.current!.x - point.x), height: Math.abs(start.current!.y - point.y) });
  return <div className={styles.areaSelection} aria-label="Drag around an area to select it. Escape cancels." onPointerDown={event => {
    if (event.button !== 0 || !event.isPrimary) return;
    event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); start.current = position(event); setRect(null);
  }} onPointerMove={event => { if (start.current) setRect(rectangle(position(event))); }} onPointerUp={event => {
    if (!start.current) return;
    const next = rectangle(position(event)); start.current = null; setRect(null);
    event.currentTarget.releasePointerCapture(event.pointerId);
    const box = event.currentTarget.getBoundingClientRect();
    if (next.width * box.width >= 5 && next.height * box.height >= 5) onSelect(next);
  }} onPointerCancel={() => { start.current = null; setRect(null); }} onKeyDown={event => { if (event.key === 'Escape') onCancel(); }}>
    {rect && <div className={styles.areaRectangle} style={{ left: `${rect.left * 100}%`, top: `${rect.top * 100}%`, width: `${rect.width * 100}%`, height: `${rect.height * 100}%` }} />}
  </div>;
}
