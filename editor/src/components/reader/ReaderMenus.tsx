import { useState, type ReactNode } from 'react';
import { BookOpen, ChevronDown, MoreHorizontal, SlidersHorizontal } from 'lucide-react';
import { useDismissDetails } from '../workbench/useDismissDetails';

export function ReaderMenu({ label, children, more = false }: { label: string; children: ReactNode; more?: boolean }) {
  const ref = useDismissDetails();
  return <details ref={ref} className={`wb-reader-menu${more ? "" : " wb-reading-menu"}`} onKeyDown={event => {
    if (event.key === 'Escape' && ref.current) { ref.current.open = false; ref.current.querySelector('summary')?.focus(); }
  }}><summary aria-label={label} title={label}>{more ? <MoreHorizontal size={16} /> : <><SlidersHorizontal size={14} /><span>{label}</span><ChevronDown size={12} /></>}</summary><div className="wb-reader-menu-body">{children}</div></details>;
}

export function ReaderContents({ items, value, onSelect, placeholder = 'Contents' }: {
  items: { id: string; label: string }[]; value: string; onSelect: (id: string) => void; placeholder?: string;
}) {
  const ref = useDismissDetails();
  const [query, setQuery] = useState('');
  const current = items.find(item => item.id === value)?.label.trim();
  const filtered = items.filter(item => item.label.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  return <details ref={ref} className="wb-reader-menu wb-reader-contents" onKeyDown={event => {
    if (event.key === 'Escape' && ref.current) { ref.current.open = false; ref.current.querySelector('summary')?.focus(); }
  }}>
    <summary aria-label="Contents" title={current || placeholder}><BookOpen size={14} /><span>{current || placeholder}</span><ChevronDown size={12} /></summary>
    <div className="wb-reader-menu-body">
      <input type="search" aria-label="Search contents" placeholder="Find a chapter…" value={query} onChange={event => setQuery(event.target.value)} />
      <nav aria-label="Book contents">{filtered.map((item, index) => <button type="button" key={`${item.id}:${index}`} aria-current={item.id === value ? 'location' : undefined} onClick={() => { onSelect(item.id); if (ref.current) ref.current.open = false; }}>{item.label}</button>)}</nav>
      {!filtered.length && <p>{items.length ? 'No matching chapters' : 'No contents available'}</p>}
    </div>
  </details>;
}
