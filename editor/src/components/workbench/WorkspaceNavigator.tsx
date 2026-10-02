import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ChevronDown, Search, MoreHorizontal, Pin, Square, X } from 'lucide-react';
import { orderedWorkspaceItems, workspaceItems, type ItemGroup, type WorkspaceItem } from './workspaceItems';
import type { MainTab } from './mainTabState';
import type { DocumentFile } from '../documents/documents';
import type { SessionPreferences } from './sessionPreferences';
const STORAGE = 'dan.workspaceVisits.v1';
function readVisits(): string[] {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE) || 'null');
    if (Array.isArray(saved)) return [...new Set(saved.filter((key): key is string => typeof key === 'string'))].slice(0,100);
    const old = JSON.parse(localStorage.getItem('dan.recentSessions.v1') || '[]');
    return Array.isArray(old) ? old.filter(row => typeof row?.workflowId === 'string' && typeof row?.id === 'string').map(row => `${row.workflowId}:${row.id}`) : [];
  } catch { return []; }
}
export default function WorkspaceNavigator({ host, groups, projects = [], tabs, documents, dirty, active, query, archived, preferences, running, unread, onSelect, onMenu, onClose, onArchive, onStop, project = "*", onOpenProjects, onQuery, projectActions }: {
  projectActions?: ReactNode;
  project?: string; onOpenProjects?: () => void; onQuery?: (query: string) => void;
  projects?: { id: string; name: string }[]; host: HTMLElement | null; groups: ItemGroup[]; tabs: MainTab[]; documents: Record<string, DocumentFile>; dirty: Record<string, boolean>;
  active: string; query: string; archived: boolean; preferences: SessionPreferences; running: string[]; unread: string[];
  onSelect: (item: WorkspaceItem) => void; onMenu: (item: WorkspaceItem, x: number, y: number) => void;
  onClose: (id: string) => void; onArchive: (thread: NonNullable<WorkspaceItem['thread']>) => void; onStop: (thread: NonNullable<WorkspaceItem['thread']>) => void;
}) {
  const items = useMemo(() => workspaceItems(groups, tabs, documents, dirty, projects), [groups, tabs, documents, dirty, projects]);
  const baseline = useRef(new Map<string, number>());
  for (const item of items) if (!baseline.current.has(item.key)) baseline.current.set(item.key, item.timestamp);
  const [visits, setVisits] = useState(readVisits);
  const [showAllReading, setShowAllReading] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [frozen, setFrozen] = useState<{ choices: string[]; order: string[] } | null>(null);
  const frozenRef = useRef<typeof frozen>(null);
  const lastActive = useRef('');
  const swipe = useRef<{ key: string; x: number; y: number } | null>(null);
  const suppressClick = useRef('');
  useEffect(() => {
    if (!active || active === lastActive.current || !items.some(item => item.key === active && !item.archived)) return;
    lastActive.current = active;
    const next = [active, ...readVisits().filter(key => key !== active)].slice(0,100);
    setVisits(next); try { localStorage.setItem(STORAGE, JSON.stringify(next)); } catch { /* Navigation remains available without persistent storage. */ }
  }, [active, items]);
  useEffect(() => {
    const sync = (event: StorageEvent) => { if (event.key === STORAGE) setVisits(readVisits()); };
    window.addEventListener('storage', sync); return () => window.removeEventListener('storage', sync);
  }, []);
  const activeItems = items.filter(item => !item.archived);
  const recency = orderedWorkspaceItems(activeItems, visits, {}, baseline.current);
  const choices = recency.filter(item => item.key !== (active || lastActive.current)).slice(0,9).map(item => item.key);
  const ordered = orderedWorkspaceItems(items, visits, preferences, baseline.current);
  const latest = useRef({ choices, ordered, items, onSelect }); latest.current = { choices, ordered, items, onSelect };
  useEffect(() => {
    const mac = /Mac|iPhone|iPad/.test(navigator.platform);
    const held = (event: KeyboardEvent) => mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
    const release = () => { frozenRef.current = null; setFrozen(null); };
    const freeze = () => {
      if (!frozenRef.current) { frozenRef.current = { choices: [...latest.current.choices], order: latest.current.ordered.map(item => item.key) }; setFrozen(frozenRef.current); }
      return frozenRef.current;
    };
    const down = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.altKey || event.shiftKey || document.querySelector('dialog[open], [role=dialog][aria-modal=true], .wb-session-menu, [popover]:popover-open')) return;
      if (event.key === (mac ? 'Meta' : 'Control')) { freeze(); return; }
      if (!held(event)) return;
      const digit = /^(?:Digit|Numpad)[1-9]$/.test(event.code) ? event.code.slice(-1) : event.key;
      if (!/^[1-9]$/.test(digit) || event.repeat) return;
      const key = freeze().choices[Number(digit) - 1];
      const item = latest.current.items.find(item => item.key === key && !item.archived);
      if (item) { event.preventDefault(); latest.current.onSelect(item); }
    };
    const up = (event: KeyboardEvent) => { if (!held(event)) release(); };
    window.addEventListener('keydown', down, true); window.addEventListener('keyup', up); window.addEventListener('blur', release);
    return () => { window.removeEventListener('keydown', down, true); window.removeEventListener('keyup', up); window.removeEventListener('blur', release); };
  }, []);
  const projectKey = (item: WorkspaceItem) => item.projectId || `context:${item.project}`;
  if (!host) return null;
  const text = query.trim().toLowerCase();
  const order = frozen ? [...ordered].sort((a,b) => {
    const rank = (key: string) => { const index = frozen.order.indexOf(key); return index < 0 ? Infinity : index; };
    return rank(a.key) - rank(b.key);
  }) : ordered;
  const reading = recency.filter(item => item.kind === 'pdf' || item.kind === 'file');
  const visible = order.filter(item => item.kind !== 'pdf' && item.kind !== 'file' && Boolean(item.archived) === archived && (project === '*' || projectKey(item) === project) && (!text || `${item.title} ${item.project} ${item.path || ''} ${item.key}`.toLowerCase().includes(text)));
  const modifier = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl+';
  return createPortal(<section className="wb-workspace-items" aria-label={archived ? 'Archived items' : 'Workspace items'}>
    {!archived && reading.length > 0 && <section className="wb-reading-list" aria-label="Reading">
      <header><span>Reading</span>{reading.length > 5 && <button onClick={() => setShowAllReading(!showAllReading)} aria-expanded={showAllReading}>{showAllReading ? 'Show less' : `Show all (${reading.length})`}</button>}</header>
      {(showAllReading ? reading : reading.slice(0, 5)).map(item => <div key={item.key} className="wb-item-row" data-active={item.key === active || undefined} data-item-key={item.key}>
        <button className="wb-item-select" title={item.path || item.title} aria-current={item.key === active ? 'page' : undefined} onClick={() => onSelect(item)}><span className="wb-item-title">{item.title}</span>{item.dirty && <span className="wb-item-state">Unsaved</span>}</button>
        <button className="wb-item-action" aria-label={`Close ${item.title}`} onClick={() => item.tabId && onClose(item.tabId)}><X size={13} /></button>
      </div>)}
    </section>}
    <div className="wb-items-toolbar"><button className="wb-project-filter" onClick={onOpenProjects} aria-label="Filter items by project" aria-haspopup="dialog" title={`Select project (${modifier}⇧E)`}><span>{project === '*' ? 'All projects' : projects.find(item => item.id === project)?.name || 'Project'}</span><ChevronDown size={13} /></button><div className="wb-items-project-actions">{projectActions}<button className="wb-item-action" aria-label="Search workspace items" aria-expanded={searchOpen || Boolean(query)} onClick={() => { setSearchOpen(!searchOpen); if (searchOpen) onQuery?.(''); }}><Search size={14} /></button></div></div>
    {(searchOpen || query) && <label className="wb-shelf-search"><Search size={15} /><input autoFocus aria-label="Search workspace items" placeholder="Search sessions, files, projects" value={query} onChange={event => onQuery?.(event.target.value)} onKeyDown={event => { if (event.key === 'Escape') { onQuery?.(''); setSearchOpen(false); } }} /></label>}
    <div className="wb-items-list">
      {visible.map(item => {
        const number = frozen ? frozen.choices.indexOf(item.key) + 1 : 0;
        const isRunning = Boolean(item.thread && running.includes(item.thread.id));
        const isUnread = Boolean(preferences[item.key]?.unread || (item.thread && unread.includes(item.thread.id)));
        const type = item.kind === 'session' ? 'Session' : item.kind === 'pdf' ? 'PDF' : item.kind === 'file' ? 'File' : item.kind === 'papers' ? 'Papers' : 'Settings';
        return <div key={item.key} className="wb-item-row" data-active={item.key === active || undefined} data-item-key={item.key}
          onPointerDown={event => { if (item.thread && event.pointerType !== 'mouse' && !(event.target as HTMLElement).closest('.wb-item-action')) swipe.current = { key: item.key, x:event.clientX, y:event.clientY }; }}
          onPointerCancel={() => { swipe.current = null; }}
          onPointerUp={event => { const start=swipe.current; swipe.current=null; if (!start || start.key !== item.key || !item.thread || Math.abs(event.clientY-start.y)>30) return; const dx=event.clientX-start.x; if (item.archived ? dx>56 : dx < -56) { suppressClick.current=item.key; onArchive(item.thread); } }}
          onContextMenu={event => { if (item.thread) { event.preventDefault(); onMenu(item,event.clientX,event.clientY); } }}>
          <button type="button" className="wb-item-select" aria-current={item.key === active ? 'page' : undefined} aria-label={`${item.title}, ${item.project}, ${type}`} title={`${item.title}\n${item.project}${item.path ? '\n'+item.path : ''}`} onClick={() => { if (suppressClick.current === item.key) { suppressClick.current=''; return; } onSelect(item); }}>
            <span className="wb-item-title">{item.title}</span>{(isRunning || item.dirty || isUnread) && <span className="wb-item-state">{isRunning ? 'Working' : item.dirty ? 'Unsaved' : 'Unread'}</span>}
            {preferences[item.key]?.pinned && <Pin size={12} aria-label="Pinned" />}
            {number > 0 && <kbd className="wb-session-shortcut" aria-label={`Shortcut ${modifier}${number}`}>{modifier}{number}</kbd>}
          </button>
          {isRunning && item.thread && <button type="button" className="wb-item-action" aria-label={`Stop ${item.title}`} onClick={() => onStop(item.thread!)}><Square size={12} /></button>}
          {item.thread ? <button type="button" className="wb-item-action" aria-label={`Actions for ${item.title}`} aria-haspopup="menu" onClick={event => { const rect=event.currentTarget.getBoundingClientRect(); onMenu(item,rect.right,rect.bottom); }}><MoreHorizontal size={15} /></button> : <button type="button" className="wb-item-action" aria-label={`Close ${item.title}`} onClick={() => item.tabId && onClose(item.tabId)}><X size={13} /></button>}
        </div>;
      })}
      {!visible.length && <p className="wb-items-empty">{text || project !== '*' ? 'No matching items' : archived ? 'No archived sessions' : 'Start a chat or open a file.'}</p>}
    </div>
  </section>, host);
}
