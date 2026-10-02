import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

export type SessionAction = 'log' | 'tab' | 'window' | 'pin' | 'read' | 'rename' | 'move' | 'fork' | 'id' | 'copy' | 'archive' | 'delete';
export default function SessionMenu({ x, y, session, title, archived, pinned, unread, running, projects, onAction, onClose }: {
  session: { id: string; workflow_id: string }; x: number; y: number; title: string; archived: boolean; pinned: boolean; unread: boolean; running: boolean;
  projects: { id: string; name: string }[]; onAction: (action: SessionAction, value?: string) => Promise<void>; onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const opener = useRef(document.activeElement as HTMLElement | null);
  const [edit, setEdit] = useState<'rename' | 'move' | null>(null);
  const [value, setValue] = useState(title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useLayoutEffect(() => {
    const node = ref.current!;
    node.style.left = `${Math.max(8, Math.min(x, innerWidth - node.offsetWidth - 8))}px`;
    node.style.top = `${Math.max(8, Math.min(y, innerHeight - node.offsetHeight - 8))}px`;
    node.querySelector<HTMLElement>('input, button')?.focus();
  }, [x, y, edit]);
  useEffect(() => {
    const previous = opener.current;
    const outside = (event: PointerEvent) => { if (!ref.current?.contains(event.target as Node)) onClose(); };
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); }
      if (!edit && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const items = [...ref.current!.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
        const index = items.indexOf(document.activeElement as HTMLButtonElement);
        items[event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus();
      }
      if (event.key === 'Tab' && !edit) onClose();
    };
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', key);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', key); if (previous?.isConnected) previous.focus(); };
  }, [edit, onClose]);
  const run = async (action: SessionAction, argument?: string) => {
    setBusy(true); setError('');
    try {
      if (action === 'window') {
        const url = new URL(location.href); url.search = ''; url.hash = '';
        url.searchParams.set('session', session.id); url.searchParams.set('workflow', session.workflow_id);
        const opened = window.open(url.href, '_blank', 'popup,width=1200,height=850');
        if (!opened && !window.electronAPI) throw Error('Allow popups to open a session window.');
      } else await onAction(action, argument);
      onClose();
    }
    catch (e) { setError(e instanceof Error ? e.message : 'Action failed. Try again.'); setBusy(false); }
  };
  const item = (action: SessionAction, label: string, disabled = false) => <button type="button" role="menuitem" className={action === 'delete' ? 'wb-session-delete' : undefined} title={action === 'delete' && running ? 'Stop this session before deleting it' : undefined} disabled={busy || disabled} onClick={() => void run(action)}>{label}</button>;
  return createPortal(<div ref={ref} className="wb-session-menu" role={edit ? 'dialog' : 'menu'} aria-label="Session actions">
    {edit === 'rename' ? <form onSubmit={event => { event.preventDefault(); if (value.trim()) void run('rename', value.trim()); }}>
      <label>Session name<input autoFocus value={value} maxLength={200} onChange={event => setValue(event.target.value)} /></label>
      <button type="submit" disabled={busy || !value.trim()}>Save</button><button type="button" onClick={() => setEdit(null)}>Back</button>
    </form> : edit === 'move' ? <><div className="wb-session-menu-label">Move to project</div>{projects.map(project => <button key={project.id} disabled={busy} onClick={() => void run('move', project.id)}>{project.name}</button>)}<button onClick={() => setEdit(null)}>Back</button></> : <>
      {!archived && <>{item('tab', 'Open in workspace')}{item('window', 'Open in new window')}<hr />{item('pin', pinned ? 'Unpin' : 'Pin')}{item('read', unread ? 'Mark as read' : 'Mark as unread')}</>}
      <button role="menuitem" onClick={() => setEdit('rename')}>Rename</button>
      {!archived && <>{item('fork', 'Fork conversation', running)}<button role="menuitem" onClick={() => setEdit('move')}>Move to project…</button></>}
      <hr />{!archived && item('log', 'View prompt log')}{item('id', 'Copy session ID')}{item('copy', 'Copy conversation')}<hr />{item('archive', archived ? 'Restore' : 'Archive')}{item('delete', 'Delete permanently…', running)}
    </>}
    {error && <p role="alert">{error}</p>}
  </div>, document.querySelector(".wb-shell") || document.body);
}
