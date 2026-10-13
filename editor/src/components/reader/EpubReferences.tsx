import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import type Book from 'epubjs/types/book';
import { EpubReferencePreview } from './EpubReferencePreview';
import { MAX_PAPER_REFERENCE_LINES, movePaperReferenceTag, type PaperReferenceTray, type PaperReferenceTag } from './lib/paper-reference-tags';
import styles from './interactive-pdf-viewer.module.css';

export function EpubReferences({ book, tray, onChange, onKeep, onGo, onPin, preview, onPreview, page }: {
  book: Book | null; tray: PaperReferenceTray; onChange: (tray: PaperReferenceTray) => void;
  onKeep: () => void; onGo: (cfi: string) => void; onPin: () => void;
  preview: string | null; onPreview: (id: string | null) => void; page: number;
}) {
  const help = useId();
  const [dragged, setDragged] = useState<string | null>(null);
  const [drop, setDrop] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const suppressClick = useRef(false);
  const cancelPeek = () => window.clearTimeout(timer.current);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const peek = (id: string, delay = 140) => { cancelPeek(); timer.current = window.setTimeout(() => onPreview(id), delay); };
  const closePeek = () => { cancelPeek(); timer.current = window.setTimeout(() => onPreview(null), 240); };
  const move = (id: string, line: string, before: string | null) => {
    if (!tray.tags.some(tag => tag.tagId === id)) return;
    onChange({ ...tray, tags: movePaperReferenceTag(tray.tags, id, line, before) }); setDrop(null); setDragged(null);
  };
  const keyboard = (event: KeyboardEvent, tag: PaperReferenceTag) => {
    if (!event.altKey || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault(); onPin();
    const tags = tray.tags.filter(item => item.lineId === tag.lineId), index = tags.findIndex(item => item.tagId === tag.tagId);
    const line = tray.lines.findIndex(item => item.lineId === tag.lineId);
    if (event.key === 'ArrowLeft' && index > 0) move(tag.tagId, tag.lineId, tags[index - 1].tagId);
    if (event.key === 'ArrowRight' && index < tags.length - 1) move(tag.tagId, tag.lineId, tags[index + 2]?.tagId || null);
    if (event.key === 'ArrowUp' && line > 0) move(tag.tagId, tray.lines[line - 1].lineId, null);
    if (event.key === 'ArrowDown' && line < tray.lines.length - 1) move(tag.tagId, tray.lines[line + 1].lineId, null);
  };
  const selected = tray.tags.find(tag => tag.tagId === preview);
  return <>
    <section className={styles.referenceTray} aria-label="Saved page references" style={{ maxHeight: 'min(360px,45vh)' }}>
      <header><strong>References</strong><div className={styles.referenceTrayActions}>
        <button disabled={tray.lines.length >= MAX_PAPER_REFERENCE_LINES} onClick={() => { onPin(); onChange({ ...tray, lines: [...tray.lines, { lineId: crypto.randomUUID(), label: `Line ${tray.lines.length + 1}` }] }); }}>+ Line</button>
        <button className={styles.keepReferenceButton} onClick={onKeep}>+ Keep p. {page}</button>
      </div></header>
      <div className={styles.referenceLines}>{tray.lines.map(line => {
        const tags = tray.tags.filter(tag => tag.lineId === line.lineId);
        return <section key={line.lineId} className={`${styles.referenceLine} ${drop === line.lineId ? styles.referenceLineDrop : ''}`}
          onDragOver={event => { if (!dragged) return; event.preventDefault(); event.dataTransfer.dropEffect = 'move'; setDrop(line.lineId); }}
          onDrop={event => { if (!dragged) return; event.preventDefault(); move(dragged, line.lineId, null); }}>
          <header><input aria-label="Reference line name" value={line.label} maxLength={40} onFocus={onPin} onChange={event => onChange({ ...tray, lines: tray.lines.map(item => item.lineId === line.lineId ? { ...item, label: event.target.value } : item) })} />
            {tray.lines.length > 1 && !tags.length && <button onClick={() => onChange({ ...tray, lines: tray.lines.filter(item => item.lineId !== line.lineId) })}>Remove</button>}
          </header>
          <div className={styles.referenceTags}>{tags.map(tag => <button key={tag.tagId} draggable aria-describedby={help} aria-grabbed={dragged === tag.tagId}
            className={`${dragged === tag.tagId ? styles.referenceTagDragging : ''} ${drop === tag.tagId ? styles.referenceTagDrop : ''}`}
            onMouseEnter={() => peek(tag.tagId)} onMouseLeave={closePeek} onFocus={() => peek(tag.tagId, 0)} onBlur={closePeek}
            onPointerDown={event => { if (event.pointerType === 'touch') { suppressClick.current = false; cancelPeek(); timer.current = window.setTimeout(() => { suppressClick.current = true; onPreview(tag.tagId); }, 420); } }}
            onPointerUp={cancelPeek} onPointerCancel={cancelPeek}
            onClick={() => { if (suppressClick.current) { suppressClick.current = false; return; } if (tag.selection?.epub) onGo(tag.selection.epub.cfi); }}
            onDragStart={event => { event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', tag.tagId); cancelPeek(); onPin(); onPreview(null); suppressClick.current = true; setDragged(tag.tagId); }}
            onDragEnd={() => { setDragged(null); setDrop(null); timer.current = window.setTimeout(() => { suppressClick.current = false; }, 0); }}
            onDragOver={event => { if (!dragged) return; event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = 'move'; setDrop(tag.tagId); }}
            onDrop={event => { if (!dragged) return; event.preventDefault(); event.stopPropagation(); move(dragged, line.lineId, tag.tagId); }}
            onKeyDown={event => keyboard(event, tag)}>
            <span title={tag.selection?.epub?.label}>ch. {tag.pageNumber}</span><strong>{tag.label}</strong>
          </button>)}{!tags.length && <span className={styles.referenceLineEmpty}>Drop tags here</span>}</div>
        </section>;
      })}</div>
      {!tray.tags.length && <p>Keep a page you expect to revisit.</p>}
      <small id={help}>Hover or hold to peek. Drag to arrange; Alt + arrows also move tags.</small>
    </section>
    {selected && <section className="wb-epub-reference-peek" aria-label="Reference preview" onMouseEnter={cancelPeek} onMouseLeave={closePeek} onFocus={cancelPeek} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) closePeek(); }}>
      <header><input aria-label="Reference label" value={selected.label} maxLength={80} onFocus={onPin} onChange={event => onChange({ ...tray, tags: tray.tags.map(tag => tag.tagId === selected.tagId ? { ...tag, label: event.target.value } : tag) })} />
        <button onClick={() => selected.selection?.epub && onGo(selected.selection.epub.cfi)}>Open</button><button onClick={() => { onChange({ ...tray, tags: tray.tags.filter(tag => tag.tagId !== selected.tagId) }); onPreview(null); }}>Remove</button>
      </header>
      <EpubReferencePreview book={book} selection={selected.selection} />
    </section>}
  </>;
}
